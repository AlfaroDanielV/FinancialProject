"""Telegram handlers for the Gmail onboarding flow (Phase 6b addenda).

Routes registered on `gmail_router`:

    /conectar_gmail    → mint OAuth URL, send inline button + warning
                         about Google's "App not verified" screen.
    /desconectar_gmail → confirm + revoke (KV delete + DB flag).
    /estado_gmail      → connected? activated? full whitelist?
    /agregar_banco     → enter selecting_banks fresh post-activation.
    /quitar_banco      → list active senders, soft-delete on tap.
    /agregar_muestra   → placeholder until Block D wires the optional
                         sample analyzer.

Multi-bank selection flow (during onboarding AND /agregar_banco):

    bank_preset:{bank}   → tap added; updates the live keyboard message.
    bank_custom (text)   → user typed an email; validated, inferred,
                            added to pending_senders.
    bank_done            → renders the confirm prompt.
    bank_done_addonly    → /agregar_banco shortcut: skip activation,
                            just commit the new senders.
    bank_cancel          → drop state.
    bank_confirm:activate → first-time activation: flip activated_at,
                            persist whitelist, kick backfill (B.4 — Block B).
    bank_confirm:edit    → back to selecting_banks.
    bank_confirm:cancel  → drop state.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.data.bank_senders_cr import (
    KNOWN_BANK_SENDERS_CR,
    infer_bank_from_email,
    preset_senders_for,
)
from api.database import AsyncSessionLocal
from api.models.gmail_credential import GmailCredential
from api.models.user import User
from api.redis_client import get_redis
from api.services.gmail import oauth as oauth_svc
from api.services.gmail import whitelist as wl
from api.services.gmail.backfill import enqueue_backfill, enqueue_manual_scan
from api.services.secrets import get_secret_store

from . import gmail_onboarding
from . import messages_es
from .app import get_bot
from .redis_keys import (
    GMAIL_MANUAL_SCAN_COOLDOWN_S,
    gmail_manual_scan_cooldown_key,
)
from .user_resolver import user_by_telegram_id


log = logging.getLogger("bot.gmail_handlers")


router = Router(name="gmail_onboarding")


# ── sample analyzer singleton (used by /agregar_muestra) ────────────────────
# Lazy-instantiated AnthropicSampleAnalyzer; tests inject a stub via
# `set_sample_analyzer`. Keeping the singleton here (not in services/)
# because the only consumer is the bot handler.

_sample_analyzer = None


def get_sample_analyzer():
    """Return the process-wide sample analyzer client. First call
    constructs an AnthropicSampleAnalyzer using the same Haiku model
    as the chat extractor."""
    global _sample_analyzer
    if _sample_analyzer is None:
        from api.services.gmail.sample_analyzer import AnthropicSampleAnalyzer

        _sample_analyzer = AnthropicSampleAnalyzer(
            api_key=settings.anthropic_api_key,
            model=settings.llm_extraction_model,
        )
    return _sample_analyzer


def set_sample_analyzer(client) -> None:
    """Test hook. Pass None to force the next get_sample_analyzer() call
    to lazy-construct a fresh real client."""
    global _sample_analyzer
    _sample_analyzer = client


# RFC-ish email regex. Permissive — anything Gmail's `from:` query can
# match is fine, since the worst case is a sender that never appears in
# the user's inbox.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def _resolve_user(message: Message) -> Optional[tuple[User, AsyncSession]]:
    """Resolve the User from message.from_user.id. Returns (user, session)
    or None and replies with PAIR_PROMPT itself.

    Caller is responsible for closing the session.
    """
    if message.from_user is None:
        return None
    db = AsyncSessionLocal()
    user = await user_by_telegram_id(
        telegram_user_id=message.from_user.id, db=db
    )
    if user is None:
        await db.close()
        await message.answer(messages_es.PAIR_PROMPT)
        return None
    return user, db


async def _get_credential(
    user_id: uuid.UUID, db: AsyncSession
) -> Optional[GmailCredential]:
    row = await db.execute(
        select(GmailCredential).where(GmailCredential.user_id == user_id)
    )
    return row.scalar_one_or_none()


# ── shared keyboard / text helpers ───────────────────────────────────────────


def _bank_selection_text(
    pending_senders: list[dict], *, awaiting_bank: Optional[str] = None
) -> str:
    """Render the body of the bank-selection prompt based on what the
    user has picked so far. When pending is empty we use a different
    string because the listing-zero case looks odd.

    When `awaiting_bank` is set, append a footer reminding the user
    we're waiting for their typed email — keeps the running state
    visible without sending a separate message on every preset tap.
    """
    if not pending_senders:
        body = messages_es.GMAIL_BANK_SELECTION_HEADER_EMPTY
    else:
        lines = []
        for entry in pending_senders:
            email = entry.get("email", "")
            bank = entry.get("bank_name")
            suffix = f" ({bank})" if bank else ""
            lines.append(f"• <code>{email}</code>{suffix}")
        body = messages_es.GMAIL_BANK_SELECTION_HEADER_TPL.format(
            lines="\n".join(lines)
        )
    if awaiting_bank:
        body += messages_es.GMAIL_BANK_AWAITING_TPL.format(bank=awaiting_bank)
    return body


def _bank_selection_kb(*, mode: str = "onboarding") -> InlineKeyboardMarkup:
    """Inline keyboard with one button per preset bank, plus Listo and
    Cancelar. `mode` switches the Listo callback so the same keyboard
    can drive both onboarding and /agregar_banco.
    """
    rows = []
    bank_buttons: list[InlineKeyboardButton] = []
    for bank_name in KNOWN_BANK_SENDERS_CR.keys():
        bank_buttons.append(
            InlineKeyboardButton(
                text=bank_name, callback_data=f"bank_preset:{bank_name}"
            )
        )
    # Lay out 2 buttons per row for readability on phones.
    for i in range(0, len(bank_buttons), 2):
        rows.append(bank_buttons[i : i + 2])
    done_cb = "bank_done" if mode == "onboarding" else "bank_done_addonly"
    rows.append(
        [
            InlineKeyboardButton(
                text=messages_es.GMAIL_BANK_SELECTION_LISTO, callback_data=done_cb
            ),
            InlineKeyboardButton(
                text=messages_es.GMAIL_BANK_SELECTION_CANCELAR,
                callback_data="bank_cancel",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=messages_es.GMAIL_ACTIVATE_BUTTON,
                    callback_data="bank_confirm:activate",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=messages_es.GMAIL_BANK_CONFIRM_EDIT,
                    callback_data="bank_confirm:edit",
                ),
                InlineKeyboardButton(
                    text=messages_es.GMAIL_BANK_SELECTION_CANCELAR,
                    callback_data="bank_confirm:cancel",
                ),
            ],
        ]
    )


async def send_bank_selection_prompt(
    *, bot: Bot, chat_id: int, user_id: uuid.UUID, redis
) -> None:
    """Send the bank-selection message and remember its message_id so
    later preset taps can edit-in-place. Called by the OAuth callback
    listener AND by /agregar_banco. The state must already be
    `selecting_banks` before this runs.
    """
    state = await gmail_onboarding.get(user_id, redis)
    if state is None:
        log.warning("send_bank_selection_prompt: no state for user=%s", user_id)
        return
    sent = await bot.send_message(
        chat_id=chat_id,
        text=messages_es.GMAIL_CALLBACK_SUCCESS,
        reply_markup=_bank_selection_kb(mode="onboarding"),
    )
    await gmail_onboarding.set_selection_message_id(
        user_id=user_id, message_id=sent.message_id, redis=redis
    )


# ── /conectar_gmail ──────────────────────────────────────────────────────────


@router.message(Command("conectar_gmail"))
async def on_connect_gmail(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if cred is not None and cred.revoked_at is None:
            await message.answer(messages_es.GMAIL_CONNECT_ALREADY_CONNECTED)
            return

        redis = get_redis()
        try:
            auth_url = await oauth_svc.build_auth_url(
                user_id=user.id, redis=redis
            )
        except oauth_svc.OAuthStateError as e:
            log.warning("conectar_gmail config error: %s", e)
            await message.answer(messages_es.GMAIL_CONNECT_FAILED_CONFIG)
            return

        await gmail_onboarding.begin(
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            redis=redis,
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=messages_es.GMAIL_CONNECT_BUTTON, url=auth_url
                    )
                ]
            ]
        )
        await message.answer(
            messages_es.GMAIL_CONNECT_INTRO, reply_markup=kb
        )
    finally:
        await db.close()


# ── /desconectar_gmail ───────────────────────────────────────────────────────


@router.message(Command("desconectar_gmail"))
async def on_disconnect_gmail(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if cred is None or cred.revoked_at is not None:
            await message.answer(messages_es.GMAIL_DISCONNECT_NOT_CONNECTED)
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Sí, desconectar",
                        callback_data="gmail_disconnect:confirm",
                    ),
                    InlineKeyboardButton(
                        text="Cancelar",
                        callback_data="gmail_disconnect:cancel",
                    ),
                ]
            ]
        )
        await message.answer(
            messages_es.GMAIL_DISCONNECT_CONFIRM, reply_markup=kb
        )
    finally:
        await db.close()


@router.callback_query(F.data.startswith("gmail_disconnect:"))
async def on_disconnect_callback(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass

    if action == "cancel":
        await cb.message.answer("Cancelado.")
        await cb.answer()
        return

    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return

        cred = await _get_credential(user.id, db)
        if cred is None or cred.revoked_at is not None:
            await cb.message.answer(
                messages_es.GMAIL_DISCONNECT_NOT_CONNECTED
            )
            await cb.answer()
            return

        store = get_secret_store()
        try:
            await store.delete(cred.kv_secret_name)
        except Exception:
            log.exception(
                "secret store delete failed; aborting disconnect for user=%s",
                user.id,
            )
            await cb.message.answer(messages_es.GMAIL_CALLBACK_ERROR)
            await cb.answer()
            return

        cred.revoked_at = func.now()
        await db.commit()

        await gmail_onboarding.clear(user.id, redis=get_redis())

        await cb.message.answer(messages_es.GMAIL_DISCONNECT_DONE)
    finally:
        await db.close()
        await cb.answer()


# ── /estado_gmail ────────────────────────────────────────────────────────────


@router.message(Command("estado_gmail"))
async def on_status_gmail(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if cred is None or cred.revoked_at is not None:
            await message.answer(messages_es.GMAIL_STATUS_DISCONNECTED)
            return

        senders = await wl.list_active(db=db, user_id=user.id)
        if not senders:
            wl_section = messages_es.GMAIL_STATUS_NO_WHITELIST
        else:
            lines = [
                messages_es.GMAIL_STATUS_WHITELIST_HEADER.format(
                    count=len(senders)
                )
            ]
            for s in senders:
                bank = f" — {s.bank_name}" if s.bank_name else ""
                lines.append(f"• <code>{s.sender_email}</code>{bank}")
            wl_section = "\n".join(lines)

        await message.answer(
            messages_es.GMAIL_STATUS_CONNECTED_TPL.format(
                granted_at=_format_dt(cred.granted_at),
                activated_at=_format_dt(cred.activated_at),
                last_refresh_at=_format_dt(cred.last_refresh_at),
                whitelist_section=wl_section,
            )
        )
    finally:
        await db.close()


# ── /agregar_banco ───────────────────────────────────────────────────────────


@router.message(Command("agregar_banco"))
async def on_add_bank(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if (
            cred is None
            or cred.revoked_at is not None
            or cred.activated_at is None
        ):
            await message.answer(messages_es.GMAIL_ADD_BANK_NOT_ACTIVE)
            return

        # Reuse the onboarding state. `begin` overwrites any stale state.
        redis = get_redis()
        await gmail_onboarding.begin(
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            redis=redis,
        )
        await gmail_onboarding.transition(
            user_id=user.id, to="selecting_banks", redis=redis
        )
        sent = await message.answer(
            messages_es.GMAIL_ADD_BANK_ENTRY,
            reply_markup=_bank_selection_kb(mode="add_bank"),
        )
        await gmail_onboarding.set_selection_message_id(
            user_id=user.id, message_id=sent.message_id, redis=redis
        )
    finally:
        await db.close()


# ── /quitar_banco ────────────────────────────────────────────────────────────


@router.message(Command("quitar_banco"))
async def on_remove_bank(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        senders = await wl.list_active(db=db, user_id=user.id)
        if not senders:
            await message.answer(messages_es.GMAIL_REMOVE_BANK_NO_ACTIVE)
            return

        rows = []
        for s in senders:
            label = (
                f"{s.bank_name} — {s.sender_email}"
                if s.bank_name
                else s.sender_email
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label, callback_data=f"bank_remove:{s.id}"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=messages_es.GMAIL_BANK_SELECTION_CANCELAR,
                    callback_data="bank_remove_cancel",
                )
            ]
        )
        await message.answer(
            messages_es.GMAIL_REMOVE_BANK_PROMPT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    finally:
        await db.close()


@router.callback_query(F.data.startswith("bank_remove:"))
async def on_remove_bank_callback(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    sender_id_raw = cb.data.split(":", 1)[1]
    try:
        sender_id = uuid.UUID(sender_id_raw)
    except ValueError:
        await cb.answer()
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass

    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return

        # Look up the row for the message text BEFORE removing.
        row = await db.execute(
            select(wl.GmailSenderWhitelist)
            .where(wl.GmailSenderWhitelist.id == sender_id)
            .where(wl.GmailSenderWhitelist.user_id == user.id)
        )
        sender = row.scalar_one_or_none()
        if sender is None or sender.removed_at is not None:
            await cb.message.answer(messages_es.GMAIL_REMOVE_BANK_NOT_FOUND)
            await cb.answer()
            return

        ok = await wl.remove_sender_by_id(
            db=db, user_id=user.id, sender_id=sender_id
        )
        await db.commit()
        if ok:
            await cb.message.answer(
                messages_es.GMAIL_REMOVE_BANK_DONE_TPL.format(
                    email=sender.sender_email
                )
            )
        else:
            await cb.message.answer(messages_es.GMAIL_REMOVE_BANK_NOT_FOUND)
    finally:
        await db.close()
        await cb.answer()


@router.callback_query(F.data == "bank_remove_cancel")
async def on_remove_bank_cancel(cb: CallbackQuery) -> None:
    if cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover
            pass
        await cb.message.answer(messages_es.GMAIL_REMOVE_BANK_CANCELLED)
    await cb.answer()


# ── /agregar_muestra (placeholder until Block D) ─────────────────────────────


# ── /aprobar_shadow / /rechazar_shadow (Block C.2) ───────────────────────────


@router.message(Command("aprobar_shadow"))
async def on_approve_shadow(message: Message) -> None:
    """Promote all of the user's gmail-source shadow rows to confirmed."""
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        from sqlalchemy import update

        from api.models.transaction import Transaction

        result = await db.execute(
            update(Transaction)
            .where(Transaction.user_id == user.id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
            .values(status="confirmed")
            .returning(Transaction.id)
        )
        ids = [r[0] for r in result.fetchall()]
        await db.commit()
        if not ids:
            await message.answer(messages_es.GMAIL_APPROVE_SHADOW_NONE)
            return
        await message.answer(
            messages_es.GMAIL_APPROVE_SHADOW_DONE_TPL.format(count=len(ids))
        )
    finally:
        await db.close()


@router.message(Command("rechazar_shadow"))
async def on_reject_shadow_prompt(message: Message) -> None:
    """Confirm with an inline keyboard. The actual delete + mark
    happens on the callback."""
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        from sqlalchemy import func as sa_func, select

        from api.models.transaction import Transaction

        count_row = await db.execute(
            select(sa_func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user.id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
        )
        count = count_row.scalar_one()
        if count == 0:
            await message.answer(messages_es.GMAIL_APPROVE_SHADOW_NONE)
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=messages_es.GMAIL_REJECT_SHADOW_BUTTON_YES,
                        callback_data="shadow_reject:confirm",
                    ),
                    InlineKeyboardButton(
                        text=messages_es.GMAIL_BANK_SELECTION_CANCELAR,
                        callback_data="shadow_reject:cancel",
                    ),
                ]
            ]
        )
        await message.answer(
            messages_es.GMAIL_REJECT_SHADOW_CONFIRM_TPL.format(count=count),
            reply_markup=kb,
        )
    finally:
        await db.close()


@router.callback_query(F.data.startswith("shadow_reject:"))
async def on_reject_shadow_callback(cb: CallbackQuery) -> None:
    """Handle confirm/cancel on the /rechazar_shadow prompt."""
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass

    if action == "cancel":
        await cb.message.answer(messages_es.GMAIL_REJECT_SHADOW_CANCELLED)
        await cb.answer()
        return

    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return

        from sqlalchemy import delete, select, update

        from api.models.gmail_message_seen import GmailMessageSeen
        from api.models.transaction import Transaction

        # 1. Pick up all shadow gmail rows for this user.
        rows = await db.execute(
            select(Transaction.id, Transaction.gmail_message_id)
            .where(Transaction.user_id == user.id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
        )
        targets = [(r[0], r[1]) for r in rows.fetchall()]
        if not targets:
            await cb.message.answer(messages_es.GMAIL_APPROVE_SHADOW_NONE)
            await cb.answer()
            return

        gmail_ids = [g for _t, g in targets if g]
        # 2. Mark seen rows as rejected_by_user (audit trail) BEFORE the
        # transaction rows are deleted, because gmail_messages_seen.transaction_id
        # has ON DELETE SET NULL — we don't lose the link, but rejecting
        # before delete keeps the cause-and-effect ordering clean.
        if gmail_ids:
            await db.execute(
                update(GmailMessageSeen)
                .where(GmailMessageSeen.user_id == user.id)
                .where(GmailMessageSeen.gmail_message_id.in_(gmail_ids))
                .values(outcome="rejected_by_user")
            )
        # 3. Delete the shadow transactions.
        await db.execute(
            delete(Transaction)
            .where(Transaction.user_id == user.id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
        )
        await db.commit()

        await cb.message.answer(
            messages_es.GMAIL_REJECT_SHADOW_DONE_TPL.format(count=len(targets))
        )
    finally:
        await db.close()
        await cb.answer()


@router.message(Command("revisar_correos"))
async def on_manual_scan(message: Message) -> None:
    """Manual scan with a 30-minute cooldown. Runs days=2 (last 48h)."""
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if (
            cred is None
            or cred.revoked_at is not None
            or cred.activated_at is None
        ):
            await message.answer(messages_es.GMAIL_MANUAL_SCAN_NOT_ACTIVE)
            return

        redis = get_redis()
        key = gmail_manual_scan_cooldown_key(user.id)
        # Atomic SETNX: only the first caller within the window wins.
        # Returns True (in redis-py decode_responses=True we get bool/int)
        # if the key was new; False if it already existed.
        was_set = await redis.set(
            key, "1", ex=GMAIL_MANUAL_SCAN_COOLDOWN_S, nx=True
        )
        if not was_set:
            ttl = await redis.ttl(key)
            minutes = max(1, (ttl + 59) // 60) if ttl > 0 else 30
            await message.answer(
                messages_es.GMAIL_MANUAL_SCAN_COOLDOWN.format(minutes=minutes)
            )
            return

        enqueue_manual_scan(user_id=user.id)
        await message.answer(messages_es.GMAIL_MANUAL_SCAN_QUEUED)
    finally:
        await db.close()


@router.message(Command("agregar_muestra"))
async def on_add_sample(message: Message) -> None:
    """Block D.2: enter the optional-sample state. Next photo or text
    from this user is treated as a sample, not as text routed to the
    extractor or to the bank-selection flow.

    Independent of the onboarding state machine — this works only when
    the user is already activated, and uses its own short-TTL Redis
    key indexed by telegram_user_id (so the message filter doesn't
    need a DB lookup).
    """
    if message.from_user is None:
        return
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if (
            cred is None
            or cred.revoked_at is not None
            or cred.activated_at is None
        ):
            await message.answer(messages_es.GMAIL_ADD_SAMPLE_NOT_ACTIVE)
            return

        from .redis_keys import (
            GMAIL_OPTIONAL_SAMPLE_TTL_S,
            gmail_optional_sample_key,
        )

        redis = get_redis()
        await redis.set(
            gmail_optional_sample_key(message.from_user.id),
            "1",
            ex=GMAIL_OPTIONAL_SAMPLE_TTL_S,
        )
        await message.answer(messages_es.GMAIL_ADD_SAMPLE_PROMPT)
    finally:
        await db.close()


# ── filter for the optional-sample state ────────────────────────────────────


async def _is_awaiting_optional_sample(message: Message) -> bool:
    """True iff the user is currently in the /agregar_muestra window.
    Cheap: one Redis GET, no DB hit (the key is indexed by
    telegram_user_id, which we already have on the message)."""
    if message.from_user is None:
        return False
    from .redis_keys import gmail_optional_sample_key

    raw = await get_redis().get(
        gmail_optional_sample_key(message.from_user.id)
    )
    return raw is not None


# ── /agregar_muestra: text and photo handlers (Block D.2) ───────────────────


async def _persist_optional_sample(
    *,
    user_id: uuid.UUID,
    raw_text: str,
    source: str,  # 'text' | 'photo'
    db: AsyncSession,
) -> tuple[Optional[str], Optional[str]]:
    """Run the analyzer, persist a BankNotificationSample row.
    Returns (bank_name, sender_email) so the handler can format the
    confirmation message.
    """
    from api.models.bank_notification_sample import BankNotificationSample
    from api.services.gmail.sample_analyzer import (
        SampleAnalyzerError,
        analyze_image_sample,
        analyze_text_sample,
    )

    # Reuse the Block A.4 sample analyzer client.
    client = get_sample_analyzer()
    try:
        if source == "text":
            analysis = await analyze_text_sample(raw_text, client=client)
        else:
            # Caller already turned bytes into text via the analyzer's
            # vision step — we receive raw_text == extracted text.
            analysis = await analyze_text_sample(raw_text, client=client)
    except SampleAnalyzerError:
        raise

    sample = BankNotificationSample(
        user_id=user_id,
        raw_text=raw_text,
        source=source,
        detected_sender=analysis.sender_email,
        detected_bank=analysis.bank_name,
        detected_format=analysis.format_signature,
        confidence=analysis.confidence,
    )
    db.add(sample)
    await db.commit()
    return analysis.bank_name, analysis.sender_email


@router.message(F.text, _is_awaiting_optional_sample)
async def on_optional_sample_text(message: Message) -> None:
    if message.from_user is None or not message.text:
        return
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return
        await message.answer(messages_es.GMAIL_ADD_SAMPLE_ANALYZING)
        try:
            from api.services.gmail.sample_analyzer import SampleAnalyzerError

            try:
                bank_name, sender = await _persist_optional_sample(
                    user_id=user.id,
                    raw_text=message.text,
                    source="text",
                    db=db,
                )
            except SampleAnalyzerError:
                log.exception("optional sample text analyze failed")
                await message.answer(messages_es.GMAIL_ADD_SAMPLE_ERROR)
                return
        finally:
            # Clear state regardless of outcome so the user doesn't get
            # stuck in optional-sample mode with future messages.
            from .redis_keys import gmail_optional_sample_key

            await get_redis().delete(
                gmail_optional_sample_key(message.from_user.id)
            )

        if bank_name and sender:
            detail = messages_es.GMAIL_ADD_SAMPLE_SAVED_DETAIL_KNOWN.format(
                bank=bank_name, sender=sender
            )
        else:
            detail = messages_es.GMAIL_ADD_SAMPLE_SAVED_DETAIL_UNKNOWN
        await message.answer(
            messages_es.GMAIL_ADD_SAMPLE_SAVED_TPL.format(detail=detail)
        )
    finally:
        await db.close()


@router.message(F.photo, _is_awaiting_optional_sample)
async def on_optional_sample_photo(message: Message) -> None:
    if message.from_user is None or not message.photo:
        return
    bot = get_bot()
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return
        await message.answer(messages_es.GMAIL_ADD_SAMPLE_ANALYZING)

        biggest = max(message.photo, key=lambda p: p.width * p.height)
        try:
            buf = await bot.download(biggest.file_id)
            image_bytes = buf.read() if hasattr(buf, "read") else buf
        except Exception:
            log.exception("optional sample photo download failed")
            await message.answer(messages_es.GMAIL_ADD_SAMPLE_DOWNLOAD_FAILED)
            return

        from api.services.gmail.sample_analyzer import (
            SampleAnalyzerError,
            analyze_image_sample,
        )
        from api.models.bank_notification_sample import BankNotificationSample

        try:
            analysis = await analyze_image_sample(
                image_bytes, client=get_sample_analyzer()
            )
        except SampleAnalyzerError:
            log.exception("optional sample image analyze failed")
            await message.answer(messages_es.GMAIL_ADD_SAMPLE_ERROR)
            return
        finally:
            from .redis_keys import gmail_optional_sample_key

            await get_redis().delete(
                gmail_optional_sample_key(message.from_user.id)
            )

        sample = BankNotificationSample(
            user_id=user.id,
            raw_text=analysis.raw_text,
            source="photo",
            detected_sender=analysis.sender_email,
            detected_bank=analysis.bank_name,
            detected_format=analysis.format_signature,
            confidence=analysis.confidence,
        )
        db.add(sample)
        await db.commit()

        if analysis.bank_name and analysis.sender_email:
            detail = messages_es.GMAIL_ADD_SAMPLE_SAVED_DETAIL_KNOWN.format(
                bank=analysis.bank_name, sender=analysis.sender_email
            )
        else:
            detail = messages_es.GMAIL_ADD_SAMPLE_SAVED_DETAIL_UNKNOWN
        await message.answer(
            messages_es.GMAIL_ADD_SAMPLE_SAVED_TPL.format(detail=detail)
        )
    finally:
        await db.close()


# ── selecting_banks: filter ─────────────────────────────────────────────────


async def _is_selecting_banks(message: Message) -> bool:
    """True iff the user is currently in `selecting_banks` state.

    This filter gates the custom-email handler so a non-onboarding
    text message falls through to the extractor as usual. Onboarding
    is exceptional — most messages skip this fast (no Redis key).
    """
    if message.from_user is None or not message.text:
        return False
    if message.text.startswith("/"):
        return False
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return False
        state = await gmail_onboarding.get(user.id, redis=get_redis())
        return state is not None and state.state == "selecting_banks"
    finally:
        await db.close()


# ── selecting_banks: preset tap callback ─────────────────────────────────────


@router.callback_query(F.data.startswith("bank_preset:"))
async def on_bank_preset_tap(cb: CallbackQuery) -> None:
    """User tapped a preset bank button. We do NOT auto-load canonical
    senders — instead we set `awaiting_bank` and ask the user to type
    the actual email they receive notifications from. See decisions
    doc entry "Preset tap pide correo, no precarga senders"."""
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    bank_name = cb.data.split(":", 1)[1]
    if bank_name not in KNOWN_BANK_SENDERS_CR:
        await cb.answer("Banco desconocido", show_alert=True)
        return

    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return
    finally:
        await db.close()

    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_banks":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return

    # Cap check: count active pending + 1 to admit the email about to
    # come in. We're tolerant — if at cap, refuse the preset tap.
    if len(state.pending_senders) >= wl.ACTIVE_CAP:
        await cb.answer(messages_es.GMAIL_BANK_CAP_REACHED, show_alert=True)
        return

    # Stash the bank we're awaiting an email for. If a previous
    # awaiting_bank was set, this overwrites — UX permissive: tap BAC,
    # change mind, tap Promerica, then type Promerica's email. We don't
    # block that.
    await gmail_onboarding.set_awaiting_bank(
        user_id=user.id, bank_name=bank_name, redis=redis
    )

    # Edit the keyboard message in place: same text, but with the
    # "esperando correo de BAC" footer.
    fresh = await gmail_onboarding.get(user.id, redis)
    new_text = _bank_selection_text(
        fresh.pending_senders if fresh else [],
        awaiting_bank=bank_name,
    )
    try:
        await cb.message.edit_text(
            new_text, reply_markup=cb.message.reply_markup
        )
    except Exception:
        # Edit may fail if the message body is identical (no-op edit).
        log.debug("edit_text on preset tap failed", exc_info=True)

    await cb.answer(
        messages_es.GMAIL_BANK_PRESET_BUTTON_ACK.format(bank=bank_name)
    )
    # Send a separate prompt so it's unmistakable what the user has to
    # do next. Keeps the keyboard message clean for the running list.
    await cb.message.answer(
        messages_es.GMAIL_BANK_PRESET_ASK_EMAIL.format(bank=bank_name)
    )


# ── selecting_banks: custom email text ───────────────────────────────────────


@router.message(F.text, _is_selecting_banks)
async def on_custom_email(message: Message) -> None:
    """Receive an email address while in selecting_banks.

    Two paths converge here:
      1. The user typed an email cold (no preset tapped). We use
         `infer_bank_from_email` to label the sender and record
         `source='custom_typed'`.
      2. The user tapped a preset earlier (e.g. BAC) and we set
         `awaiting_bank`. We use that bank name verbatim and record
         `source='preset_tap'`. Inference is skipped — the user's
         intent ("this is my BAC notification email") wins over
         whatever the domain says.
    """
    if message.from_user is None or not message.text:
        return
    text = message.text.strip()
    if not _EMAIL_RE.match(text):
        await message.answer(messages_es.GMAIL_BANK_CUSTOM_INVALID)
        return

    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return
    finally:
        await db.close()

    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_banks":
        # Filter said yes a moment ago; race lost. Bail quietly.
        return

    if len(state.pending_senders) >= wl.ACTIVE_CAP:
        await message.answer(messages_es.GMAIL_BANK_CAP_REACHED)
        return

    awaited_bank = state.awaiting_bank
    norm = wl.normalize_email(text)

    if awaited_bank:
        bank_name = awaited_bank
        source = wl.SOURCE_PRESET
    else:
        bank_name = infer_bank_from_email(text)
        source = wl.SOURCE_CUSTOM

    _, was_new = await gmail_onboarding.add_pending_sender(
        user_id=user.id,
        email=norm,
        bank_name=bank_name,
        source=source,
        redis=redis,
    )

    # Always clear awaiting_bank — even if the email was a duplicate,
    # the user already responded to the prompt; don't leave them stuck.
    if awaited_bank is not None:
        await gmail_onboarding.set_awaiting_bank(
            user_id=user.id, bank_name=None, redis=redis
        )

    if not was_new:
        await message.answer(messages_es.GMAIL_BANK_PRESET_ALREADY)
    elif awaited_bank:
        await message.answer(
            messages_es.GMAIL_BANK_CUSTOM_ADDED_FOR_PRESET.format(
                email=norm, bank=awaited_bank
            )
        )
    elif bank_name:
        await message.answer(
            messages_es.GMAIL_BANK_CUSTOM_ADDED_KNOWN.format(
                email=norm, bank=bank_name
            )
        )
    else:
        await message.answer(
            messages_es.GMAIL_BANK_CUSTOM_ADDED_UNKNOWN.format(email=norm)
        )

    # Refresh the live keyboard message if we have its id, so the user
    # can see the running list. Best-effort.
    if state.selection_message_id is not None:
        bot = get_bot()
        try:
            fresh = await gmail_onboarding.get(user.id, redis)
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=state.selection_message_id,
                text=_bank_selection_text(
                    fresh.pending_senders if fresh else [],
                    awaiting_bank=None,
                ),
                reply_markup=_bank_selection_kb(mode="onboarding"),
            )
        except Exception:
            log.debug("edit selection message after custom add failed", exc_info=True)


# ── bank_done / bank_cancel ──────────────────────────────────────────────────


async def _resolve_user_for_callback(
    cb: CallbackQuery,
) -> Optional[User]:
    """Like _resolve_user but for callbacks (no Message ergonomics).
    Closes its own session — caller must NOT use it for DB ops."""
    if cb.from_user is None:
        return None
    db = AsyncSessionLocal()
    try:
        return await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
    finally:
        await db.close()


@router.callback_query(F.data == "bank_done")
async def on_bank_done(cb: CallbackQuery) -> None:
    """User tapped Listo during ONBOARDING (first time). Show the
    confirm prompt; activation happens on bank_confirm:activate."""
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_banks":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.pending_senders:
        await cb.answer(
            messages_es.GMAIL_BANK_SELECTION_LISTO_EMPTY, show_alert=True
        )
        return
    if state.awaiting_bank:
        await cb.answer(
            messages_es.GMAIL_BANK_LISTO_PENDING_BANK.format(
                bank=state.awaiting_bank
            ),
            show_alert=True,
        )
        return

    await gmail_onboarding.transition(
        user_id=user.id, to="confirming", redis=redis
    )

    lines = []
    for entry in state.pending_senders:
        bank = entry.get("bank_name")
        suffix = f" ({bank})" if bank else ""
        lines.append(f"• <code>{entry['email']}</code>{suffix}")
    text = messages_es.GMAIL_BANK_CONFIRM_TPL.format(lines="\n".join(lines))

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass
    await cb.message.answer(text, reply_markup=_confirm_kb())
    await cb.answer()


@router.callback_query(F.data == "bank_done_addonly")
async def on_bank_done_addonly(cb: CallbackQuery) -> None:
    """User tapped Listo during /agregar_banco (already activated).
    Skip the confirmation step — just commit the senders to whitelist."""
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_banks":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.pending_senders:
        await cb.answer(
            messages_es.GMAIL_BANK_SELECTION_LISTO_EMPTY, show_alert=True
        )
        return
    if state.awaiting_bank:
        await cb.answer(
            messages_es.GMAIL_BANK_LISTO_PENDING_BANK.format(
                bank=state.awaiting_bank
            ),
            show_alert=True,
        )
        return

    db = AsyncSessionLocal()
    added_lines = []
    try:
        for entry in state.pending_senders:
            row = await wl.add_sender(
                db=db,
                user_id=user.id,
                sender_email=entry["email"],
                bank_name=entry.get("bank_name"),
                source=entry.get("source", wl.SOURCE_CUSTOM),
            )
            bank_suffix = f" ({row.bank_name})" if row.bank_name else ""
            added_lines.append(f"• <code>{row.sender_email}</code>{bank_suffix}")
        await db.commit()
    finally:
        await db.close()

    await gmail_onboarding.clear(user.id, redis)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass
    await cb.message.answer(
        messages_es.GMAIL_ADD_BANK_DONE_TPL.format(lines="\n".join(added_lines))
    )
    await cb.answer()


@router.callback_query(F.data == "bank_cancel")
async def on_bank_cancel(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer()
        return
    await gmail_onboarding.clear(user.id, redis=get_redis())
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass
    await cb.message.answer(messages_es.GMAIL_BANK_CANCELLED)
    await cb.answer()


# ── confirming: bank_confirm:* ───────────────────────────────────────────────


@router.callback_query(F.data.startswith("bank_confirm:"))
async def on_bank_confirm(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return

    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "confirming":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # pragma: no cover
        pass

    if action == "cancel":
        await gmail_onboarding.clear(user.id, redis)
        await cb.message.answer(messages_es.GMAIL_BANK_CANCELLED)
        await cb.answer()
        return

    if action == "edit":
        await gmail_onboarding.transition(
            user_id=user.id, to="selecting_banks", redis=redis
        )
        sent = await cb.message.answer(
            _bank_selection_text(state.pending_senders),
            reply_markup=_bank_selection_kb(mode="onboarding"),
        )
        await gmail_onboarding.set_selection_message_id(
            user_id=user.id, message_id=sent.message_id, redis=redis
        )
        await cb.answer()
        return

    if action == "activate":
        await _activate_and_persist(user=user, state_redis=redis, cb=cb)
        return

    log.warning("unknown bank_confirm action: %s", action)
    await cb.answer()


async def _activate_and_persist(
    *, user: User, state_redis, cb: CallbackQuery
) -> None:
    """Flip activated_at, persist whitelist, kick backfill (B.4 stub).

    Order matters:
      1. activated_at + commit  → DB consistent.
      2. whitelist.add_sender + commit → scanner sees senders.
      3. asyncio.create_task(_run_backfill_safe) → fire-and-forget.
      4. clear onboarding state.
      5. reply to user.
    """
    state = await gmail_onboarding.get(user.id, state_redis)
    if state is None or not state.pending_senders:
        await cb.answer()
        return

    db = AsyncSessionLocal()
    try:
        cred = await _get_credential(user.id, db)
        if cred is None or cred.revoked_at is not None:
            await cb.message.answer(messages_es.GMAIL_STATUS_DISCONNECTED)
            await cb.answer()
            return

        if cred.activated_at is None:
            cred.activated_at = func.now()
        await db.commit()

        for entry in state.pending_senders:
            await wl.add_sender(
                db=db,
                user_id=user.id,
                sender_email=entry["email"],
                bank_name=entry.get("bank_name"),
                source=entry.get("source", wl.SOURCE_CUSTOM),
            )
        await db.commit()
    finally:
        await db.close()

    # B.4: kick backfill fire-and-forget. The Task lives in the event
    # loop; Python's GC won't collect it because asyncio holds a ref
    # internally. We don't await it — the user gets the "¡Activado!"
    # reply immediately, and the start/end notices come from
    # backfill.run_backfill itself.
    enqueue_backfill(user_id=user.id)

    await gmail_onboarding.clear(user.id, state_redis)
    await cb.message.answer(messages_es.GMAIL_ACTIVATED)
    await cb.answer()
