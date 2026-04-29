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
from .app import get_bot, get_llm_client
from .delivery_send import send_chunked
from .pairing import consume_pairing_code, resolve_pairing_code
from .clarification import clear_clarification
from .pending import clear_pending, load_pending
from .pending_db import resolve_from_pending
from .pipeline import (
    BotReply,
    handle_nudge_callback,
    handle_pending_callback,
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
    dp.include_router(router)


# ── helpers ───────────────────────────────────────────────────────────────────


def _kb(reply: BotReply) -> Optional[InlineKeyboardMarkup]:
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b.label, callback_data=b.callback_data)]
            for b in reply.buttons
        ]
    )


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
            # Already paired — greet and show capabilities.
            await message.answer(
                messages_es.PAIR_SUCCESS.format(name=first_name)
                + "\n\n"
                + messages_es.HELP_TEXT
            )
            return

        if not command.args:
            await message.answer(messages_es.PAIR_PROMPT)
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
        await message.answer(
            messages_es.PAIR_SUCCESS.format(name=first_name)
        )


# ── /help, /whoami, /cancel, /undo ────────────────────────────────────────────


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(messages_es.HELP_TEXT)


@router.message(Command("cancel"))
async def on_cancel(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
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


@router.message(F.text)
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
                await message.answer(messages_es.PAIR_PROMPT)
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
