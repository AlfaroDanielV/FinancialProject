"""aiogram handlers — the thin layer between Telegram updates and the
channel-agnostic `process_message` / `handle_pending_callback` pipeline.

Each handler:
  1. Opens a DB session (AsyncSessionLocal).
  2. Resolves the user via telegram_user_id (or handles /start for pairing).
  3. Delegates to `bot.pipeline`.
  4. Converts the returned BotReply into Telegram messages (chunked +
     sanitized via `bot.delivery_send`).

Keep logic out of here. If you find yourself writing an `if` beyond
pairing or routing, put it in pipeline.py instead — this file stays thin
so the _simulate endpoint can drive the same code path faithfully.
"""
from __future__ import annotations

import asyncio
import io
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal
from api.models.user import User
from api.redis_client import get_redis

from app.queries.history import clear_history

from . import messages_es
from .account_creation import clear_account_creation
from .app import get_bot, get_llm_client
from .delivery_send import send_chunked
from .onboarding_welcome import build_onboarding_reply
from .pairing import consume_pairing_code, resolve_pairing_code
from .registration import (
    begin_registration,
    clear_registration,
    handle_registration_text,
    load_registration,
)
from .clarification import clear_clarification
from .pending import clear_pending, load_pending
from .pending_db import resolve_from_pending
from .pipeline import (
    BotReply,
    handle_clarify_callback,
    handle_nudge_callback,
    handle_pending_callback,
    handle_statement_document,
    process_message,
)
from .user_resolver import bind_telegram_id, user_by_telegram_id
from api.config import settings


# Refresh cadence for the typing indicator. Telegram expires the action
# at 5s; 4s leaves comfortable headroom for jitter without flooding the
# Bot API. See docs/phase-6a-decisions.md (2026-04-29 entry).
TYPING_REFRESH_INTERVAL_S = 4.0


log = logging.getLogger("bot.handlers")


router = Router(name="phase5b")


def register(dp: Dispatcher) -> None:
    # Gmail onboarding router (Phase 6b) registered FIRST so its
    # state-aware text handler (F.text & _is_selecting_banks) gets
    # first shot at evaluating an incoming message. If we register the
    # main router first, its catch-all
    # `@router.message(F.text & ~F.text.startswith("/"))` consumes
    # everything (including `notif@bac.cr` while the user is in the
    # bank-selection flow) before the gmail filter runs.
    #
    # Slash commands like /undo, /help, /cancel still resolve correctly
    # because the gmail router doesn't declare them — they fall through
    # to the main router below.
    #
    # Phase 6c B7 memory router slots between gmail and main: same
    # rationale (its `_is_in_memory_edit` filter must beat the catch-all
    # so /editar_memoria replies don't get extracted as transactions),
    # but the gmail bank-selection flow has higher priority since it's
    # an active onboarding state with a 30-min TTL.
    from . import gmail_handlers, memory_handlers, onboarding_handlers

    dp.include_router(gmail_handlers.router)
    dp.include_router(memory_handlers.router)
    dp.include_router(onboarding_handlers.router)
    dp.include_router(router)


# ── helpers ───────────────────────────────────────────────────────────────────


def _kb(reply: BotReply) -> Optional[InlineKeyboardMarkup]:
    if not reply.buttons and not reply.url_buttons:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(
        [InlineKeyboardButton(text=b.label, callback_data=b.callback_data)]
        for b in reply.buttons
    )
    rows.extend(
        [InlineKeyboardButton(text=b.label, url=b.url)]
        for b in reply.url_buttons
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send(message: Message, reply: BotReply) -> None:
    """Send `reply` to Telegram, chunked + sanitized for HTML safety.

    Buttons attach to the LAST chunk. Most replies fit in a single chunk
    (cap is 3900 chars); the splitter is a no-op there.
    """
    await send_chunked(message, reply.text, reply_markup=_kb(reply))


# ── typing indicator ──────────────────────────────────────────────────────────


async def _typing_loop(bot: Bot, chat_id: int) -> None:
    """Send `typing` every TYPING_REFRESH_INTERVAL_S until cancelled.

    Network failures are swallowed (the indicator is best-effort UX, not
    correctness) but `CancelledError` propagates so the task exits
    cleanly when the context manager finishes.
    """
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("typing send_chat_action failed", exc_info=True)
        await asyncio.sleep(TYPING_REFRESH_INTERVAL_S)


@asynccontextmanager
async def typing_action(bot: Bot, chat_id: int) -> AsyncIterator[None]:
    """Background task that keeps the `typing` indicator alive.

    Fires the first send_chat_action immediately, then refreshes every
    TYPING_REFRESH_INTERVAL_S seconds. On exit (success or exception)
    the task is cancelled and awaited so we don't leak.
    """
    task = asyncio.create_task(_typing_loop(bot, chat_id))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ── /start ────────────────────────────────────────────────────────────────────


@router.message(Command("start"))
async def on_start(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    tg_id = message.from_user.id
    first_name = message.from_user.first_name or ""

    async with AsyncSessionLocal() as db:
        existing = await user_by_telegram_id(telegram_user_id=tg_id, db=db)
        if existing is not None and not command.args:
            reply = await build_onboarding_reply(
                user=existing,
                db=db,
                first_name=first_name,
                include_setup_link=True,
            )
            await message.answer(reply.text)
            return

        if not command.args:
            # Phase 8 B1 — unknown Telegram account + bare /start: cold-start
            # registration right here (Telegram already authenticates the
            # telegram_user_id; pairing codes are only for linking a
            # pre-existing user row).
            await message.answer(
                await begin_registration(
                    telegram_user_id=tg_id, redis=get_redis()
                )
            )
            return

        code = command.args.strip().upper()
        redis = get_redis()
        candidate = await resolve_pairing_code(code=code, redis=redis, db=db)
        if candidate is None:
            await message.answer(messages_es.PAIR_BAD_CODE)
            return

        # Guard: this Telegram account is already bound to a different user.
        if existing is not None and existing.id != candidate.id:
            await message.answer(messages_es.PAIR_TG_ACCOUNT_TAKEN)
            return

        # Guard: this candidate user is already paired to a different TG.
        if (
            candidate.telegram_user_id is not None
            and candidate.telegram_user_id != tg_id
        ):
            await message.answer(messages_es.PAIR_USER_ALREADY_PAIRED)
            return

        await bind_telegram_id(user=candidate, telegram_user_id=tg_id, db=db)
        await consume_pairing_code(code=code, redis=redis)
        reply = await build_onboarding_reply(
            user=candidate,
            db=db,
            first_name=first_name,
            include_setup_link=True,
            paired_now=True,
        )
        await message.answer(reply.text)


# ── /help, /whoami, /cancel, /undo ────────────────────────────────────────────


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        reply = await build_onboarding_reply(user=user, db=db)
    await message.answer(reply.text)


@router.message(Command("menu"))
async def on_menu(message: Message) -> None:
    from bot.menu import build_menu_text

    await message.answer(build_menu_text())


@router.message(Command("resumen", "resumen_mes", "resumen_semana", "resumen_hoy"))
async def on_resumen(message: Message) -> None:
    if message.from_user is None:
        return
    from bot.menu import handle_resumen

    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        reply = await handle_resumen(message.text or "/resumen", user=user, db=db)
    await message.answer(reply.text)


@router.message(Command("gmail", "correos", "correo"))
async def on_gmail(message: Message) -> None:
    await message.answer(
        "En la app, /gmail abre la pantalla de Gmail para conectar tu correo.\n"
        "Por acá usá: /conectar_gmail · /estado_gmail · /revisar_correos"
    )


@router.message(Command("login", "iniciar"))
async def on_login(message: Message) -> None:
    """Phase 6f B3 — mint a 6-character device-login code.

    Reply to the same chat that issued the command (Telegram sends the
    code only to the paired Telegram account). The native app exchanges
    the code for a JWT via `POST /api/v1/auth/device-code/exchange`.
    """
    if message.from_user is None:
        return
    from api.services.auth.device_code import request_device_code
    from bot.deep_link import mint_native_deep_link

    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        code = await request_device_code(user=user, redis=get_redis())
        # Phase 6f B15 — additive tap-to-open path. The 6-char code stays the
        # primary instruction; if minting the deep link fails we just omit it.
        deep_link = await mint_native_deep_link(db, user_id=user.id)
    reply = messages_es.LOGIN_CODE_REPLY.format(code=code)
    if deep_link:
        reply += messages_es.LOGIN_DEEP_LINK_SUFFIX.format(deep_link=deep_link)
    await message.answer(reply)


@router.message(Command("cancel"))
async def on_cancel(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            # Phase 8 B1 — /cancel aborts an in-flight registration.
            redis = get_redis()
            tg_id = message.from_user.id
            if await load_registration(telegram_user_id=tg_id, redis=redis):
                await clear_registration(telegram_user_id=tg_id, redis=redis)
                await message.answer(messages_es.REGISTER_CANCELLED)
            else:
                await message.answer(messages_es.PAIR_PROMPT)
            return
        redis = get_redis()
        # Phase 5d: close the DB audit row before we drop the Redis key.
        existing = await load_pending(user_id=user.id, redis=redis)
        if existing is not None:
            await resolve_from_pending(
                session=db, pending=existing, resolution="cancelled"
            )
            await db.commit()
        await clear_pending(user_id=user.id, redis=redis)
        await clear_clarification(user_id=user.id, redis=redis)
        await clear_account_creation(user_id=user.id, redis=redis)
        # Phase 6c B7: also clear an in-progress /editar_memoria reply.
        from .memory_handlers import clear_memory_edit_state
        await clear_memory_edit_state(user_id=user.id, redis=redis)
    await message.answer(messages_es.CANCELLED)


@router.message(Command("whoami"))
async def on_whoami(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        from api.services.accounts import list_active
        accounts = await list_active(user, db)
        default_name = accounts[0].name if len(accounts) == 1 else "(varias)"
        await message.answer(
            messages_es.WHO_AM_I.format(
                email=user.email, default_account=default_name
            )
        )


@router.message(Command("clear"))
async def on_clear(message: Message) -> None:
    """Wipe the user's query conversation history. Idempotent.

    Touches NO write-state — pending proposals (telegram:pending:*) and
    clarification round-trips (telegram:clarify:*) are owned by /cancel.
    """
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        await clear_history(user.id, redis=get_redis())
    await message.answer(messages_es.CONTEXT_CLEARED)


@router.message(Command("undo"))
async def on_undo(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        from .undo import run_undo
        _ok, msg = await run_undo(user=user, db=db, redis=get_redis())
    await message.answer(msg)


# ── free text ─────────────────────────────────────────────────────────────────
#
# Excluimos slash commands del catch-all: texto libre va al pipeline de
# extracción, pero `/algo` debe seguir buscándose hasta el router que lo
# declare (e.g. /conectar_gmail vive en bot/gmail_handlers.py). Si no
# excluís el "/", aiogram matchea acá primero y el comando nunca llega.


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    if message.from_user is None or not message.text:
        return
    bot = get_bot()
    async with typing_action(bot, message.chat.id):
        async with AsyncSessionLocal() as db:
            user = await user_by_telegram_id(
                telegram_user_id=message.from_user.id, db=db
            )
            if user is None:
                # Phase 8 B1 — an in-flight registration consumes the text
                # (email / nombre / confirmación) before the pair fallback.
                reg_reply = await handle_registration_text(
                    telegram_user_id=message.from_user.id,
                    text=message.text,
                    db=db,
                    redis=get_redis(),
                )
                await message.answer(reg_reply or messages_es.PAIR_PROMPT)
                return
            reply = await process_message(
                user=user,
                text=message.text,
                db=db,
                redis=get_redis(),
                llm_client=get_llm_client(),
                llm_model=settings.llm_extraction_model,
            )
        await _send(message, reply)


# ── document (bank statement PDF → reconciliation) ────────────────────────────


@router.message(F.document)
async def on_document(message: Message) -> None:
    """A user sends a bank-statement PDF → we read the saldo al corte of each
    account and propose reconciling them (Sí/No). Native users use the in-app
    form instead; this is the Telegram/WhatsApp path."""
    if message.from_user is None or message.document is None:
        return
    doc = message.document
    if (doc.mime_type or "") != "application/pdf":
        await message.answer(messages_es.STATEMENT_NOT_PDF)
        return
    if (doc.file_size or 0) > 4 * 1024 * 1024:
        await message.answer(messages_es.STATEMENT_TOO_BIG)
        return
    bot = get_bot()
    async with typing_action(bot, message.chat.id):
        async with AsyncSessionLocal() as db:
            user = await user_by_telegram_id(
                telegram_user_id=message.from_user.id, db=db
            )
            if user is None:
                await message.answer(messages_es.PAIR_PROMPT)
                return
            buf = io.BytesIO()
            await bot.download(doc, destination=buf)
            reply = await handle_statement_document(
                user=user,
                pdf_bytes=buf.getvalue(),
                db=db,
                redis=get_redis(),
                llm_client=get_llm_client(),
                haiku_model=settings.llm_extraction_model,
                sonnet_model=settings.llm_query_model,
            )
        await _send(message, reply)


# ── inline-keyboard callbacks ─────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pending:"))
async def on_pending_callback(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return
        reply = await handle_pending_callback(
            user=user, callback_data=cb.data, db=db, redis=get_redis()
        )
    if cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover - best effort
            pass
        await cb.message.answer(reply.text)
    await cb.answer()


@router.callback_query(F.data.startswith("clarify:"))
async def on_clarify_callback(cb: CallbackQuery) -> None:
    """Phase 7f — user tapped a clarification option button (account name).

    The follow-up reply can itself carry buttons (the proposal's Sí/No/
    Editar), so it goes through `_send` with its keyboard — unlike the
    pending/nudge callbacks whose replies are always plain text."""
    if cb.from_user is None or cb.data is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return
        reply = await handle_clarify_callback(
            user=user, callback_data=cb.data, db=db, redis=get_redis()
        )
    if cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover - best effort
            pass
        await _send(cb.message, reply)
    await cb.answer()


@router.callback_query(F.data.startswith("nudge:"))
async def on_nudge_callback(cb: CallbackQuery) -> None:
    """Phase 5d — user tapped act/dismiss/later on a nudge card."""
    if cb.from_user is None or cb.data is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return
        reply = await handle_nudge_callback(
            user=user, callback_data=cb.data, db=db, redis=get_redis()
        )
    if cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover - best effort
            pass
        await cb.message.answer(reply.text)
    await cb.answer()
