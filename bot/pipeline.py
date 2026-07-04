"""Channel-agnostic message pipeline.

`process_message(user, text, ...)` is called by both the aiogram handler
(real Telegram) and the `_simulate` endpoint (tests). It implements the
full Phase 5b flow without any aiogram-specific types so the HTTP simulator
can run identical code. The return value is a `BotReply` that the caller
converts into a Telegram message (with or without inline keyboard) or a
JSON response.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.queries.dispatcher import run_dispatch
from app.queries.history import append_turn as _append_query_history
from api.models.user import User
from api.models.lazy_detection_event import LazyDetectionEvent
from api.services.clock import user_today
from api.services.insights.extractor import (
    enqueue_insight_extraction,
    post_clarification_context,
)
from api.services.llm_extractor import (
    ExtractionResult,
    LLMClient,
    LLMClientError,
    extract_finance_intent,
    extract_vision,
)
from api.services.telegram_dispatcher import (
    AskClarification,
    ConfirmResponse,
    LazyDetectionPrompt,
    OpenScreenAction,
    ProposeAction,
    Reject,
    RerouteToQuery,
    ShowHelp,
    StartAccountCreation,
    UndoRequest,
    dispatch,
)

from . import messages_es
from api.services.advisory.guardrails import detect_distress

from .advisory import (
    advisory_this_turn,
    end_advisory_session,
    is_advisory_session_active,
    start_advisory_session,
)
from .account_creation import (
    AccountCreationError,
    clear_account_creation,
    handle_account_creation_reply,
    is_awaiting_start_answer,
    load_account_creation,
    parse_account_creation_start,
    save_lazy_account_creation,
    start_account_creation,
)
from .clarification import (
    ClarificationState,
    clear_clarification,
    load_clarification,
    merge_reply,
    save_clarification,
)
from .commit import commit_pending
from .formatting import format_amount
from .onboarding_welcome import build_onboarding_reply
from .pending import (
    PendingAction,
    clear_pending,
    load_last_action,
    load_pending,
    new_short_id,
    save_pending,
)
from .pending_db import (
    mark_previous_superseded,
    persist_pending_confirmation,
    resolve_from_pending,
)
from .rate_limit import check_and_increment_rate
from .redis_keys import DISTRESS_NOTE_TTL_S, distress_note_key
from app.queries.delivery import BudgetExceeded, handle_query_error
from api.services.budget import assert_within_budget
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
class UrlButton:
    """Phase 6e B12 — opens a URL in the user's browser.

    Used for `edit_session` magic-link deep links into the SPA.
    """

    label: str
    url: str


@dataclass
class OpenScreen:
    """Phase 6f debt slice — directs a native client to open `screen`
    pre-filled with `prefill`. The Telegram renderer ignores this (debt is a
    native-app feature); the native chat reads it and navigates."""

    screen: str
    prefill: dict


@dataclass
class BotReply:
    text: str
    buttons: list[ConfirmButton] = field(default_factory=list)
    url_buttons: list[UrlButton] = field(default_factory=list)
    open_screen: Optional[OpenScreen] = None
    # P10 B0.5 (R5) — machine-readable failure class so clients can render
    # distinct copy instead of one generic banner. None = a normal answer.
    # Values: "understanding" (extractor couldn't parse), "budget" (daily
    # token budget), "transient" (rate limit / retryable), "system"
    # (unexpected internal failure).
    error_class: Optional[str] = None


# ── small helpers ─────────────────────────────────────────────────────────────


_CONFIRM_YES_WORDS = frozenset({"si", "sí", "dale", "ok", "okey", "listo", "va"})
_CONFIRM_NO_WORDS = frozenset({"no", "cancelar", "cancela", "nel"})
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Commands that bypass the LLM entirely — cheaper and deterministic.
# Mirrors aiogram's Command() handlers so _simulate + real Telegram agree.
_COMMAND_HELP = {"/help", "/ayuda"}
_COMMAND_UNDO = {"/undo", "/deshacer"}
_COMMAND_CANCEL = {"/cancel", "/cancelar"}
_COMMAND_MENU = {"/menu", "/menú"}
# P10 B2: advisory continuity session entry/exit (deterministic, zero LLM).
_COMMAND_ADVISORY = {"/asesor", "/asesoria", "/asesoría"}
_COMMAND_ADVISORY_END = {"/normal", "/salir_asesor"}


def _message_hash(text: str) -> str:
    """Short stable hash for failure-class log correlation (R6). Never logs
    the message content itself — chat text can hold amounts and merchants."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _today_for(user: User) -> date:
    """User's local calendar date. Thin alias over the shared single source
    (`api.services.clock.user_today`) kept so existing callsites don't churn."""
    return user_today(user)


def _cb(short_id: str, verb: str) -> str:
    return f"pending:{short_id}:{verb}"


def _buttons_for(short_id: str) -> list[ConfirmButton]:
    return [
        ConfirmButton(messages_es.CONFIRM_BUTTONS_YES, _cb(short_id, "yes")),
        ConfirmButton(messages_es.CONFIRM_BUTTONS_NO, _cb(short_id, "no")),
        ConfirmButton(messages_es.CONFIRM_BUTTONS_EDIT, _cb(short_id, "edit")),
    ]


def _yes_no_buttons(short_id: str) -> list[ConfirmButton]:
    """Sí / No only (no Editar) — used by the reclassify proposal, which has
    nothing to edit field-by-field."""
    return [
        ConfirmButton(messages_es.CONFIRM_BUTTONS_YES, _cb(short_id, "yes")),
        ConfirmButton(messages_es.CONFIRM_BUTTONS_NO, _cb(short_id, "no")),
    ]


def _clarify_buttons(state: ClarificationState) -> list[ConfirmButton]:
    """Phase 7f — tappable clarification options (account names).

    The label is the literal answer. Native chat posts it back as text (the
    clarification branch merges it like a typed reply); Telegram taps route
    through `handle_clarify_callback` via `clarify:{nonce}:{idx}`, where the
    nonce rejects taps on a superseded question."""
    return [
        ConfirmButton(label, f"clarify:{state.nonce}:{idx}")
        for idx, label in enumerate(state.options)
    ]


def _text_is_confirmation(text: str) -> Optional[bool]:
    """Plain-text confirmation fallback. Strips emoji and punctuation so
    native-app button labels like 'Sí ✅' / 'No ❌' match the word sets."""
    t = _NON_WORD_RE.sub("", text).strip().lower()
    if t in _CONFIRM_YES_WORDS:
        return True
    if t in _CONFIRM_NO_WORDS:
        return False
    return None


# Reclassify recognizer (gasto ↔ ingreso of the LAST movement). Deterministic,
# LLM-free — the LLM never decides a reclassification. Kept deliberately TIGHT
# (full-match anchored, no bare-negation forms, digit-free) so it can't hijack a
# new capture ("ingresé 5000") or a query ("¿cuál fue mi último ingreso?"). The
# common corrective phrasings — "era/fue/es un ingreso", optionally led by a
# discourse "no," or "eso/el último/en realidad", and "cambialo a ingreso" — are
# covered; ambiguous bare negations ("no era un gasto") fall through to the LLM.
_RECLASSIFY_PREFIX = (
    r"(?:(?:eso|ese|esto|esa|el ultimo|la ultima|el movimiento|"
    r"ese movimiento|en realidad)\s+)*"
)
_RECLASSIFY_VERB = r"(?:era|fue|es)\s+(?:un\s+)?"
_RECLASSIFY_IMPER = (
    r"(?:cambialo|cambiar|cambia|reclasificalo|reclasifica|marcalo|marca|"
    r"ponelo|ponlo|pasalo)\s+(?:a|como)\s+(?:un\s+)?"
)
_RECLASSIFY_INCOME_RE = re.compile(
    rf"(?:{_RECLASSIFY_PREFIX}{_RECLASSIFY_VERB}|{_RECLASSIFY_IMPER})ingreso"
)
_RECLASSIFY_EXPENSE_RE = re.compile(
    rf"(?:{_RECLASSIFY_PREFIX}{_RECLASSIFY_VERB}|{_RECLASSIFY_IMPER})gasto"
)


def _reclassify_target(text: str) -> Optional[bool]:
    """Return True to reclassify the last movement to INCOME, False to EXPENSE,
    or None when the text isn't a reclassification phrase."""
    # Normalize: strip accents, lowercase, drop a leading discourse "no," (the
    # rejection-then-correction form — NOT a negation), replace punctuation with
    # spaces, collapse whitespace.
    t = "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()
    if any(ch.isdigit() for ch in t):
        return None
    t = re.sub(r"^\s*no\s*[,.;:!¡]+\s*", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if _RECLASSIFY_INCOME_RE.fullmatch(t):
        return True
    if _RECLASSIFY_EXPENSE_RE.fullmatch(t):
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
    image_bytes: Optional[bytes] = None,
    image_media_type: Optional[str] = None,
    vision_model: Optional[str] = None,
) -> BotReply:
    """One round-trip: text in (or image in), BotReply out.

    P10 B10 — the deterministic distress net runs PRE-LLM, for both channels:
    a genuine crisis signal short-circuits to the warm línea-1322 hand-off
    (never assessed by an LLM); financial despair flows through normally and
    gets a warm point-to-a-real-person note appended (once per day). Ordinary
    money-stress is never escalated to a crisis line (O3)."""
    tier = detect_distress(text) if image_bytes is None else None
    if tier == "crisis":
        log.info("distress_crisis_handoff user_id=%s", user.id)
        return BotReply(text=messages_es.DISTRESS_CRISIS)

    reply = await _process_message_inner(
        user=user,
        text=text,
        db=db,
        redis=redis,
        llm_client=llm_client,
        llm_model=llm_model,
        image_bytes=image_bytes,
        image_media_type=image_media_type,
        vision_model=vision_model,
    )

    if tier == "financial":
        # Warmth WITHOUT hijacking the financial answer — appended, deduped
        # per day, and best-effort (a Redis hiccup must never eat the reply).
        try:
            marked = await redis.set(
                distress_note_key(user.id), "1", ex=DISTRESS_NOTE_TTL_S, nx=True
            )
            if marked:
                log.info("distress_financial_note user_id=%s", user.id)
                reply.text = reply.text + messages_es.DISTRESS_FINANCIAL_SUFFIX
        except Exception:  # noqa: BLE001 — the note is optional; the answer is not
            log.exception("distress_note_failed user_id=%s", user.id)
    return reply


async def _process_message_inner(
    *,
    user: User,
    text: str,
    db: AsyncSession,
    redis: Redis,
    llm_client: LLMClient,
    llm_model: str,
    image_bytes: Optional[bytes] = None,
    image_media_type: Optional[str] = None,
    vision_model: Optional[str] = None,
) -> BotReply:
    """The pre-B10 pipeline body. Side effects (Redis writes, DB commits)
    happen as dispatcher branches fire.

    When `image_bytes` is provided the text pipeline branches (commands,
    account creation, clarification, pending-confirm) are skipped and vision
    extraction runs instead, routing through the same write dispatcher."""

    # ── rate limit gate ──
    allowed = await check_and_increment_rate(user_id=user.id, redis=redis)
    if not allowed:
        return BotReply(text=messages_es.RATE_LIMIT_HIT, error_class="transient")

    today = _today_for(user)

    # ── vision fast-path ──
    # When the native app uploads a receipt photo, skip the text pipeline
    # branches entirely (there is no text to parse for commands or pending
    # confirms). Budget gate still applies; write dispatcher runs as normal.
    if image_bytes is not None and image_media_type is not None:
        return await _process_image(
            user=user,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            today=today,
            db=db,
            redis=redis,
            llm_client=llm_client,
            haiku_model=llm_model,
            sonnet_model=vision_model or llm_model,
        )

    # ── command short-circuits ──
    # Cheap deterministic routing for slash commands. Keeps /undo and /help
    # free of LLM cost and lets _simulate exercise them without a token.
    lowered = text.strip().lower()
    if lowered in _COMMAND_HELP:
        reply = await build_onboarding_reply(user=user, db=db)
        return BotReply(text=reply.text)
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
        await clear_account_creation(user_id=user.id, redis=redis)
        await end_advisory_session(user_id=user.id, redis=redis)
        return BotReply(text=messages_es.CANCELLED)
    if lowered in _COMMAND_ADVISORY:
        # P10 B2: explicit entry into the advisory continuity session.
        from api.config import settings as _settings

        if not _settings.advisory_persona_enabled:
            return BotReply(text=messages_es.ADVISORY_UNAVAILABLE)
        await start_advisory_session(user_id=user.id, redis=redis)
        return BotReply(text=messages_es.ADVISORY_STARTED)
    if lowered in _COMMAND_ADVISORY_END:
        was_active = await is_advisory_session_active(user_id=user.id, redis=redis)
        await end_advisory_session(user_id=user.id, redis=redis)
        return BotReply(
            text=messages_es.ADVISORY_ENDED
            if was_active
            else messages_es.ADVISORY_NOT_ACTIVE
        )
    if lowered in _COMMAND_MENU:
        # Imported lazily so bot.menu can import the reply dataclasses from this
        # module at top level without a circular import.
        from .menu import build_menu_reply

        return build_menu_reply()
    if (
        lowered == "/resumen"
        or lowered.startswith("/resumen ")
        or lowered.startswith("/resumen_")
    ):
        from .menu import handle_resumen

        return await handle_resumen(text, user=user, db=db)
    if lowered.startswith("/"):
        # Native screen launchers (/cuentas, /movimientos, /sobres, /gmail,
        # /memoria). Imported lazily to avoid a circular import with bot.menu.
        from .menu import LAUNCHER_COMMANDS, build_launcher_reply

        if lowered in LAUNCHER_COMMANDS:
            return build_launcher_reply(lowered)

    # ── account-creation round-trip ──
    # Phase 6d B9: this conversational flow owns its replies while active,
    # including "sí" in the confirmation step. That must beat the generic
    # pending-action confirmation handler below.
    pending_account = await load_account_creation(user_id=user.id, redis=redis)
    # The lazy-detection "¿La creamos?" prompt (step=awaiting_start) is a soft
    # offer. If the user ignores it and types something else (e.g. a new
    # expense), abandon the prompt and process the new message — don't trap
    # them in the account dialog. Committed steps (asking_name/type/...) still
    # own their replies.
    if (
        pending_account is not None
        and pending_account.step == "awaiting_start"
        and not is_awaiting_start_answer(text)
    ):
        await clear_account_creation(user_id=user.id, redis=redis)
        pending_account = None
    if pending_account is not None:
        try:
            outcome = await handle_account_creation_reply(
                user=user,
                text=text,
                state=pending_account,
                db=db,
                redis=redis,
            )
        except AccountCreationError as exc:
            await clear_account_creation(user_id=user.id, redis=redis)
            return BotReply(text=str(exc))

        if outcome.account is not None and outcome.origin_extraction:
            try:
                followup_extraction = ExtractionResult.model_validate(
                    outcome.origin_extraction
                )
            except ValidationError:
                return BotReply(text=outcome.text)
            followup = await _route_extraction(
                user=user,
                text=text,
                extraction=followup_extraction,
                today=today,
                db=db,
                redis=redis,
            )
            return BotReply(
                text=f"{outcome.text}\n\n{followup.text}",
                buttons=followup.buttons,
            )
        return BotReply(text=outcome.text)

    starts_account_creation, account_name = parse_account_creation_start(text)
    if starts_account_creation:
        await clear_clarification(user_id=user.id, redis=redis)
        return BotReply(
            text=await start_account_creation(
                user_id=user.id,
                redis=redis,
                name=account_name,
            )
        )

    # ── clarification round-trip ──
    # If the previous dispatch returned AskClarification, any non-command
    # reply is an answer to that question — NOT a fresh intent. Merge the
    # reply into the stashed partial and re-dispatch. See bot/clarification.py.
    pending_clarify = await load_clarification(user_id=user.id, redis=redis)
    if pending_clarify is not None:
        merged = merge_reply(pending_clarify, text, user)
        if merged is None:
            # Reply couldn't be interpreted — keep state, re-ask (with the
            # same option buttons, if the question carried any).
            return BotReply(
                text=pending_clarify.question_es,
                buttons=_clarify_buttons(pending_clarify),
            )
        if merged.dispatcher == "query":
            log.info(
                "clarification_abandoned reason=query_dispatcher user_id=%s",
                user.id,
            )
            await clear_clarification(user_id=user.id, redis=redis)
            return await _route_extraction(
                user=user,
                text=text,
                extraction=merged,
                today=today,
                db=db,
                redis=redis,
            )
        decision = await dispatch(
            extraction=merged, user=user, today=today, db=db
        )
        reply = await _apply_decision(
            user=user, decision=decision, db=db, redis=redis, source_text=text
        )
        enqueue_insight_extraction(
            user_id=user.id,
            conversation_window=[
                {"role": "assistant", "content": pending_clarify.question_es},
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply.text},
            ],
            transaction_context=post_clarification_context(),
            source_event="post_clarification",
        )
        return reply

    # ── pending-action short-circuit ──
    # If the user has a pending proposal and typed a confirmation word,
    # skip the LLM entirely. Saves tokens and keeps the flow snappy.
    #
    # BUT only when a write proposal is actually pending. A bare "sí"/"no"
    # with nothing to confirm is almost always answering a question the
    # QUERY dispatcher just asked (e.g. "¿Querés que revise tu capacidad de
    # ahorro?"). Short-circuiting it to the write path dead-ends with "no
    # tengo nada pendiente"; instead fall through to extract → dispatch so
    # the LLM handles it with its conversation history.
    plain_confirm = _text_is_confirmation(text)
    if plain_confirm is not None:
        pending = await load_pending(user_id=user.id, redis=redis)
        if pending is not None:
            return await _handle_confirm(
                user=user, yes=plain_confirm, db=db, redis=redis, source_text=text
            )

    # ── reclassify gasto ↔ ingreso (last movement) ──
    # A deterministic corrective phrase ("eso fue un ingreso") proposes flipping
    # the sign of the LAST committed movement, with a Sí/No confirm. LLM-free —
    # the LLM never decides a reclassification. Skipped while a proposal is in
    # flight (that turn belongs to its own confirm). The native post-capture
    # "Era un ingreso" chip PATCHes directly; this is the typed-phrase path,
    # available on both channels.
    reclassify_to_income = _reclassify_target(text)
    if reclassify_to_income is not None and (
        await load_pending(user_id=user.id, redis=redis) is None
    ):
        return await _handle_reclassify(
            user=user, to_income=reclassify_to_income, db=db, redis=redis
        )

    # ── token budget gate ──
    # Source of truth: llm_extractions + llm_query_dispatches summed
    # since CR midnight (api.services.budget). Both dispatchers contribute
    # to the same daily cap. Confirmations and /undo bypass this gate
    # because they don't invoke the LLM.
    tz_name = getattr(user, "timezone", None) or "America/Costa_Rica"
    try:
        await assert_within_budget(
            user_id=user.id, db=db, tz_name=tz_name
        )
    except BudgetExceeded as e:
        # Reuse the same Spanish copy as the query dispatcher's error
        # handler so both paths look identical to the user.
        return BotReply(
            text=handle_query_error(e, user_id=user.id), error_class="budget"
        )

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
        log.info(
            "extractor_failure user_id=%s err=%s failure_class=understanding "
            "message_hash=%s",
            user.id,
            type(e).__name__,
            _message_hash(text),
        )
        return BotReply(text=messages_es.EXTRACTOR_FAILED, error_class="understanding")
    except Exception:
        # Anything else (e.g. a raw Anthropic SDK overload/429/529/BadRequest the
        # extractor didn't wrap as LLMClientError) must not bubble to the native
        # chat endpoint as a raw 500. Logged with full traceback for diagnosis.
        log.exception(
            "extractor_unexpected_failure user_id=%s failure_class=understanding "
            "message_hash=%s",
            user.id,
            _message_hash(text),
        )
        return BotReply(text=messages_es.EXTRACTOR_FAILED, error_class="understanding")

    return await _route_extraction(
        user=user,
        text=text,
        extraction=extraction,
        today=today,
        db=db,
        redis=redis,
    )


# ── Phase 7a: cross-dispatcher context bridge ───────────────────────────────
# The read-only query dispatcher (the advisor) reads `query_history` to resolve
# follow-ups like "¿cómo alcanzo esa meta?". Goal/income/bill creation runs
# through THIS write path, which historically never touched that store — so the
# advisor was blind to a goal the user had just created one turn earlier.
# Bridge the conversational creation turns (proposal + confirmation) into the
# same store so the advisor sees the recent narrative. Raw expense/income logs
# are deliberately NOT bridged: they're capture noise that would flood the
# 10-entry window and crowd out real conversation.
_BRIDGED_CREATION_ACTIONS = frozenset(
    {"create_goal", "create_income", "create_bill"}
)


async def _bridge_to_query_history(
    *,
    user_id: uuid.UUID,
    user_text: str,
    assistant_text: str,
    redis: Redis,
) -> None:
    """Best-effort append of a write-side creation turn to the shared query
    history. A Redis hiccup here must never break the user-facing reply — log
    and move on (the rule: no silent failures, but also no fatal ones here)."""
    assistant_text = (assistant_text or "").strip()
    if not assistant_text:
        return
    # A button-tap confirmation has no source text. The replayed history must
    # not contain an empty user message (Anthropic rejects empty content), so
    # substitute a minimal placeholder — the assistant turn carries the signal.
    user_text = (user_text or "").strip() or "(confirmé la acción)"
    try:
        await _append_query_history(
            user_id,
            user_msg=user_text,
            assistant_msg=assistant_text,
            redis=redis,
        )
    except Exception:  # pragma: no cover - defensive against Redis transients
        log.warning(
            "query_history_bridge_failed user_id=%s", user_id, exc_info=True
        )


async def _handle_confirm(
    *,
    user: User,
    yes: bool,
    db: AsyncSession,
    redis: Redis,
    source_text: str = "",
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

    # Phase 6f conversational creation: goals/incomes commit + confirm
    # differently from transactions (no amount sign, no /transactions deep link).
    if pending.action_type == "create_goal":
        await commit_pending(user=user, pending=pending, db=db, redis=redis)
        reply = BotReply(
            text=messages_es.GOAL_CREATED.format(
                name=pending.payload.get("name", "tu meta")
            )
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "create_income":
        await commit_pending(user=user, pending=pending, db=db, redis=redis)
        payload = pending.payload
        msg = messages_es.INCOME_CREATED.format(
            name=payload.get("name", "tu ingreso")
        )
        if payload.get("income_type") == "salary" and payload.get("currency") == "CRC":
            msg += messages_es.INCOME_DERIVE_TIP
        reply = BotReply(text=msg)
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "create_bill":
        await commit_pending(user=user, pending=pending, db=db, redis=redis)
        reply = BotReply(
            text=messages_es.BILL_CREATED.format(
                name=pending.payload.get("name", "el gasto fijo")
            )
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "log_transfer":
        # Phase 7b: a transfer commits two legs through the shared transfers
        # service. No assign_envelope hint — legs are excluded from envelope
        # spend math (paying the card is not new consumption). The shared
        # service runs the funds guard; surface its Spanish message in-chat
        # instead of bubbling a raw error (the pending row stays for a retry).
        try:
            await commit_pending(user=user, pending=pending, db=db, redis=redis)
        except HTTPException as exc:
            return BotReply(text=str(exc.detail))
        payload = pending.payload
        amt = format_amount(Decimal(payload["amount"]), payload["currency"])
        tmpl = (
            messages_es.CARD_PAYMENT_COMMITTED
            if payload.get("is_card_payment")
            else messages_es.TRANSFER_COMMITTED
        )
        return BotReply(
            text=tmpl.format(
                amount=amt,
                from_name=payload.get("from_account_name", ""),
                to_name=payload.get("to_account_name", ""),
            )
        )

    if pending.action_type == "reclassify":
        from api.services.transactions import (
            RECLASSIFY_NOT_FOUND,
            RECLASSIFY_REASON_ES,
            ReclassifyError,
        )

        to_income = bool(pending.payload.get("to_income"))
        try:
            await commit_pending(user=user, pending=pending, db=db, redis=redis)
        except ReclassifyError as exc:
            # The row changed between proposal and confirm (deleted, became a
            # transfer leg, etc.). Drop the stale proposal and explain.
            await clear_pending(user_id=user.id, redis=redis)
            return BotReply(
                text=RECLASSIFY_REASON_ES.get(
                    exc.reason_code, RECLASSIFY_REASON_ES[RECLASSIFY_NOT_FOUND]
                )
            )
        reply = BotReply(
            text=messages_es.RECLASSIFIED_TO_INCOME
            if to_income
            else messages_es.RECLASSIFIED_TO_EXPENSE
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "set_balance":
        # A7 re-anchor: append the anchor + the informational ajuste; the
        # balance heals to the stated value in one step (no amount sign, no
        # transaction deep link). Not part of the /undo chain.
        await commit_pending(user=user, pending=pending, db=db, redis=redis)
        payload = pending.payload
        reply = BotReply(
            text=messages_es.BALANCE_SET.format(
                name=payload.get("account_name", "la cuenta"),
                amount=format_amount(
                    Decimal(payload["value"]), payload["currency"]
                ),
            )
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "reallocate_envelope":
        # Phase 8 B4: move budget between two same-level envelopes through the
        # shared `envelopes.reallocate` (committed_outflows = total_limit stays
        # invariant). The service can 422 if a sobre went archived between
        # propose and confirm — surface that as Spanish copy, not a raw crash.
        # Append-style; not in /undo (reverse by reallocating back).
        try:
            await commit_pending(user=user, pending=pending, db=db, redis=redis)
        except HTTPException as exc:
            await db.rollback()
            await clear_pending(user_id=user.id, redis=redis)
            return BotReply(text=str(exc.detail))
        payload = pending.payload
        reply = BotReply(
            text=messages_es.ENVELOPE_REALLOCATED.format(
                amount=format_amount(
                    Decimal(payload["amount"]), payload["currency"]
                ),
                from_name=payload.get("from_name", ""),
                to_name=payload.get("to_name", ""),
            )
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    if pending.action_type == "reconcile_statement":
        # Statement reconciliation: anchor each matched account to its corte
        # balance (+ set loan balances) in one batch. Append-only; not in /undo.
        # A target can go archived/foreign or a value can fail validation between
        # propose and confirm — surface that as Spanish copy, never a raw crash.
        from api.services.statements import ReconcileError

        try:
            await commit_pending(user=user, pending=pending, db=db, redis=redis)
        except (ReconcileError, ValidationError, ValueError) as exc:
            log.warning("statement reconcile commit failed: %s", exc)
            await db.rollback()
            await clear_pending(user_id=user.id, redis=redis)
            return BotReply(text=messages_es.STATEMENT_RECONCILE_FAILED)
        payload = pending.payload
        n = len(payload["items"])
        reply = BotReply(
            text=messages_es.STATEMENT_RECONCILED.format(
                count=n,
                noun="saldo" if n == 1 else "saldos",
                corte=payload["corte_date"],
            )
        )
        await _bridge_to_query_history(
            user_id=user.id,
            user_text=source_text,
            assistant_text=reply.text,
            redis=redis,
        )
        return reply

    txn_id = await commit_pending(user=user, pending=pending, db=db, redis=redis)
    amt_decimal = Decimal(pending.payload["amount"])
    currency = pending.payload["currency"]
    amt_formatted = format_amount(amt_decimal, currency)
    tmpl = (
        messages_es.COMMITTED_EXPENSE
        if pending.action_type == "log_expense"
        else messages_es.COMMITTED_INCOME
    )

    # Phase 6f B16: the SPA "Ver en Centro Financiero" deep-link button was
    # removed with the SPA. The committed transaction is visible in the
    # native app's Transactions tab.
    text = tmpl.format(amount=amt_formatted)
    open_screen = None
    if pending.action_type == "log_expense":
        # Duplicate detection first: if the just-logged expense looks like a
        # dupe, flag it + raise the nudge (best-effort) and prefer a
        # `duplicate_warning` hint over the envelope-assign one — the user
        # should decide keep/delete before tagging a possibly-duplicate spend.
        from api.models.transaction import Transaction as _Txn
        from api.services.dedup import flag_and_notify

        matched = None
        dup_nudge_id = None
        txn = await db.get(_Txn, txn_id)
        if txn is not None:
            matched, dup_nudge_id = await flag_and_notify(db, user=user, txn=txn)

        if matched is not None:
            text = text + messages_es.DUPLICATE_WARNING.format(
                merchant=matched.merchant or "sin comercio",
                date=matched.transaction_date.isoformat(),
            )
            open_screen = OpenScreen(
                screen="duplicate_warning",
                prefill={
                    "transaction_id": str(txn_id),
                    "nudge_id": str(dup_nudge_id) if dup_nudge_id else None,
                    "amount": str(amt_decimal),
                    "currency": currency,
                    "merchant": pending.payload.get("merchant"),
                    "matched_merchant": matched.merchant,
                    "matched_date": matched.transaction_date.isoformat(),
                },
            )
        else:
            # Envelope budgeting (Sobres): after an EXPENSE commits, hand the
            # native chat a structured `assign_envelope` hint carrying the new
            # transaction id so it can offer an in-chat "Asignar a un sobre"
            # affordance. Telegram ignores open_screen (envelopes are
            # native-only). Income never gets the hint — caps are for spending.
            open_screen = OpenScreen(
                screen="assign_envelope",
                prefill={
                    "transaction_id": str(txn_id),
                    "amount": str(amt_decimal),
                    "currency": currency,
                    "merchant": pending.payload.get("merchant"),
                },
            )
    elif pending.action_type == "log_income":
        # Reclassify affordance: after an INCOME commits, hand the native chat a
        # `reclassify` hint so it can offer an in-chat "Era un gasto" chip on the
        # just-created row (mirrors assign_envelope; Telegram ignores it). The
        # expense path reuses its assign_envelope hint to also offer "Era un
        # ingreso", so both directions are one tap away right after capture.
        open_screen = OpenScreen(
            screen="reclassify",
            prefill={
                "transaction_id": str(txn_id),
                "amount": str(amt_decimal),
                "currency": currency,
                "merchant": pending.payload.get("merchant"),
            },
        )
    return BotReply(text=text, open_screen=open_screen)


async def _handle_reclassify(
    *, user: User, to_income: bool, db: AsyncSession, redis: Redis
) -> BotReply:
    """Stage a proposal to flip the LAST committed movement gasto ↔ ingreso.

    Validates the target row up front with the shared guards so we refuse with
    a clear reason (no recent movement / it's a transfer leg / already that
    kind …) instead of staging a proposal that can't commit. On success, saves
    a `reclassify` pending and returns the Sí/No confirm — the actual flip
    happens in `_commit_reclassify` after the user confirms.
    """
    from api.models.transaction import Transaction
    from api.services.transactions import (
        RECLASSIFY_NOT_FOUND,
        RECLASSIFY_REASON_ES,
        reclassify_blocker,
    )

    entry = await load_last_action(user_id=user.id, redis=redis)
    if not entry or entry.get("action_type") not in ("log_expense", "log_income"):
        return BotReply(text=RECLASSIFY_REASON_ES[RECLASSIFY_NOT_FOUND])
    try:
        record_id = uuid.UUID(entry.get("record_id", ""))
    except (ValueError, TypeError):
        return BotReply(text=RECLASSIFY_REASON_ES[RECLASSIFY_NOT_FOUND])

    txn = await db.get(Transaction, record_id)
    if txn is None or txn.user_id != user.id:
        return BotReply(text=RECLASSIFY_REASON_ES[RECLASSIFY_NOT_FOUND])

    blocker = reclassify_blocker(txn, to_income=to_income)
    if blocker is not None:
        return BotReply(text=RECLASSIFY_REASON_ES[blocker])

    magnitude = abs(Decimal(str(txn.amount)))
    summary = messages_es.RECLASSIFY_CONFIRM.format(
        kind="INGRESO" if to_income else "GASTO",
        amount=format_amount(magnitude, txn.currency),
    )
    short_id = new_short_id()
    pending = PendingAction(
        short_id=short_id,
        action_type="reclassify",
        payload={
            "transaction_id": str(txn.id),
            "to_income": to_income,
            "amount": str(magnitude),
            "currency": txn.currency,
        },
        summary_es=summary,
    )
    await save_pending(user_id=user.id, pending=pending, redis=redis)
    return BotReply(text=summary, buttons=_yes_no_buttons(short_id))


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
    return await _route_extraction(
        user=user,
        text="",
        extraction=extraction,
        today=today,
        db=db,
        redis=redis,
    )


async def _route_extraction(
    *,
    user: User,
    text: str,
    extraction: ExtractionResult,
    today: date,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    if extraction.dispatcher == "query":
        # Belt-and-suspenders: the query dispatcher already maps known
        # failures to Spanish copy, but anything it lets escape (budget-row
        # commit, an unforeseen path) must not surface as a raw 500 to the
        # native chat / Telegram. Always hand back a handled message.
        # Call run_dispatch (not the str-only `handle`) so we can carry the
        # outcome's optional open_screen handoff (e.g. → the native 'Sin cuenta'
        # screen after listing unassigned movements). Telegram ignores it.
        # P10 B2: per-turn advisory resolution (D12) — a planning question (or
        # an active continuity session) gets the advisory persona/toolset; a
        # transactional lookup stays plain even mid-session.
        advisory = await advisory_this_turn(
            user_id=user.id, text=text, redis=redis
        )
        try:
            outcome = await run_dispatch(
                user_id=user.id,
                message_text=text,
                telegram_chat_id=user.telegram_user_id,
                advisory=advisory,
            )
        except Exception as e:  # noqa: BLE001 - last line of defense
            log.exception(
                "query_dispatcher_unhandled user_id=%s dispatcher=%s intent=%s "
                "confidence=%s failure_class=%s message_hash=%s",
                user.id,
                extraction.dispatcher,
                extraction.intent.value,
                extraction.confidence,
                "budget" if isinstance(e, BudgetExceeded) else "system",
                _message_hash(text),
            )
            return BotReply(
                text=handle_query_error(e, user_id=user.id),
                error_class="budget" if isinstance(e, BudgetExceeded) else "system",
            )
        open_screen = None
        if outcome.open_screen:
            open_screen = OpenScreen(
                screen=outcome.open_screen["screen"],
                prefill=outcome.open_screen.get("prefill", {}),
            )
        return BotReply(text=outcome.text, open_screen=open_screen)

    # P10 B0.5 (R2): the write/control branch gets the same last-line-of-defense
    # net the query branch has had since 2026-06-15. Before this, a throw from
    # dispatch()/_apply_decision() propagated raw out of process_message — the
    # native endpoint's outer guard caught it, but with an undifferentiated
    # "system" story and no structured failure log (the 2026-07-02 compound-
    # intent bug rode exactly this asymmetry).
    try:
        decision = await dispatch(
            extraction=extraction, user=user, today=today, db=db
        )
        return await _apply_decision(
            user=user, decision=decision, db=db, redis=redis, source_text=text
        )
    except Exception:  # noqa: BLE001 - last line of defense
        log.exception(
            "write_dispatcher_unhandled user_id=%s dispatcher=%s intent=%s "
            "confidence=%s failure_class=system message_hash=%s",
            user.id,
            extraction.dispatcher,
            extraction.intent.value,
            extraction.confidence,
            _message_hash(text),
        )
        return BotReply(text=messages_es.CHAT_UNEXPECTED_ERROR, error_class="system")


async def _apply_decision(
    *,
    user: User,
    decision,
    db: AsyncSession,
    redis: Redis,
    source_text: str = "",
) -> BotReply:
    telemetry_persisted = _stage_lazy_detection_events(
        user=user, decision=decision, db=db
    )

    # Clarification state is tied to "we just asked a question". Any
    # decision that isn't a new question ends the clarification.
    if not isinstance(decision, AskClarification):
        await clear_clarification(user_id=user.id, redis=redis)

    if isinstance(decision, RerouteToQuery):
        # P10 B0.5 (R3): an analytical intent slipped into the write path
        # (compound / mis-tagged message). Preferring the analytical answer is
        # the correct behavior — route the ORIGINAL text through the query
        # dispatcher instead of erroring.
        log.warning(
            "reroute_to_query user_id=%s message_hash=%s",
            user.id,
            _message_hash(source_text),
        )
        advisory = await advisory_this_turn(
            user_id=user.id, text=source_text, redis=redis
        )
        try:
            outcome = await run_dispatch(
                user_id=user.id,
                message_text=source_text,
                telegram_chat_id=user.telegram_user_id,
                advisory=advisory,
            )
        except Exception as e:  # noqa: BLE001 - last line of defense
            log.exception("reroute_query_unhandled user_id=%s", user.id)
            return BotReply(
                text=handle_query_error(e, user_id=user.id),
                error_class="budget" if isinstance(e, BudgetExceeded) else "system",
            )
        return BotReply(text=outcome.text)

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
        # Bridge the creation proposal (not the overwrite prefix) into the
        # shared query history so a follow-up advice question can resolve it.
        if decision.action_type in _BRIDGED_CREATION_ACTIONS:
            await _bridge_to_query_history(
                user_id=user.id,
                user_text=source_text,
                assistant_text=decision.summary_es,
                redis=redis,
            )
        return BotReply(
            text=prefix + decision.summary_es,
            buttons=_buttons_for(short_id),
        )
    if isinstance(decision, OpenScreenAction):
        # Debt: chat hands off to the native form. No pending, no commit, no
        # buttons — the form gathers the rest and posts to POST /debts. The
        # clarification key was already cleared above (not an AskClarification).
        if telemetry_persisted:
            await db.commit()
        # Bridge the debt-creation handoff so the advisor knows a debt is being
        # set up if the user immediately asks about it.
        if decision.screen == "debt_create":
            await _bridge_to_query_history(
                user_id=user.id,
                user_text=source_text,
                assistant_text=decision.message_es,
                redis=redis,
            )
        return BotReply(
            text=decision.message_es,
            open_screen=OpenScreen(
                screen=decision.screen, prefill=decision.prefill
            ),
        )
    if isinstance(decision, AskClarification):
        # Phase 7f: when the dispatcher supplied tappable options (account
        # names), stash them with a fresh nonce so a Telegram tap can be
        # validated against THIS question, then render them as buttons.
        state = ClarificationState(
            partial=decision.partial,
            awaiting_field=decision.awaiting_field,
            question_es=decision.question_es,
            options=list(decision.options),
            nonce=new_short_id() if decision.options else "",
        )
        await save_clarification(user_id=user.id, state=state, redis=redis)
        if telemetry_persisted:
            await db.commit()
        return BotReply(
            text=decision.question_es, buttons=_clarify_buttons(state)
        )
    if isinstance(decision, LazyDetectionPrompt):
        await save_lazy_account_creation(
            user_id=user.id,
            suggested_name=decision.hint_text,
            origin_extraction=decision.origin_extraction,
            redis=redis,
        )
        if telemetry_persisted:
            await db.commit()
        return BotReply(text=decision.message_es)
    if isinstance(decision, StartAccountCreation):
        # Phase 8 B2 chat-led activation: seed the account-creation flow with the
        # stated balance + currency; it asks only name + type, then creates the
        # account WITH this real balance (no replay needed). The subsequent
        # account-creation round-trip (above) drives name → type → confirm.
        prompt = await start_account_creation(
            user_id=user.id,
            redis=redis,
            currency=decision.currency,
            initial_balance=decision.initial_balance,
        )
        if telemetry_persisted:
            await db.commit()
        return BotReply(text=prompt)
    if isinstance(decision, ConfirmResponse):
        return await _handle_confirm(
            user=user, yes=decision.yes, db=db, redis=redis, source_text=source_text
        )
    if isinstance(decision, UndoRequest):
        _ok, msg = await run_undo(user=user, db=db, redis=redis)
        return BotReply(text=msg)
    if isinstance(decision, ShowHelp):
        return BotReply(text=messages_es.HELP_TEXT)
    if isinstance(decision, Reject):
        return BotReply(text=decision.message_es)
    return BotReply(text=messages_es.HELP_TEXT)


def _stage_lazy_detection_events(*, user: User, decision, db: AsyncSession) -> bool:
    events = getattr(decision, "telemetry_events", None) or []
    for event in events:
        db.add(
            LazyDetectionEvent(
                user_id=user.id,
                hint_type=event.hint_type,
                hint_text=event.hint_text,
                fuzzy_match_score=event.fuzzy_match_score,
                resolution=event.resolution,
                matched_account_id=event.matched_account_id,
            )
        )
    return bool(events)


# ── handler entry for inline-keyboard callbacks ──────────────────────────────


async def handle_statement_document(
    *,
    user: User,
    pdf_bytes: bytes,
    db: AsyncSession,
    redis: Redis,
    llm_client,
    haiku_model: str,
    sonnet_model: str,
) -> BotReply:
    """Chat path for a bank-statement PDF (Telegram now; WhatsApp inherits when
    P5c lands — it's channel-agnostic, the caller just supplies the bytes).

    Reads the statement, fuzzy-matches each product to one of the user's
    accounts/debts, and proposes the confident matches as ONE Sí/No batch.
    Confirm → `reconcile_products` anchors each at the corte (same write the
    native form uses). Products that can't be matched are listed for the app.
    "LLM extracts; rules decide": the deterministic reconcile is the only writer.
    """
    from sqlalchemy import select

    from api.models.account import Account
    from api.models.debt import Debt
    from api.services.llm_extractor import extract_statement
    from api.services.statement_normalize import (
        build_reconcile_plan,
        is_auto_includable,
        leg_to_item,
    )

    try:
        extraction = await extract_statement(
            user=user,
            pdf_bytes=pdf_bytes,
            client=llm_client,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            db=db,
        )
    except Exception:  # extraction must never bubble a raw error to the chat
        log.exception("statement extraction failed")
        return BotReply(text=messages_es.STATEMENT_PARSE_ERROR)

    if not extraction.accounts or extraction.period_end is None:
        return BotReply(text=messages_es.STATEMENT_EMPTY)

    accounts = list(
        (
            await db.execute(
                select(Account).where(
                    Account.user_id == user.id,
                    Account.is_active.is_(True),
                    Account.archived.is_(False),
                )
            )
        ).scalars()
    )
    debts = list(
        (
            await db.execute(
                select(Debt).where(
                    Debt.user_id == user.id,
                    Debt.is_active.is_(True),
                    Debt.archived.is_(False),
                )
            )
        ).scalars()
    )
    plan = build_reconcile_plan(extraction, accounts=accounts, debts=debts)
    corte = plan.corte_date

    _KIND_LABEL = {"deposit": "Cuenta", "credit": "Tarjeta", "loan": "Préstamo"}
    items: list[dict] = []
    lines: list[str] = []
    review: list[str] = []
    for leg in plan.legs:
        leg_label = leg.label or _KIND_LABEL.get(leg.resolution.kind, "Producto")
        if leg.last4:
            leg_label = f"{leg_label} …{leg.last4}"
        if not is_auto_includable(leg):
            # Conservation-flagged, no role candidate, or no confident target →
            # the user resolves it in the app (no silent drop).
            review.append(leg_label)
            continue
        # The one collapse point — identical to the REST/native write items.
        items.append(leg_to_item(leg).model_dump(mode="json"))
        name = leg.resolution.target_name or leg_label
        new_txt = format_amount(Decimal(str(leg.reconcile_value)), leg.currency)
        # For a loan show the antes→después so the user confirms a balance change
        # knowingly (deposit/credit legs anchor without a prior here). Flagged
        # loans never reach this branch — they go to `review` (confirm in the app).
        if leg.prior_balance is not None:
            line = f"• {name}: {format_amount(leg.prior_balance, leg.currency)} → {new_txt}"
        else:
            line = f"• {name}: {new_txt}"
        # An auto-included leg whose conservation cross-check failed is trusted
        # (the printed balance) but flagged so the user confirms it knowingly.
        if leg.conservation_ok is False:
            line += " (no verificado)"
        lines.append(line)

    if not items:
        return BotReply(
            text=messages_es.STATEMENT_NO_MATCH.format(corte=corte.isoformat())
        )

    short_id = new_short_id()
    summary = messages_es.STATEMENT_PROPOSAL.format(
        bank=plan.bank or "tu banco",
        corte=corte.isoformat(),
        lines="\n".join(lines),
    )
    if review:
        summary += messages_es.STATEMENT_REVIEW.format(names=", ".join(review))
    pending = PendingAction(
        short_id=short_id,
        action_type="reconcile_statement",
        payload={"corte_date": corte.isoformat(), "items": items},
        summary_es=summary,
    )
    await save_pending(user_id=user.id, pending=pending, redis=redis)
    return BotReply(text=summary, buttons=_yes_no_buttons(short_id))


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


async def handle_clarify_callback(
    *,
    user: User,
    callback_data: str,
    db: AsyncSession,
    redis: Redis,
) -> BotReply:
    """Phase 7f — the user tapped a clarification option button (an account
    name). `callback_data` has the form `clarify:<nonce>:<idx>`. The label is
    resolved against the stashed ClarificationState and routed through the
    SAME merge→dispatch path a typed reply takes (the native chat never gets
    here — its chips post the label as text). Stale taps (expired state or
    nonce from a superseded question) get a gentle "expired" reply."""
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "clarify":
        return BotReply(text=messages_es.CLARIFY_EXPIRED)
    _, nonce, idx_raw = parts

    state = await load_clarification(user_id=user.id, redis=redis)
    if state is None or not state.nonce or state.nonce != nonce:
        return BotReply(text=messages_es.CLARIFY_EXPIRED)
    try:
        label = state.options[int(idx_raw)]
    except (ValueError, IndexError):
        return BotReply(text=messages_es.CLARIFY_EXPIRED)

    merged = merge_reply(state, label, user)
    if merged is None:  # pragma: no cover — option labels merge raw
        return BotReply(
            text=state.question_es, buttons=_clarify_buttons(state)
        )
    today = _today_for(user)
    if merged.dispatcher == "query":  # pragma: no cover — defensive; account
        # merges never flip the dispatcher, but mirror the typed path anyway.
        await clear_clarification(user_id=user.id, redis=redis)
        return await _route_extraction(
            user=user,
            text=label,
            extraction=merged,
            today=today,
            db=db,
            redis=redis,
        )
    decision = await dispatch(extraction=merged, user=user, today=today, db=db)
    return await _apply_decision(
        user=user, decision=decision, db=db, redis=redis, source_text=label
    )


# ── nudge inline-keyboard callbacks ──────────────────────────────────────────


def _nudge_act_reply(nudge_type: str) -> str:
    if nudge_type == "missing_income":
        return messages_es.NUDGE_ACK_ACT_MISSING_INCOME
    if nudge_type == "stale_pending_confirmation":
        return messages_es.NUDGE_ACK_ACT_STALE_PENDING
    if nudge_type == "upcoming_bill":
        return messages_es.NUDGE_ACK_ACT_UPCOMING_BILL
    if nudge_type == "shadow_review_pending":
        return messages_es.NUDGE_ACK_ACT_SHADOW_REVIEW
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

    # Duplicate warning: act = delete the dupe, dismiss = keep it. Both resolve
    # the nudge terminally (acted_on) via resolve_duplicate — NOT through the
    # generic dismiss path, whose auto-silence would mute duplicate detection
    # after two false positives.
    if nudge_type == "duplicate_transaction":
        from api.services.dedup import resolve_duplicate
        from api.services.transactions import (
            TXN_DELETE_REASON_ES,
            TransactionDeleteError,
        )

        if verb == _NUDGE_VERB_ACT:
            try:
                await resolve_duplicate(db, user=user, nudge=nudge, keep=False)
            except TransactionDeleteError as exc:
                await db.rollback()
                return BotReply(
                    text=TXN_DELETE_REASON_ES.get(
                        exc.reason_code,
                        "No se puede eliminar este movimiento.",
                    )
                )
            await db.commit()
            return BotReply(text=messages_es.DUPLICATE_DELETED)
        if verb == _NUDGE_VERB_DISMISS:
            await resolve_duplicate(db, user=user, nudge=nudge, keep=True)
            await db.commit()
            return BotReply(text=messages_es.DUPLICATE_KEPT)
        return BotReply(text=messages_es.NUDGE_ACK_LATER)

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


# ── vision helper ─────────────────────────────────────────────────────────────


async def _process_image(
    *,
    user: User,
    image_bytes: bytes,
    image_media_type: str,
    today: date,
    db: AsyncSession,
    redis: Redis,
    llm_client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
) -> BotReply:
    """Budget gate → vision extraction → write dispatcher. Skips all
    text-pipeline branches (commands, account creation, clarification,
    pending-confirm) because there is no user text to parse."""
    tz_name = getattr(user, "timezone", None) or "America/Costa_Rica"
    try:
        await assert_within_budget(user_id=user.id, db=db, tz_name=tz_name)
    except BudgetExceeded as e:
        return BotReply(text=handle_query_error(e, user_id=user.id))

    try:
        extraction = await extract_vision(
            user=user,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            client=llm_client,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            db=db,
        )
    except (LLMClientError, ValidationError) as e:
        log.info(
            "vision_extraction_failure user_id=%s err=%s", user.id, type(e).__name__
        )
        return BotReply(text=messages_es.EXTRACTOR_FAILED)

    return await _route_extraction(
        user=user,
        text="",
        extraction=extraction,
        today=today,
        db=db,
        redis=redis,
    )
