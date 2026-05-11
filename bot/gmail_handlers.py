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

import asyncio
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
from api.data.bank_directory_cr import (
    BANK_DIRECTORY_CR,
    bank_by_slug,
    bank_name_for_slug,
    match_bank_by_hint,
)
from api.database import AsyncSessionLocal
from api.models.gmail_credential import GmailCredential
from api.models.user import User
from api.redis_client import get_redis
from api.services.gmail import discovery as discovery_svc
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

_DISCOVERY_KEYWORDS = [
    "transacción",
    "compra",
    "movimiento",
    "se realizó",
    "transferencia",
    "notificación",
    "comprobante",
]


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
    for bank in BANK_DIRECTORY_CR:
        bank_buttons.append(
            InlineKeyboardButton(
                text=bank["name"], callback_data=f"bank_preset:{bank['slug']}"
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


def _root_kb(*, can_finish: bool) -> InlineKeyboardMarkup:
    done_text = "Listo, activar 🚀" if can_finish else "Listo, no agregar más"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Por banco 💡", callback_data="gmail_root:bank"
                ),
                InlineKeyboardButton(
                    text="Descubrir por keyword 🔍",
                    callback_data="gmail_root:discovery",
                ),
            ],
            [
                InlineKeyboardButton(text=done_text, callback_data="gmail_root:done")
            ],
        ]
    )


def _root_text() -> str:
    return (
        "¡Conectado! ¿Cómo querés agregar tus bancos?\n\n"
        "💡 <b>Por banco</b> (más rápido si sabés el correo): elegís un banco "
        "de una lista y me das el sender exacto. Te ofrezco hacer un test "
        "rápido para confirmar.\n\n"
        "🔍 <b>Descubrir por keyword</b> (si no sabés los correos): escaneo tu "
        "Gmail por palabras como “transacción” o “compra” y te muestro los "
        "senders que aparecen.\n\n"
        "Podés combinar los dos."
    )


def _bank_directory_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons = [
        InlineKeyboardButton(
            text=bank["name"], callback_data=f"bank_select:{bank['slug']}"
        )
        for bank in BANK_DIRECTORY_CR
    ]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])
    rows.append([InlineKeyboardButton(text="Volver", callback_data="gmail_root:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _test_scan_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Sí, scanear", callback_data="bank_test:yes"),
                InlineKeyboardButton(text="No, agregar igual", callback_data="bank_test:no"),
            ]
        ]
    )


def _hint_override_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Sí, agregar", callback_data="bank_hint_override:yes"
                ),
                InlineKeyboardButton(
                    text="No, lo escribo de nuevo",
                    callback_data="bank_hint_override:no",
                ),
            ]
        ]
    )


def _zero_scan_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Agregar igual", callback_data="bank_test_zero:add"
                ),
                InlineKeyboardButton(
                    text="Probar otro sender", callback_data="bank_test_zero:retry"
                ),
            ]
        ]
    )


def _keyword_kb(selected: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons = []
    selected_set = {kw.casefold() for kw in selected}
    for idx, kw in enumerate(_DISCOVERY_KEYWORDS):
        mark = "✅" if kw.casefold() in selected_set else "☐"
        buttons.append(
            InlineKeyboardButton(text=f"{mark} {kw}", callback_data=f"kw_toggle:{idx}")
        )
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])
    search_text = "Buscar 🔍" if selected else "Buscar 🔍 (elegí una)"
    rows.append(
        [
            InlineKeyboardButton(text=search_text, callback_data="kw_search"),
            InlineKeyboardButton(text="Cancelar", callback_data="kw_cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _discovery_results_text(
    *, keywords: list[str], candidates: list[dict], selected: list[int]
) -> str:
    if not candidates:
        return (
            "No encontré senders con esas keywords. Podés probar palabras más "
            "generales como “transacción” o “notificación”."
        )
    selected_set = set(selected)
    lines = [f"Encontré estos senders con {', '.join(keywords)}:\n"]
    for idx, item in enumerate(candidates):
        mark = "✅" if idx in selected_set else "☐"
        email = item.get("email", "")
        count = item.get("count", 0)
        lines.append(f"{mark} <code>{email}</code>  ({count} correos)")
        for subject in (item.get("sample_subjects") or [])[:2]:
            lines.append(f"  “{subject}”")
    lines.append("\nTapeá los que SÍ son bancarios y dale a Confirmar.")
    return "\n".join(lines)


def _discovery_results_kb(candidates: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, item in enumerate(candidates[:10]):
        email = item.get("email", "")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{idx + 1}. {email[:45]}",
                    callback_data=f"discovery_toggle:{idx}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Confirmar selección ✅", callback_data="discovery_confirm"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="Volver al menú", callback_data="discovery_back")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_bank_selection_prompt(
    *, bot: Bot, chat_id: int, user_id: uuid.UUID, redis
) -> None:
    """Send the post-OAuth Gmail onboarding root menu."""
    state = await gmail_onboarding.get(user_id, redis)
    if state is None:
        log.warning("send_bank_selection_prompt: no state for user=%s", user_id)
        return
    if state.state != "gmail_onboarding_root":
        try:
            state = await gmail_onboarding.transition(
                user_id=user_id, to="gmail_onboarding_root", redis=redis
            )
        except Exception:
            log.exception("transition to gmail_onboarding_root failed")
            return
    sent = await bot.send_message(
        chat_id=chat_id,
        text=_root_text(),
        reply_markup=_root_kb(can_finish=bool(state.pending_senders)),
    )
    await gmail_onboarding.set_selection_message_id(
        user_id=user_id, message_id=sent.message_id, redis=redis
    )


async def _send_root_menu(
    *,
    message: Message,
    user_id: uuid.UUID,
    redis,
    text: str | None = None,
) -> None:
    state = await gmail_onboarding.get(user_id, redis)
    if state is None:
        return
    await message.answer(
        text or _root_text(),
        reply_markup=_root_kb(can_finish=bool(state.pending_senders)),
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
            source_groups = [
                (
                    "Cargados manual",
                    {wl.SOURCE_CUSTOM, wl.SOURCE_MANUAL_WITH_BANK_HINT},
                ),
                ("Descubiertos por keyword", {wl.SOURCE_DISCOVERED}),
                ("Histórico (preset antiguo)", {wl.SOURCE_PRESET}),
                ("Importados", {wl.SOURCE_IMPORTED}),
            ]
            rendered_ids: set[uuid.UUID] = set()
            for title, sources in source_groups:
                group = [s for s in senders if s.source in sources]
                if not group:
                    continue
                lines.append(f"\n<b>{title}</b>")
                for s in group:
                    rendered_ids.add(s.id)
                    bank = f" — {s.bank_name}" if s.bank_name else ""
                    lines.append(f"• <code>{s.sender_email}</code>{bank}")
            leftovers = [s for s in senders if s.id not in rendered_ids]
            if leftovers:
                lines.append("\n<b>Otros</b>")
                for s in leftovers:
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
            add_only=True,
        )
        await gmail_onboarding.transition(
            user_id=user.id, to="gmail_onboarding_root", redis=redis
        )
        await _send_root_menu(
            message=message,
            user_id=user.id,
            redis=redis,
            text=messages_es.GMAIL_ADD_BANK_ENTRY,
        )
    finally:
        await db.close()


@router.message(Command("discovery"))
async def on_discovery_shortcut(message: Message) -> None:
    resolved = await _resolve_user(message)
    if resolved is None:
        return
    user, db = resolved
    try:
        cred = await _get_credential(user.id, db)
        if cred is None or cred.revoked_at is not None:
            await message.answer(messages_es.GMAIL_STATUS_DISCONNECTED)
            return
        redis = get_redis()
        await gmail_onboarding.begin(
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            redis=redis,
            add_only=cred.activated_at is not None,
        )
        await gmail_onboarding.transition(
            user_id=user.id, to="selecting_keywords", redis=redis
        )
        await message.answer(
            messages_es.GMAIL_DISCOVERY_KEYWORD_PROMPT,
            reply_markup=_keyword_kb([]),
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


# ── Gmail onboarding root + Mode A / Mode B ─────────────────────────────────


@router.callback_query(F.data.startswith("gmail_root:"))
async def on_gmail_root_callback(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return

    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None:
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return

    if action == "back":
        state = await gmail_onboarding.transition(
            user_id=user.id, to="gmail_onboarding_root", redis=redis
        )
        await cb.message.answer(
            _root_text(),
            reply_markup=_root_kb(can_finish=bool(state.pending_senders)),
        )
        await cb.answer()
        return

    if action == "bank":
        await gmail_onboarding.transition(
            user_id=user.id, to="selecting_bank_from_list", redis=redis
        )
        await cb.message.answer(
            "Elegí el banco para cargar el sender exacto.",
            reply_markup=_bank_directory_kb(),
        )
        await cb.answer()
        return

    if action == "discovery":
        await gmail_onboarding.transition(
            user_id=user.id, to="selecting_keywords", redis=redis
        )
        await gmail_onboarding.set_keywords(user_id=user.id, keywords=[], redis=redis)
        await cb.message.answer(
            messages_es.GMAIL_DISCOVERY_KEYWORD_PROMPT,
            reply_markup=_keyword_kb([]),
        )
        await cb.answer()
        return

    if action == "done":
        await _finish_from_root(cb=cb, user=user, redis=redis)
        return

    await cb.answer()


async def _finish_from_root(*, cb: CallbackQuery, user: User, redis) -> None:
    state = await gmail_onboarding.get(user.id, redis)
    if state is None:
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.pending_senders:
        await cb.answer(
            "No agregaste ningún correo. Sin eso no puedo escanear nada.",
            show_alert=True,
        )
        return

    if state.add_only:
        await gmail_onboarding.clear(user.id, redis)
        await cb.message.answer(
            "Listo. Agregué esos correos. Podés forzar una revisión con "
            "/revisar_correos o esperar la corrida automática."
        )
        await cb.answer()
        return

    await gmail_onboarding.transition(user_id=user.id, to="confirming", redis=redis)
    lines = []
    for entry in state.pending_senders:
        bank = entry.get("bank_name")
        suffix = f" ({bank})" if bank else ""
        lines.append(f"• <code>{entry['email']}</code>{suffix}")
    await cb.message.answer(
        messages_es.GMAIL_BANK_CONFIRM_TPL.format(lines="\n".join(lines)),
        reply_markup=_confirm_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bank_select:"))
async def on_bank_select(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    slug = cb.data.split(":", 1)[1]
    await _handle_bank_select(cb, slug)


async def _handle_bank_select(cb: CallbackQuery, slug: str) -> None:
    if cb.from_user is None or cb.message is None:
        return
    bank = bank_by_slug(slug)
    if bank is None:
        await cb.answer("Banco desconocido", show_alert=True)
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state not in {
        "selecting_bank_from_list",
        "selecting_banks",
    }:
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return

    await gmail_onboarding.set_bank_context(
        user_id=user.id, bank_slug=slug, bank_name=bank["name"], redis=redis
    )
    await gmail_onboarding.transition(
        user_id=user.id, to="entering_sender_for_bank", redis=redis
    )
    await cb.message.answer(
        messages_es.GMAIL_BANK_GUIDED_ASK_EMAIL.format(bank=bank["name"])
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bank_preset:"))
async def on_bank_preset_tap(cb: CallbackQuery) -> None:
    """Backward-compatible alias for old Phase 6b callback data."""
    if cb.data is None:
        return
    await _handle_bank_select(cb, cb.data.split(":", 1)[1])


async def _is_entering_sender_for_bank(message: Message) -> bool:
    if message.from_user is None or not message.text or message.text.startswith("/"):
        return False
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return False
        state = await gmail_onboarding.get(user.id, redis=get_redis())
        return state is not None and state.state == "entering_sender_for_bank"
    finally:
        await db.close()


@router.message(F.text, _is_entering_sender_for_bank)
async def on_guided_sender_email(message: Message) -> None:
    if message.from_user is None or not message.text:
        return
    email = message.text.strip()
    if not _EMAIL_RE.match(email):
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
    if state is None or state.state != "entering_sender_for_bank":
        return
    norm = wl.normalize_email(email)
    await gmail_onboarding.set_pending_sender_email(
        user_id=user.id, email=norm, redis=redis
    )

    slug = state.selected_bank_slug
    bank_name = state.selected_bank_name or bank_name_for_slug(slug) or "ese banco"
    if not match_bank_by_hint(norm, slug):
        await gmail_onboarding.transition(
            user_id=user.id, to="sender_hint_override", redis=redis
        )
        await message.answer(
            messages_es.GMAIL_BANK_HINT_WARNING.format(
                email=norm, bank=bank_name
            ),
            reply_markup=_hint_override_kb(),
        )
        return

    await gmail_onboarding.transition(
        user_id=user.id, to="test_scan_prompt", redis=redis
    )
    await message.answer(
        messages_es.GMAIL_TEST_SCAN_PROMPT.format(email=norm),
        reply_markup=_test_scan_kb(),
    )


@router.callback_query(F.data.startswith("bank_hint_override:"))
async def on_bank_hint_override(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "sender_hint_override":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if action == "no":
        await gmail_onboarding.set_pending_sender_email(
            user_id=user.id, email=None, redis=redis
        )
        await gmail_onboarding.transition(
            user_id=user.id, to="entering_sender_for_bank", redis=redis
        )
        bank_name = state.selected_bank_name or "ese banco"
        await cb.message.answer(
            messages_es.GMAIL_BANK_GUIDED_ASK_EMAIL.format(bank=bank_name)
        )
        await cb.answer()
        return

    await gmail_onboarding.transition(
        user_id=user.id, to="test_scan_prompt", redis=redis
    )
    await cb.message.answer(
        messages_es.GMAIL_TEST_SCAN_PROMPT.format(email=state.pending_sender_email),
        reply_markup=_test_scan_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bank_test:"))
async def on_bank_test_scan_choice(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "test_scan_prompt":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.pending_sender_email:
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return

    if action == "no":
        await _commit_guided_sender(user=user, redis=redis)
        await cb.message.answer(messages_es.GMAIL_BANK_GUIDED_ADDED)
        await _send_root_after_callback(cb=cb, user_id=user.id, redis=redis)
        await cb.answer()
        return

    await cb.message.answer("Haciendo un scan rápido de los últimos 7 días…")
    db = AsyncSessionLocal()
    try:
        result = await discovery_svc.discover_senders(
            user.id,
            [state.pending_sender_email],
            days=7,
            max_messages=10,
            db=db,
            enforce_cooldown=False,
        )
    finally:
        await db.close()
    count = sum(s.count for s in result.senders)
    if count <= 0:
        await gmail_onboarding.transition(
            user_id=user.id, to="test_scan_zero", redis=redis
        )
        await cb.message.answer(
            messages_es.GMAIL_TEST_SCAN_ZERO.format(email=state.pending_sender_email),
            reply_markup=_zero_scan_kb(),
        )
        await cb.answer()
        return

    await _commit_guided_sender(user=user, redis=redis)
    await cb.message.answer(
        messages_es.GMAIL_TEST_SCAN_FOUND.format(
            count=count, email=state.pending_sender_email
        )
    )
    await _send_root_after_callback(cb=cb, user_id=user.id, redis=redis)
    await cb.answer()


@router.callback_query(F.data.startswith("bank_test_zero:"))
async def on_bank_test_zero(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    action = cb.data.split(":", 1)[1]
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "test_scan_zero":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if action == "retry":
        await gmail_onboarding.set_pending_sender_email(
            user_id=user.id, email=None, redis=redis
        )
        await gmail_onboarding.transition(
            user_id=user.id, to="entering_sender_for_bank", redis=redis
        )
        bank_name = state.selected_bank_name or "ese banco"
        await cb.message.answer(
            messages_es.GMAIL_BANK_GUIDED_ASK_EMAIL.format(bank=bank_name)
        )
        await cb.answer()
        return
    await _commit_guided_sender(user=user, redis=redis)
    await cb.message.answer(messages_es.GMAIL_BANK_GUIDED_ADDED)
    await _send_root_after_callback(cb=cb, user_id=user.id, redis=redis)
    await cb.answer()


async def _commit_guided_sender(*, user: User, redis) -> None:
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or not state.pending_sender_email:
        return
    bank_slug = state.selected_bank_slug
    source = wl.SOURCE_MANUAL_WITH_BANK_HINT
    db = AsyncSessionLocal()
    try:
        await wl.add_sender(
            db=db,
            user_id=user.id,
            sender_email=state.pending_sender_email,
            bank_name=None if bank_slug == "other" else bank_slug,
            source=source,
        )
        await db.commit()
    finally:
        await db.close()
    await gmail_onboarding.add_pending_sender(
        user_id=user.id,
        email=state.pending_sender_email,
        bank_name=None if bank_slug == "other" else bank_slug,
        source=source,
        redis=redis,
    )
    await gmail_onboarding.transition(
        user_id=user.id, to="gmail_onboarding_root", redis=redis
    )


async def _send_root_after_callback(*, cb: CallbackQuery, user_id: uuid.UUID, redis) -> None:
    state = await gmail_onboarding.get(user_id, redis)
    await cb.message.answer(
        "¿Querés agregar otro banco o hacer discovery?",
        reply_markup=_root_kb(can_finish=bool(state and state.pending_senders)),
    )


async def _is_selecting_keywords(message: Message) -> bool:
    if message.from_user is None or not message.text or message.text.startswith("/"):
        return False
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return False
        state = await gmail_onboarding.get(user.id, redis=get_redis())
        return state is not None and state.state == "selecting_keywords"
    finally:
        await db.close()


@router.message(F.text, _is_selecting_keywords)
async def on_custom_keyword(message: Message) -> None:
    if message.from_user is None or not message.text:
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
    if state is None or state.state != "selecting_keywords":
        return
    keywords = discovery_svc.normalize_keywords(
        state.selected_keywords + [message.text]
    )
    await gmail_onboarding.set_keywords(user_id=user.id, keywords=keywords, redis=redis)
    await message.answer(
        messages_es.GMAIL_DISCOVERY_KEYWORD_PROMPT,
        reply_markup=_keyword_kb(keywords),
    )


@router.callback_query(F.data.startswith("kw_toggle:"))
async def on_keyword_toggle(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_keywords":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    idx = int(cb.data.split(":", 1)[1])
    if idx < 0 or idx >= len(_DISCOVERY_KEYWORDS):
        await cb.answer()
        return
    kw = _DISCOVERY_KEYWORDS[idx]
    selected = list(state.selected_keywords)
    keys = {item.casefold(): item for item in selected}
    if kw.casefold() in keys:
        selected = [item for item in selected if item.casefold() != kw.casefold()]
    else:
        if len(selected) >= 5:
            await cb.answer("Máximo 5 keywords.", show_alert=True)
            return
        selected.append(kw)
    selected = discovery_svc.normalize_keywords(selected)
    await gmail_onboarding.set_keywords(user_id=user.id, keywords=selected, redis=redis)
    await cb.message.edit_reply_markup(reply_markup=_keyword_kb(selected))
    await cb.answer()


@router.callback_query(F.data == "kw_cancel")
async def on_keyword_cancel(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer()
        return
    redis = get_redis()
    state = await gmail_onboarding.transition(
        user_id=user.id, to="gmail_onboarding_root", redis=redis
    )
    await cb.message.answer(
        _root_text(),
        reply_markup=_root_kb(can_finish=bool(state.pending_senders)),
    )
    await cb.answer()


@router.callback_query(F.data == "kw_search")
async def on_keyword_search(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "selecting_keywords":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.selected_keywords:
        await cb.answer("Elegí al menos una keyword.", show_alert=True)
        return
    await gmail_onboarding.transition(
        user_id=user.id, to="discovery_running", redis=redis
    )
    await cb.message.answer("Buscando senders en Gmail…")
    asyncio.create_task(_run_discovery_safe(user_id=user.id, chat_id=cb.message.chat.id))
    await cb.answer()


async def _run_discovery_safe(*, user_id: uuid.UUID, chat_id: int) -> None:
    bot = get_bot()
    redis = get_redis()
    state = await gmail_onboarding.get(user_id, redis)
    if state is None:
        return
    db = AsyncSessionLocal()
    try:
        try:
            result = await discovery_svc.discover_senders(
                user_id,
                state.selected_keywords,
                days=30,
                max_messages=settings.gmail_discovery_max_messages,
                db=db,
            )
        except discovery_svc.DiscoveryRateLimited as exc:
            minutes = max(1, (exc.ttl_s + 59) // 60)
            await bot.send_message(
                chat_id=chat_id,
                text=f"Ya hiciste un discovery hace poco. Probá en {minutes} minutos.",
            )
            await gmail_onboarding.transition(
                user_id=user_id, to="selecting_keywords", redis=redis
            )
            return

        await gmail_onboarding.transition(
            user_id=user_id, to="discovery_results", redis=redis
        )
        candidates = [s.model_dump() for s in result.senders]
        await gmail_onboarding.set_discovery_results(
            user_id=user_id,
            candidates=candidates,
            run_id=str(result.run_id) if result.run_id else None,
            redis=redis,
        )
        await bot.send_message(
            chat_id=chat_id,
            text=_discovery_results_text(
                keywords=result.keywords, candidates=candidates, selected=[]
            ),
            reply_markup=_discovery_results_kb(candidates),
        )
    except Exception:
        log.exception("gmail_discovery_safe_failed user=%s", user_id)
        await bot.send_message(
            chat_id=chat_id,
            text="Algo falló haciendo discovery. Probá de nuevo en un rato.",
        )
    finally:
        await db.close()


@router.callback_query(F.data.startswith("discovery_toggle:"))
async def on_discovery_toggle(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    idx = int(cb.data.split(":", 1)[1])
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "discovery_results":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if idx < 0 or idx >= len(state.discovery_candidates):
        await cb.answer()
        return
    state = await gmail_onboarding.toggle_discovery_index(
        user_id=user.id, index=idx, redis=redis
    )
    await cb.message.edit_text(
        _discovery_results_text(
            keywords=state.selected_keywords,
            candidates=state.discovery_candidates,
            selected=state.discovery_selected_indices,
        ),
        reply_markup=_discovery_results_kb(state.discovery_candidates),
    )
    await cb.answer()


@router.callback_query(F.data == "discovery_back")
async def on_discovery_back(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer()
        return
    redis = get_redis()
    state = await gmail_onboarding.transition(
        user_id=user.id, to="gmail_onboarding_root", redis=redis
    )
    await cb.message.answer(
        _root_text(),
        reply_markup=_root_kb(can_finish=bool(state.pending_senders)),
    )
    await cb.answer()


@router.callback_query(F.data == "discovery_confirm")
async def on_discovery_confirm(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    user = await _resolve_user_for_callback(cb)
    if user is None:
        await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
        return
    redis = get_redis()
    state = await gmail_onboarding.get(user.id, redis)
    if state is None or state.state != "discovery_results":
        await cb.answer(messages_es.GMAIL_ONBOARDING_NOT_IN_FLOW)
        return
    if not state.discovery_selected_indices:
        await cb.answer("Tenés que marcar al menos uno, o Volver.", show_alert=True)
        return

    selected = [
        state.discovery_candidates[i]
        for i in state.discovery_selected_indices
        if 0 <= i < len(state.discovery_candidates)
    ]
    db = AsyncSessionLocal()
    try:
        for item in selected:
            await wl.add_sender(
                db=db,
                user_id=user.id,
                sender_email=item["email"],
                bank_name=None,
                source=wl.SOURCE_DISCOVERED,
            )
            await gmail_onboarding.add_pending_sender(
                user_id=user.id,
                email=item["email"],
                bank_name=None,
                source=wl.SOURCE_DISCOVERED,
                redis=redis,
            )
        if state.discovery_run_id:
            await discovery_svc.record_confirmed_senders(
                db=db,
                run_id=uuid.UUID(state.discovery_run_id),
                confirmed=[discovery_svc.SenderCandidate(**item) for item in selected],
            )
        await db.commit()
    finally:
        await db.close()

    await gmail_onboarding.transition(
        user_id=user.id, to="gmail_onboarding_root", redis=redis
    )
    await cb.message.answer(
        f"Agregué {len(selected)} correos. ¿Querés hacer otra búsqueda o cargar más bancos?",
        reply_markup=_root_kb(can_finish=True),
    )
    await cb.answer()


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


# ── selecting_banks: custom email text ───────────────────────────────────────


@router.message(F.text, _is_selecting_banks)
async def on_custom_email(message: Message) -> None:
    """Receive an email address while in selecting_banks.

    Two paths converge here:
      1. The user typed an email cold (no preset tapped). We store it
         without bank inference and record `source='custom_typed'`.
      2. The user tapped a preset earlier (e.g. BAC) and we set
         `awaiting_bank`. We use that bank name verbatim and record
         `source='manual_with_bank_hint'`. Inference is skipped — the user's
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
        source = wl.SOURCE_MANUAL_WITH_BANK_HINT
    else:
        bank_name = None
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
