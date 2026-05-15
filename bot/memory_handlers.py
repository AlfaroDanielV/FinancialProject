"""Telegram handlers for the Phase 6c B7 memory commands.

    /memoria         — list active insights, grouped, in plain Spanish.
    /olvidar [arg]   — menu OR group view OR `/olvidar todo` shortcut.
                       All paths end in a confirmation tap before delete.
    /editar_memoria  — pick an editable insight, reply with a correction,
                       Haiku parses to schema, persisted with user_locked=true.

The router MUST be registered before the main `handlers.router` so the
state-aware text handler here gets first crack at user replies during
the /editar_memoria flow. See `bot/handlers.py::register`.

Hard rules baked in:
- LLM never decides what to delete or what types exist. The user picks.
- /editar_memoria only edits `USER_EDITABLE_TYPES` (computed types are
  off-limits — the user can /olvidar them, not rewrite them).
- /olvidar todo requires TWO confirmations.
- On every action we write a typed audit row.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import AsyncIterator, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.database import AsyncSessionLocal
from api.models.user_insight import UserInsight
from api.redis_client import get_redis
from api.schemas.insights import USER_EDITABLE_TYPES
from api.services.insights.editor_parser import (
    EditorParseError,
    log_editor_run,
    parse_edit,
)
from api.services.insights.extractor import get_insight_llm_client
from api.services.insights.memory_view import (
    GROUP_BANDERAS,
    GROUP_CONOZCO,
    GROUP_LABELS_ES,
    GROUP_METAS,
    GROUP_ORDER,
    GROUP_PATRONES,
    delete_insights,
    emit_locked_audit,
    group_for,
    load_active_insights,
    render_group_summary_lines,
    render_memoria_text,
    render_view,
)
from api.services.insights.nightly_runner import (
    NightlyRunStats,
    run_nightly_for_user,
)
from api.services.insights.persister import persist_insights
from app.queries.tools.user_context import _describe_insight
from api.schemas.insights import validate_insight_content

from . import messages_es
from .delivery_send import send_chunked
from .redis_keys import (
    MEMORY_EDIT_TTL_S,
    RECALC_COOLDOWN_S,
    memory_edit_key,
    recalc_cooldown_key,
)
from .user_resolver import user_by_telegram_id


log = logging.getLogger("bot.memory_handlers")


router = Router(name="phase6c_memory")


# Map a `/olvidar <arg>` to either a group key or insight_type.
_VALID_ARG_GROUPS = {GROUP_PATRONES, GROUP_METAS, GROUP_CONOZCO, GROUP_BANDERAS}
_ARG_ALIASES: dict[str, str] = {
    "patrones": GROUP_PATRONES,
    "metas": GROUP_METAS,
    "conozco": GROUP_CONOZCO,
    "banderas": GROUP_BANDERAS,
}


_MAX_EDIT_ATTEMPTS = 3


# ── memory edit state ───────────────────────────────────────────────────────


@dataclass
class MemoryEditState:
    insight_id: str
    insight_type: str
    attempts: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "MemoryEditState":
        data = json.loads(raw)
        return cls(
            insight_id=str(data["insight_id"]),
            insight_type=str(data["insight_type"]),
            attempts=int(data.get("attempts", 0)),
        )


async def _save_edit_state(
    *, user_id: uuid.UUID, state: MemoryEditState, redis
) -> None:
    await redis.setex(
        memory_edit_key(user_id), MEMORY_EDIT_TTL_S, state.to_json()
    )


async def _load_edit_state(
    *, user_id: uuid.UUID, redis
) -> Optional[MemoryEditState]:
    raw = await redis.get(memory_edit_key(user_id))
    if not raw:
        return None
    try:
        return MemoryEditState.from_json(raw)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


async def _clear_edit_state(*, user_id: uuid.UUID, redis) -> None:
    await redis.delete(memory_edit_key(user_id))


# Public helper so /cancel in the main router can clear our state too.
async def clear_memory_edit_state(*, user_id: uuid.UUID, redis) -> None:
    await _clear_edit_state(user_id=user_id, redis=redis)


# ── filter: are we waiting for an /editar_memoria reply? ─────────────────────


async def _is_in_memory_edit(message: Message) -> bool:
    """True iff there's a pending /editar_memoria state for this user.

    Same DB-resolve cost as `_is_selecting_banks`. We accept it because
    the alternative (key by telegram_user_id) splits state ownership
    across two key namespaces — nuance not worth the small win.
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
        state = await _load_edit_state(user_id=user.id, redis=get_redis())
        return state is not None
    finally:
        await db.close()


# ── send helper ──────────────────────────────────────────────────────────────


async def _send(
    message: Message, text: str, *, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    await send_chunked(message, text, reply_markup=kb)


# ── /memoria ────────────────────────────────────────────────────────────────


@router.message(Command("memoria"))
async def on_memoria(message: Message) -> None:
    if message.from_user is None:
        return
    db = AsyncSessionLocal()
    deep_link_url: Optional[str] = None
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return
        rows = await load_active_insights(db, user_id=user.id)
        view = render_view(rows)
        text = render_memoria_text(view, footer=messages_es.MEMORIA_FOOTER)
        # Phase 6e B12: offer the SPA "Editar en SPA" deep link.
        from .deep_link import mint_edit_session_url

        deep_link_url = await mint_edit_session_url(
            db, user_id=user.id, target_path="/memoria"
        )
    finally:
        await db.close()
    kb: Optional[InlineKeyboardMarkup] = None
    if deep_link_url:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Editar en SPA", url=deep_link_url)]
            ]
        )
    await _send(message, text, kb=kb)


# ── /olvidar ────────────────────────────────────────────────────────────────


@router.message(Command("olvidar"))
async def on_olvidar(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return

        arg = (command.args or "").strip().lower()
        if arg in {"todo", "todos", "all"}:
            await _send_olvidar_all_confirm(message, user.id, db)
            return
        if arg:
            group_key = _ARG_ALIASES.get(arg)
            if group_key is None and arg in _VALID_ARG_GROUPS:
                group_key = arg
            if group_key is not None:
                await _send_olvidar_group(message, user.id, db, group_key)
                return
            # Unknown arg → fall through to top menu.
        await _send_olvidar_menu(message, user.id, db)
    finally:
        await db.close()


async def _send_olvidar_menu(
    message: Message, user_id: uuid.UUID, db: AsyncSession
) -> None:
    rows = await load_active_insights(db, user_id=user_id)
    if not rows:
        await message.answer(messages_es.OLVIDAR_TODO_NOTHING)
        return
    view = render_view(rows)
    buttons: list[list[InlineKeyboardButton]] = []
    for group_key in GROUP_ORDER:
        items = view.groups.get(group_key) or []
        if not items:
            continue
        label = f"{GROUP_LABELS_ES[group_key]} ({len(items)})"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"mem:grp:{group_key}"
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text=messages_es.OLVIDAR_BUTTON_TODO,
                callback_data="mem:olv:all:0",
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton(text="Cancelar", callback_data="mem:cancel")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _send(message, messages_es.OLVIDAR_MENU_HEADER, kb=kb)


async def _send_olvidar_group(
    message: Message,
    user_id: uuid.UUID,
    db: AsyncSession,
    group_key: str,
) -> None:
    rows = await load_active_insights(db, user_id=user_id)
    view = render_view(rows)
    items = view.groups.get(group_key) or []
    if not items:
        await message.answer(messages_es.OLVIDAR_GROUP_EMPTY)
        return
    buttons: list[list[InlineKeyboardButton]] = []
    for item in items:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=item.short_label,
                    callback_data=f"mem:olv:one:{item.insight_id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="Cancelar", callback_data="mem:cancel")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = messages_es.OLVIDAR_GROUP_HEADER_TPL.format(
        label=GROUP_LABELS_ES[group_key]
    )
    await _send(message, text, kb=kb)


async def _send_olvidar_all_confirm(
    message: Message, user_id: uuid.UUID, db: AsyncSession
) -> None:
    rows = await load_active_insights(db, user_id=user_id)
    if not rows:
        await message.answer(messages_es.OLVIDAR_TODO_NOTHING)
        return
    view = render_view(rows)
    summary = "\n".join(render_group_summary_lines(view))
    text = messages_es.OLVIDAR_TODO_CONFIRM_1_TPL.format(
        count=view.total, lines=summary
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=messages_es.OLVIDAR_TODO_CONFIRM_1_YES,
                    callback_data="mem:olv:all:1",
                ),
                InlineKeyboardButton(
                    text="Cancelar", callback_data="mem:cancel"
                ),
            ]
        ]
    )
    await _send(message, text, kb=kb)


# ── /olvidar callbacks ──────────────────────────────────────────────────────


@router.callback_query(F.data == "mem:cancel")
async def on_mem_cancel(cb: CallbackQuery) -> None:
    if cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover - best effort
            pass
        await cb.message.answer(messages_es.OLVIDAR_CANCELLED)
    await cb.answer()


@router.callback_query(F.data.startswith("mem:grp:"))
async def on_mem_group(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    group_key = cb.data.split(":", 2)[2]
    if group_key not in _VALID_ARG_GROUPS:
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
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover
            pass
        await _send_olvidar_group(cb.message, user.id, db, group_key)
    finally:
        await db.close()
    await cb.answer()


@router.callback_query(F.data.startswith("mem:olv:one:"))
async def on_mem_olv_one(cb: CallbackQuery) -> None:
    """Two clicks behind one prefix:
    - mem:olv:one:<uuid>          → show confirmation
    - mem:olv:one:yes:<uuid>      → execute delete
    """
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer()
        return
    is_confirm = parts[3] == "yes"
    insight_id_raw = parts[4] if is_confirm else parts[3]
    try:
        insight_id = uuid.UUID(insight_id_raw)
    except ValueError:
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

        # Cross-user safety: confirm the row belongs to this user before
        # we render it or delete it. Returns 404-ish soft message otherwise.
        row = await db.execute(
            select(UserInsight)
            .where(UserInsight.id == insight_id)
            .where(UserInsight.user_id == user.id)
        )
        insight = row.scalar_one_or_none()
        if insight is None:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            await cb.message.answer(messages_es.OLVIDAR_SINGLE_NOT_FOUND)
            await cb.answer()
            return

        if not is_confirm:
            try:
                content = validate_insight_content(insight.content)
                bullet = f"• {_describe_insight(content)}"
            except Exception:
                bullet = f"• [{insight.insight_type}]"
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=messages_es.OLVIDAR_SINGLE_CONFIRM_YES,
                            callback_data=f"mem:olv:one:yes:{insight.id}",
                        ),
                        InlineKeyboardButton(
                            text="Cancelar",
                            callback_data="mem:cancel",
                        ),
                    ]
                ]
            )
            await cb.message.answer(
                messages_es.OLVIDAR_SINGLE_CONFIRM_TPL.format(bullet=bullet),
                reply_markup=kb,
            )
            await cb.answer()
            return

        # Execute delete.
        deleted = await delete_insights(
            db,
            user_id=user.id,
            insight_ids=[insight.id],
            deletion_reason="user_olvidar",
        )
        await db.commit()
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover
            pass
        if deleted:
            await cb.message.answer(messages_es.OLVIDAR_SINGLE_DONE)
        else:
            await cb.message.answer(messages_es.OLVIDAR_SINGLE_NOT_FOUND)
    finally:
        await db.close()
    await cb.answer()


@router.callback_query(F.data.startswith("mem:olv:all:"))
async def on_mem_olv_all(cb: CallbackQuery) -> None:
    """mem:olv:all:0 — initial (came from menu); show first confirm.
    mem:olv:all:1 — first confirm tapped; show second confirm.
    mem:olv:all:2 — second confirm tapped; execute bulk delete.
    """
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    step = cb.data.rsplit(":", 1)[-1]
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=cb.from_user.id, db=db
        )
        if user is None:
            await cb.answer(messages_es.PAIR_PROMPT, show_alert=True)
            return

        if step == "0":
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            await _send_olvidar_all_confirm(cb.message, user.id, db)
            await cb.answer()
            return

        if step == "1":
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=messages_es.OLVIDAR_TODO_CONFIRM_2_YES,
                            callback_data="mem:olv:all:2",
                        ),
                        InlineKeyboardButton(
                            text="Cancelar",
                            callback_data="mem:cancel",
                        ),
                    ]
                ]
            )
            await cb.message.answer(
                messages_es.OLVIDAR_TODO_CONFIRM_2, reply_markup=kb
            )
            await cb.answer()
            return

        if step == "2":
            deleted = await delete_insights(
                db,
                user_id=user.id,
                deletion_reason="user_olvidar_todo",
            )
            await db.commit()
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            if deleted:
                await cb.message.answer(
                    messages_es.OLVIDAR_TODO_DONE_TPL.format(count=deleted)
                )
            else:
                await cb.message.answer(messages_es.OLVIDAR_TODO_NOTHING)
            await cb.answer()
            return

        # Unknown step.
        await cb.answer()
    finally:
        await db.close()


# ── /editar_memoria ─────────────────────────────────────────────────────────


@router.message(Command("editar_memoria"))
async def on_editar_memoria(message: Message) -> None:
    if message.from_user is None:
        return
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return

        rows = await load_active_insights(
            db, user_id=user.id, insight_types=USER_EDITABLE_TYPES
        )
        if not rows:
            await message.answer(messages_es.EDITAR_MEMORIA_EMPTY)
            return

        view = render_view(rows)
        # Keep group structure for the picker, but we know all of these
        # fall in metas + conozco. Render in deterministic order.
        buttons: list[list[InlineKeyboardButton]] = []
        seen_ids: set[uuid.UUID] = set()
        for group_key in GROUP_ORDER:
            for item in view.groups.get(group_key) or []:
                if not item.editable or item.insight_id in seen_ids:
                    continue
                seen_ids.add(item.insight_id)
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=item.short_label,
                            callback_data=f"mem:edit:{item.insight_id}",
                        )
                    ]
                )
        buttons.append(
            [InlineKeyboardButton(text="Cancelar", callback_data="mem:cancel")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await _send(message, messages_es.EDITAR_MEMORIA_LIST_HEADER, kb=kb)
    finally:
        await db.close()


@router.callback_query(F.data.startswith("mem:edit:"))
async def on_mem_edit_pick(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.data is None or cb.message is None:
        return
    insight_id_raw = cb.data.split(":", 2)[2]
    try:
        insight_id = uuid.UUID(insight_id_raw)
    except ValueError:
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

        row = await db.execute(
            select(UserInsight)
            .where(UserInsight.id == insight_id)
            .where(UserInsight.user_id == user.id)
        )
        insight = row.scalar_one_or_none()
        if insight is None or insight.insight_type not in USER_EDITABLE_TYPES:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:  # pragma: no cover
                pass
            await cb.message.answer(messages_es.OLVIDAR_SINGLE_NOT_FOUND)
            await cb.answer()
            return

        await _save_edit_state(
            user_id=user.id,
            state=MemoryEditState(
                insight_id=str(insight.id),
                insight_type=insight.insight_type,
                attempts=0,
            ),
            redis=get_redis(),
        )
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # pragma: no cover
            pass
        await cb.message.answer(messages_es.EDITAR_MEMORIA_PROMPT)
    finally:
        await db.close()
    await cb.answer()


# ── state-aware text reply for /editar_memoria ──────────────────────────────


@router.message(F.text, _is_in_memory_edit)
async def on_editar_memoria_reply(message: Message) -> None:
    """User typed a free-text correction for the insight they're editing.

    We claim this message — it does NOT fall through to the main /text
    extractor. The state filter only matches when there's an active
    edit state in Redis, so off-flow messages are unaffected.
    """
    if message.from_user is None or not message.text:
        return
    redis = get_redis()
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            return
        state = await _load_edit_state(user_id=user.id, redis=redis)
        if state is None:
            # Race against TTL or /cancel; bail without claiming.
            return

        try:
            insight_uuid = uuid.UUID(state.insight_id)
        except ValueError:
            await _clear_edit_state(user_id=user.id, redis=redis)
            return

        # Re-fetch the insight to make sure it still exists and is still
        # editable (user could have run /olvidar in another tab).
        row_q = await db.execute(
            select(UserInsight)
            .where(UserInsight.id == insight_uuid)
            .where(UserInsight.user_id == user.id)
        )
        insight = row_q.scalar_one_or_none()
        if insight is None or insight.insight_type not in USER_EDITABLE_TYPES:
            await _clear_edit_state(user_id=user.id, redis=redis)
            await message.answer(messages_es.OLVIDAR_SINGLE_NOT_FOUND)
            return

        client = get_insight_llm_client()
        success = False
        try:
            result = await parse_edit(
                target_type=state.insight_type,
                user_text=message.text,
                client=client,
                model=settings.llm_extraction_model,
            )
            success = True
        except EditorParseError as e:
            err_str = str(e)
            await _log_editor_failure(
                db=db,
                user_id=user.id,
                user_text=message.text,
                target_type=state.insight_type,
                error=err_str,
            )
            await db.commit()
            state.attempts += 1
            if err_str.startswith("transport"):
                # Network/transport failures don't burn retries — let the
                # user try again immediately without losing their slot.
                state.attempts = max(0, state.attempts - 1)
                await _save_edit_state(
                    user_id=user.id, state=state, redis=redis
                )
                await message.answer(messages_es.EDITAR_MEMORIA_PARSE_FAIL_NETWORK)
                return
            if state.attempts >= _MAX_EDIT_ATTEMPTS:
                await _clear_edit_state(user_id=user.id, redis=redis)
                await message.answer(messages_es.EDITAR_MEMORIA_PARSE_FAIL_GIVE_UP)
                return
            await _save_edit_state(user_id=user.id, state=state, redis=redis)
            await message.answer(messages_es.EDITAR_MEMORIA_PARSE_FAIL)
            return

        # Success path: persist as user_override + locked audit.
        stats = await persist_insights(
            db,
            user_id=user.id,
            insights=[result.content],
            source="user_override",
        )
        # Refresh to find the row we just upserted; need its id for the
        # locked audit.
        refreshed_q = await db.execute(
            select(UserInsight)
            .where(UserInsight.user_id == user.id)
            .where(UserInsight.insight_type == result.content.type)
            .where(UserInsight.dedup_key == result.content.dedup_key())
        )
        refreshed = refreshed_q.scalar_one_or_none()
        if refreshed is not None:
            await emit_locked_audit(
                db,
                user_id=user.id,
                insight_id=refreshed.id,
                insight_type=refreshed.insight_type,
                dedup_key=refreshed.dedup_key,
                content=refreshed.content,
            )

        await log_editor_run(
            session=db,
            user_id=user.id,
            run=result.run,
            target_type=state.insight_type,
            model=settings.llm_extraction_model,
            user_text_hash=_hash_text(message.text),
            success=True,
        )
        await db.commit()

        await _clear_edit_state(user_id=user.id, redis=redis)
        bullet = f"• {_describe_insight(result.content)}"
        await message.answer(
            messages_es.EDITAR_MEMORIA_DONE_TPL.format(bullet=bullet)
        )
        log.info(
            "editar_memoria_committed user_id=%s type=%s stats=%s",
            user.id,
            state.insight_type,
            stats,
        )
    finally:
        await db.close()


async def _log_editor_failure(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    user_text: str,
    target_type: str,
    error: str,
) -> None:
    """Record a failed parse so editor cost / failure rate is visible."""
    from api.services.insights.editor_parser import EditorParseRun

    await log_editor_run(
        session=db,
        user_id=user_id,
        run=EditorParseRun(error=error),
        target_type=target_type,
        model=settings.llm_extraction_model,
        user_text_hash=_hash_text(user_text),
        success=False,
    )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── /recalcular_memoria (Phase 6c B10) ──────────────────────────────────────


@router.message(Command("recalcular_memoria"))
async def on_recalcular_memoria(message: Message) -> None:
    """User-driven trigger for the per-user nightly recompute.

    Rate-limited to 1 manual run per hour via Redis SETNX. The full
    nightly worker covers everyone overnight; this is the "warm me up
    now" path. Fires the recompute as a background task so we can ack
    immediately with a typing indicator and follow up when done.
    """
    if message.from_user is None:
        return
    db = AsyncSessionLocal()
    try:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(messages_es.PAIR_PROMPT)
            return

        redis = get_redis()
        key = recalc_cooldown_key(user.id)
        was_set = await redis.set(
            key, "1", ex=RECALC_COOLDOWN_S, nx=True
        )
        if not was_set:
            ttl = await redis.ttl(key)
            minutes = max(1, (ttl + 59) // 60) if ttl > 0 else 60
            await message.answer(
                messages_es.RECALCULAR_MEMORIA_COOLDOWN_TPL.format(
                    minutes=minutes
                )
            )
            return

        await message.answer(messages_es.RECALCULAR_MEMORIA_STARTED)
        # Fire-and-forget. The safe runner wraps the body so an
        # exception doesn't silently disappear (project rule: no silent
        # async failures). We capture user.id and chat id by value.
        chat_id = message.chat.id
        asyncio.create_task(
            _run_recalcular_safe(user_id=user.id, chat_id=chat_id)
        )
    finally:
        await db.close()


async def _run_recalcular_safe(*, user_id: uuid.UUID, chat_id: int) -> None:
    """Background body for /recalcular_memoria. Logs + reports any error."""
    from .app import get_bot

    bot = None
    try:
        bot = get_bot()
    except Exception:
        log.exception("recalcular_get_bot_failed user_id=%s", user_id)

    try:
        async with AsyncSessionLocal() as db:
            stats: NightlyRunStats = await run_nightly_for_user(
                db, user_id=user_id
            )
            await db.commit()
        written = (
            stats.persisted_created
            + stats.persisted_updated
            + stats.persisted_reinforced
        )
        text = messages_es.RECALCULAR_MEMORIA_DONE_TPL.format(
            written=written, gaps=stats.gaps_emitted
        )
    except Exception:
        log.exception("recalcular_memoria_failed user_id=%s", user_id)
        text = messages_es.RECALCULAR_MEMORIA_FAILED

    if bot is not None:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            log.exception("recalcular_memoria_send_failed user_id=%s", user_id)
