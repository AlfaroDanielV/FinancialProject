"""Channel-agnostic message pipeline.

`process_message(user, text, ...)` is called by both the aiogram handler
(real Telegram) and the `_simulate` endpoint (tests). It implements the
full Phase 5b flow without any aiogram-specific types so the HTTP simulator
can run identical code. The return value is a `BotReply` that the caller
converts into a Telegram message (with or without inline keyboard) or a
JSON response.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.llm_extractor import (
    ExtractionResult,
    LLMClient,
    LLMClientError,
    extract_finance_intent,
)
from api.services.telegram_dispatcher import (
    AskClarification,
    ConfirmResponse,
    ProposeAction,
    Reject,
    RunQuery,
    ShowHelp,
    UndoRequest,
    dispatch,
)

from . import messages_es
from .clarification import (
    ClarificationState,
    clear_clarification,
    load_clarification,
    merge_reply,
    save_clarification,
)
from .commit import commit_pending
from .formatting import format_amount
from .pending import (
    PendingAction,
    clear_pending,
    load_pending,
    new_short_id,
    save_pending,
)
from .pending_db import (
    mark_previous_superseded,
    persist_pending_confirmation,
    resolve_from_pending,
)
from .queries import run_query
from .rate_limit import check_and_increment_rate, check_token_budget, record_token_spend
from .undo import run_undo


log = logging.getLogger("bot.pipeline")


# ── nudge callback verbs ──────────────────────────────────────────────────────
_NUDGE_VERB_ACT = "act"
_NUDGE_VERB_DISMISS = "dismiss"
_NUDGE_VERB_LATER = "later"


@dataclass
class ConfirmButton:
    """One Sí / No / Editar button. `callback_data` encodes the action's
    short_id so stale taps are rejected."""

    label: str
    callback_data: str


@dataclass
class BotReply:
    text: str
    buttons: list[ConfirmButton] = field(default_factory=list)


# ── small helpers ─────────────────────────────────────────────────────────────


_CONFIRM_YES_WORDS = frozenset({"si", "sí", "dale", "ok", "okey", "listo", "va"})
_CONFIRM_NO_WORDS = frozenset({"no", "cancelar", "cancela", "nel"})

# Commands that bypass the LLM entirely — cheaper and deterministic.
# Mirrors aiogram's Command() handlers so _simulate + real Telegram agree.
_COMMAND_HELP = {"/help", "/ayuda"}
_COMMAND_UNDO = {"/undo", "/deshacer"}
_COMMAND_CANCEL = {"/cancel", "/cancelar"}


def _today_for(user: User) -> date:
    """User's local calendar date. Falls back to UTC if timezone is bogus
    (should never happen — every 5a-migrated row has a valid tz)."""
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _cb(short_id: str, verb: str) -> str:
    return f"pending:{short_id}:{verb}"


def _buttons_for(short_id: str) -> list[ConfirmButton]:
    return [
        ConfirmButton(messages_es.CONFIRM_BUTTONS_YES, _cb(short_id, "yes")),
        ConfirmButton(messages_es.CONFIRM_BUTTONS_NO, _cb(short_id, "no")),
        ConfirmButton(messages_es.CONFIRM_BUTTONS_EDIT, _cb(short_id, "edit")),
    ]


def _text_is_confirmation(text: str) -> Optional[bool]:
    """Plain-text confirmation fallback so the user doesn't have to tap
    the keyboard if they don't want to. None = not a confirmation word."""
    t = text.strip().lower().rstrip(".!")
    if t in _CONFIRM_YES_WORDS:
        return True
    if t in _CONFIRM_NO_WORDS:
        return False
    return None


# ── main entry ────────────────────────────────────────────────────────────────


async def process_message(
    *,
    user: User,
    text: str,
    db: AsyncSession,
    redis: Redis,
    llm_client: LLMClient,
    llm_model: str,
) -> BotReply:
    """One round-trip: text in, BotReply out. Side effects (Redis writes,
    DB commits) happen as dispatcher branches fire."""

    # ── rate limit gate ──
    allowed = await check_and_increment_rate(user_id=user.id, redis=redis)
    if not allowed:
        return BotReply(text=messages_es.RATE_LIMIT_HIT)

    today = _today_for(user)

    # ── command short-circuits ──
    # Cheap deterministic routing for slash commands. Keeps /undo and /help
    # free of LLM cost and lets _simulate exercise them without a token.
    lowered = text.strip().lower()
    if lowered in _COMMAND_HELP:
        return BotReply(text=messages_es.HELP_TEXT)
    if lowered in _COMMAND_UNDO:
        await clear_clarification(user_id=user.id, redis=redis)
        _ok, msg = await run_undo(user=user, db=db, redis=redis)
        return BotReply(text=msg)
    if lowered in _COMMAND_CANCEL:
        existing_pending = await load_pending(user_id=user.id, redis=redis)
        if existing_pending is not None:
            await resolve_from_pending(
                session=db, pending=existing_pending, resolution="cancelled"
            )
            await db.commit()
        await clear_pending(user_id=user.id, redis=redis)
        await clear_clarification(user_id=user.id, redis=redis)
        return BotReply(text=messages_es.CANCELLED)

    # ── clarification round-trip ──
    # If the previous dispatch returned AskClarification, any non-command
    # reply is an answer to that question — NOT a fresh intent. Merge the
    # reply into the stashed partial and re-dispatch. See bot/clarification.py.
    pending_clarify = await load_clarification(user_id=user.id, redis=redis)
    if pending_clarify is not None:
        merged = merge_reply(pending_clarify, text, user)
        if merged is None:
            # Reply couldn't be interpreted — keep state, re-ask.
            return BotReply(text=pending_clarify.question_es)
        decision = await dispatch(
            extraction=merged, user=user, today=today, db=db
        )
        return await _apply_decision(
            user=user, decision=decision, db=db, redis=redis
        )

    # ── pending-action short-circuit ──
    # If the user has a pending proposal and typed a confirmation word,
    # skip the LLM entirely. Saves tokens and keeps the flow snappy.
    plain_confirm = _text_is_confirmation(text)
    if plain_confirm is not None:
        return await _handle_confirm(
            user=user, yes=plain_confirm, db=db, redis=redis
        )

    # ── token budget gate ──
    # Checked only for calls that will actually invoke the LLM. Confirmations
    # and (shortly) /undo shouldn't burn the user's daily budget.
    has_budget = await check_token_budget(user_id=user.id, redis=redis, today=today)
    if not has_budget:
        return BotReply(text=messages_es.DAILY_BUDGET_HIT)

    # ── extract ──
    try:
        extraction: ExtractionResult = await extract_finance_intent(
            user=user,
            text=text,
            client=llm_client,
            model=llm_model,
            db=db,
        )
    except (LLMClientError, ValidationError) as e:
        log.info("extractor_failure user_id=%s err=%s", user.id, type(e).__name__)
        return BotReply(text=messages_es.EXTRACTOR_FAILED)

    # Record approximate spend. The extractor logged exact token counts to
    # llm_extractions; we re-derive from the result's side effects via a
    # best-effort estimate here so we don't have to plumb them back up.
    # Rough: ~500 tokens per call (input + output) — close enough for a
    # budget guard that's not a quota.
    await record_token_spend(user_id=user.id, redis=redis, today=today, tokens=500)

    # ── dispatch ──
    decision = await dispatch(
        extraction=extraction, user=user, today=today, db=db
    )
    return await _apply_decision(
        user=user, decision=decision, db=db, redis=redis
    )


async def _handle_confirm(
    *,
    user: User,
    yes: bool,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    pending = await load_pending(user_id=user.id, redis=redis)
    if pending is None:
        return BotReply(text=messages_es.PENDING_NONE_TO_CONFIRM)

    if not yes:
        await resolve_from_pending(
            session=db, pending=pending, resolution="rejected"
        )
        await db.commit()
        await clear_pending(user_id=user.id, redis=redis)
        return BotReply(text=messages_es.COMMITTED_DISCARDED)

    await commit_pending(user=user, pending=pending, db=db, redis=redis)
    amt_decimal = Decimal(pending.payload["amount"])
    currency = pending.payload["currency"]
    amt_formatted = format_amount(amt_decimal, currency)
    tmpl = (
        messages_es.COMMITTED_EXPENSE
        if pending.action_type == "log_expense"
        else messages_es.COMMITTED_INCOME
    )
    return BotReply(text=tmpl.format(amount=amt_formatted))


# ── dev/smoke entry: skip LLM, inject a pre-baked ExtractionResult ──────────


async def process_mock_extraction(
    *,
    user: User,
    raw_extraction: dict,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    """Used by the /_simulate endpoint and phase5b smoke script to exercise
    the dispatcher → commit flow without spending Anthropic tokens. Not
    available in production (the endpoint gate enforces is_dev).
    """
    try:
        extraction = ExtractionResult.model_validate(raw_extraction)
    except ValidationError:
        return BotReply(text=messages_es.EXTRACTOR_FAILED)

    today = _today_for(user)
    decision = await dispatch(
        extraction=extraction, user=user, today=today, db=db
    )
    return await _apply_decision(
        user=user, decision=decision, db=db, redis=redis
    )


async def _apply_decision(
    *,
    user: User,
    decision,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    # Clarification state is tied to "we just asked a question". Any
    # decision that isn't a new question ends the clarification.
    if not isinstance(decision, AskClarification):
        await clear_clarification(user_id=user.id, redis=redis)

    if isinstance(decision, ProposeAction):
        short_id = new_short_id()
        # Mark any previously-unresolved pending_confirmations for this
        # user as 'superseded'. Whether Redis still had the prior one or
        # not is irrelevant — the DB is the audit source for Phase 5d's
        # stale_pending evaluator. Do this BEFORE inserting the new row.
        await mark_previous_superseded(session=db, user_id=user.id)
        pending = PendingAction(
            short_id=short_id,
            action_type=decision.action_type,
            payload=decision.payload,
            summary_es=decision.summary_es,
        )
        confirmation_id = await persist_pending_confirmation(
            session=db, user_id=user.id, pending=pending
        )
        pending.confirmation_id = str(confirmation_id)
        await db.commit()
        existing = await load_pending(user_id=user.id, redis=redis)
        prefix = ""
        if existing is not None:
            prefix = messages_es.PENDING_OVERWRITTEN + "\n\n"
        await save_pending(user_id=user.id, pending=pending, redis=redis)
        return BotReply(
            text=prefix + decision.summary_es,
            buttons=_buttons_for(short_id),
        )
    if isinstance(decision, AskClarification):
        await save_clarification(
            user_id=user.id,
            state=ClarificationState(
                partial=decision.partial,
                awaiting_field=decision.awaiting_field,
                question_es=decision.question_es,
            ),
            redis=redis,
        )
        return BotReply(text=decision.question_es)
    if isinstance(decision, RunQuery):
        reply = await run_query(user=user, query=decision, db=db)
        return BotReply(text=reply)
    if isinstance(decision, ConfirmResponse):
        return await _handle_confirm(
            user=user, yes=decision.yes, db=db, redis=redis
        )
    if isinstance(decision, UndoRequest):
        _ok, msg = await run_undo(user=user, db=db, redis=redis)
        return BotReply(text=msg)
    if isinstance(decision, ShowHelp):
        return BotReply(text=messages_es.HELP_TEXT)
    if isinstance(decision, Reject):
        return BotReply(text=decision.message_es)
    return BotReply(text=messages_es.HELP_TEXT)


# ── handler entry for inline-keyboard callbacks ──────────────────────────────


async def handle_pending_callback(
    *,
    user: User,
    callback_data: str,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    """Called when the user taps Sí / No / Editar. `callback_data` has the
    form `pending:<short_id>:<verb>`. Stale clicks (short_id mismatch)
    return a gentle "expired" reply."""
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "pending":
        return BotReply(text=messages_es.PENDING_NONE_TO_CONFIRM)
    _, short_id, verb = parts

    pending = await load_pending(user_id=user.id, redis=redis)
    if pending is None or pending.short_id != short_id:
        return BotReply(text=messages_es.PENDING_EXPIRED)

    if verb == "yes":
        return await _handle_confirm(user=user, yes=True, db=db, redis=redis)
    if verb == "no":
        return await _handle_confirm(user=user, yes=False, db=db, redis=redis)
    if verb == "edit":
        # Edit flow: for now, just clear and ask the user to resend. A
        # richer field-by-field edit is a follow-up; the spec marks it
        # optional under "Editar" → "ask which field to change".
        await resolve_from_pending(
            session=db, pending=pending, resolution="edited"
        )
        await db.commit()
        await clear_pending(user_id=user.id, redis=redis)
        return BotReply(text=messages_es.EDIT_PROMPT)
    return BotReply(text=messages_es.PENDING_NONE_TO_CONFIRM)


# ── nudge inline-keyboard callbacks ──────────────────────────────────────────


def _nudge_act_reply(nudge_type: str) -> str:
    if nudge_type == "missing_income":
        return messages_es.NUDGE_ACK_ACT_MISSING_INCOME
    if nudge_type == "stale_pending_confirmation":
        return messages_es.NUDGE_ACK_ACT_STALE_PENDING
    if nudge_type == "upcoming_bill":
        return messages_es.NUDGE_ACK_ACT_UPCOMING_BILL
    return messages_es.NUDGE_ACK_DISMISS_SOFT


async def handle_nudge_callback(
    *,
    user: User,
    callback_data: str,
    db: AsyncSession,
    redis: Redis,  # noqa: ARG001 — kept for symmetry + future use
) -> BotReply:
    """`nudge:<nudge_id>:<verb>`. `act` flips the nudge to acted_on and
    replies with a type-specific CTA (missing_income prompts for income,
    stale_pending arms the proposal for re-confirmation via /sí, etc.).
    `dismiss` / `later` both flip to dismissed (with silence counting);
    the user-facing text differs but the state transition is the same.

    For stale_pending_confirmation specifically: `dismiss` ALSO closes
    the linked pending_confirmations row as 'rejected' so the next
    evaluator pass doesn't re-nudge on the same proposal.
    """
    # Imports local to avoid widening the bot module's import surface —
    # the dispatcher tests imported bot.pipeline before Phase 5d existed.
    from api.services.nudges.actions import mark_acted_on, mark_dismissed
    from api.models.user_nudge import UserNudge
    from sqlalchemy import select

    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "nudge":
        return BotReply(text=messages_es.NUDGE_EXPIRED)
    _, raw_id, verb = parts
    try:
        nudge_id = uuid.UUID(raw_id)
    except ValueError:
        return BotReply(text=messages_es.NUDGE_EXPIRED)

    # Pre-check existence + ownership so we can give NUDGE_EXPIRED (soft)
    # rather than a 404 path. mark_* raises HTTPException on missing; we
    # translate it here.
    result = await db.execute(
        select(UserNudge).where(
            UserNudge.id == nudge_id, UserNudge.user_id == user.id
        )
    )
    nudge = result.scalar_one_or_none()
    if nudge is None:
        return BotReply(text=messages_es.NUDGE_EXPIRED)
    nudge_type = nudge.nudge_type

    if verb == _NUDGE_VERB_ACT:
        await mark_acted_on(db, user_id=user.id, nudge_id=nudge_id)
        # Type-specific side-effect for stale_pending: if the user chooses
        # to act on the stale reminder, we do NOT auto-resurrect the old
        # proposal — they'll retype if they still want it. That keeps the
        # transaction trail honest (the LLM re-extracts from the fresh
        # message) and avoids reviving a proposal whose context is 48h+
        # stale. The reply text nudges them to retype.
        await db.commit()
        return BotReply(text=_nudge_act_reply(nudge_type))

    if verb in (_NUDGE_VERB_DISMISS, _NUDGE_VERB_LATER):
        outcome = await mark_dismissed(db, user_id=user.id, nudge_id=nudge_id)
        # stale_pending + hard dismiss → close the linked proposal too.
        if (
            verb == _NUDGE_VERB_DISMISS
            and nudge_type == "stale_pending_confirmation"
        ):
            pending_cid = (nudge.payload or {}).get("pending_confirmation_id")
            if pending_cid:
                from .pending_db import mark_confirmation_resolved
                try:
                    cid = uuid.UUID(pending_cid)
                except (TypeError, ValueError):
                    cid = None
                if cid is not None:
                    await mark_confirmation_resolved(
                        session=db, confirmation_id=cid, resolution="rejected"
                    )
        await db.commit()
        if verb == _NUDGE_VERB_LATER:
            return BotReply(text=messages_es.NUDGE_ACK_LATER)
        if outcome.silence_created:
            return BotReply(text=messages_es.NUDGE_ACK_DISMISS_HARD)
        return BotReply(text=messages_es.NUDGE_ACK_DISMISS_SOFT)

    return BotReply(text=messages_es.NUDGE_EXPIRED)
