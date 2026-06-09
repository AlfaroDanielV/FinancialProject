"""Deterministic dispatcher for Telegram bot intents.

Consumes an `ExtractionResult` from the LLM extractor plus user context and
returns one of a small set of `DispatcherResult` variants that describe
what the handler should do next. No LLM calls. No DB writes. No policy
baked into the LLM layer.

The Phase 5b spec's core rule: the LLM extracts, the dispatcher decides.
Everything downstream of this module is deterministic.
"""
from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from ..schemas.recurring_bills import VALID_RECURRING_BILL_CATEGORIES
from .dispatch.lazy_detection import classify_hint_type, match_account_hint
from .accounts import resolve_account, list_active
from .amortization import compute_french_payment
from .finance.affordability import (
    SAFETY_MARGIN,
    AffordabilityResult,
    FinancialContext,
    assess_for_user,
    gather_financial_context,
)
from .finance.cashflow import compute_monthly_cashflow
from .llm_extractor import ExtractionResult, Intent
from .transactions import window_bounds

from app.domain.payroll import UnconfiguredYearError, compute_net_salary


# ── result variants ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LazyDetectionTelemetry:
    hint_type: str
    hint_text: str
    fuzzy_match_score: float | None
    resolution: str
    matched_account_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProposeAction:
    """Bot has enough info to act but must confirm with the user first.

    `payload` is everything the commit step needs, fully resolved: account
    id (not hint), signed amount, concrete calendar date, etc. The handler
    serializes this into Redis at telegram:pending:{user_id}.
    """

    action_type: str  # "log_expense" | "log_income"
    payload: dict[str, Any]
    summary_es: str
    telemetry_events: list[LazyDetectionTelemetry] = field(default_factory=list)


@dataclass(frozen=True)
class AskClarification:
    """Dispatcher needs one field to proceed. The handler asks the user and
    stages `partial` alongside `awaiting_field` in Redis so the next
    message can be merged into a fresh extraction."""

    question_es: str
    awaiting_field: str  # "amount" | "account" | "intent" | "currency"
    partial: dict[str, Any] = field(default_factory=dict)
    telemetry_events: list[LazyDetectionTelemetry] = field(default_factory=list)


@dataclass(frozen=True)
class LazyDetectionPrompt:
    """The user mentioned an account/bank we cannot confidently link.

    B8 only asks the action question. B9 owns the conversational account
    creation state machine that handles the user's next "crear" response.
    """

    message_es: str
    hint_text: str
    origin_extraction: dict[str, Any]
    telemetry_events: list[LazyDetectionTelemetry] = field(default_factory=list)


@dataclass(frozen=True)
class OpenScreenAction:
    """Phase 6f debt slice — chat does LIGHT extraction then hands off to a
    native screen instead of confirming/committing in chat.

    Unlike goal/income/bill creation (which propose → confirm → commit here),
    debt needs six required fields + amortization params that the native form
    gathers. `prefill` carries the partial fields the LLM extracted; the native
    client opens `screen` pre-filled and the user completes + submits to the
    existing `POST /debts`. No Redis pending, no commit, no buttons.

    The Telegram renderer ignores `screen`/`prefill` and shows `message_es`
    only — debt is a native-app feature; Telegram stays a backup capture
    surface."""

    screen: str  # "debt_create"
    prefill: dict[str, Any]
    message_es: str


@dataclass(frozen=True)
class ConfirmResponse:
    """User said yes/no/cancel. The handler correlates with the Redis
    pending-action key — dispatcher doesn't know if one exists."""

    yes: bool


@dataclass(frozen=True)
class UndoRequest:
    """User typed /undo (or natural-language equivalent). Handler looks up
    telegram:last_action:{user_id} and runs the hard-delete flow."""


@dataclass(frozen=True)
class ShowHelp:
    """User asked for help or sent something incomprehensible. Handler
    replies with the canonical capabilities list."""


@dataclass(frozen=True)
class Reject:
    """Catch-all for known-bad inputs (suspended user, unsupported currency
    someday, etc.). `reason_code` lets the handler pick the right Spanish
    message without string parsing."""

    reason_code: str
    message_es: str


DispatcherResult = Union[
    ProposeAction,
    OpenScreenAction,
    AskClarification,
    LazyDetectionPrompt,
    ConfirmResponse,
    UndoRequest,
    ShowHelp,
    Reject,
]


# ── configuration ─────────────────────────────────────────────────────────────


# Below this confidence, clarify instead of proposing — even if the model
# produced a perfectly shaped extraction. Prevents silent miscommits.
CONFIDENCE_FLOOR = 0.6

# Default number of recent transactions shown for "últimas" queries.
DEFAULT_RECENT_LIMIT = 5


# ── Spanish relative-date resolver ────────────────────────────────────────────
# Small on purpose — if you find yourself adding to this, reach for the
# occurred_at_hint field in the prompt rather than growing the table. The
# point is not to be comprehensive; it's to handle the 80% case honestly.


def _resolve_occurred_at(hint: Optional[str], today: date) -> date:
    if not hint:
        return today
    key = hint.strip().lower()
    if key in {"hoy", "ahora", "recién", "recien"}:
        return today
    if key in {"ayer"}:
        return today - timedelta(days=1)
    if key in {"anteayer", "antier"}:
        return today - timedelta(days=2)
    # Anything we don't recognize → today. The summary_es will note the
    # resolved date so the user can correct via "Editar".
    return today


# ── entry point ───────────────────────────────────────────────────────────────


async def dispatch(
    *,
    extraction: ExtractionResult,
    user: User,
    today: date,
    db: AsyncSession,
) -> DispatcherResult:
    """Map extraction → next action. Pure decision logic — no side effects."""

    intent = extraction.intent

    # Structural intents short-circuit before any confidence check: a user
    # typing "sí" doesn't need 0.9 confidence to mean yes.
    if intent is Intent.CONFIRM_YES:
        return ConfirmResponse(yes=True)
    if intent is Intent.CONFIRM_NO:
        return ConfirmResponse(yes=False)
    if intent is Intent.UNDO:
        return UndoRequest()
    if intent is Intent.HELP:
        return ShowHelp()
    if intent is Intent.UNKNOWN:
        return ShowHelp()

    # Below the confidence floor, clarify for LOG intents rather than guess.
    # The generic "¿gasto, ingreso o consulta?" only fits log_expense/
    # log_income — it's the WRONG question for an explicit creation request
    # ("registrá una deuda" / "saqué un préstamo" is none of those), and asking
    # it traps the user in an intent-clarification loop. The create_* intents
    # carry an explicit verb, so they fall through to their own dispatchers
    # regardless of confidence; each does field-specific clarification
    # (goal/income/bill) or opens the native form (debt). The model was honest
    # about its uncertainty; respect it only where the question makes sense.
    if extraction.confidence < CONFIDENCE_FLOOR and intent in (
        Intent.LOG_EXPENSE,
        Intent.LOG_INCOME,
    ):
        return AskClarification(
            question_es=(
                "No estoy seguro de lo que querés hacer. "
                "¿Es un gasto, un ingreso, o una consulta?"
            ),
            awaiting_field="intent",
            partial=extraction.model_dump(mode="json"),
        )

    if intent is Intent.QUERY:
        raise RuntimeError(
            "Intent.QUERY no debe llegar al dispatcher de write. "
            "Verificá el routing en bot/pipeline.py — el dispatcher de query "
            "debe interceptar antes."
        )

    if intent in (Intent.LOG_EXPENSE, Intent.LOG_INCOME):
        return await _dispatch_log(
            extraction=extraction, user=user, today=today, db=db
        )

    if intent is Intent.CREATE_GOAL:
        return await _dispatch_create_goal(
            extraction=extraction, user=user, today=today, db=db
        )

    if intent is Intent.CREATE_INCOME:
        return _dispatch_create_income(
            extraction=extraction, user=user, today=today
        )

    if intent is Intent.CREATE_BILL:
        return _dispatch_create_bill(
            extraction=extraction, user=user, today=today
        )

    if intent is Intent.CREATE_DEBT:
        return await _dispatch_create_debt(
            extraction=extraction, user=user, db=db
        )

    # Defensive fallback — should be unreachable given the enum.
    return ShowHelp()


async def _dispatch_log(
    *,
    extraction: ExtractionResult,
    user: User,
    today: date,
    db: AsyncSession,
) -> DispatcherResult:
    # 1. Amount is non-negotiable. Without it there's nothing to commit.
    if extraction.amount is None:
        return AskClarification(
            question_es="¿Cuánto fue? Decime el monto (puede ser '5000' o '5 mil').",
            awaiting_field="amount",
            partial=extraction.model_dump(mode="json"),
        )

    # 2. Currency default. If the user didn't say, fall back to their
    # preferred currency. The summary mentions this explicitly so the user
    # catches a wrong default via Editar.
    resolved_currency = extraction.currency or user.currency
    currency_defaulted = extraction.currency is None

    # 3. Account resolution. Explicit hints go through Phase 6d B8 lazy
    # detection: unknown account/bank names prompt account creation instead
    # of silently falling back to another account.
    accounts = await list_active(user, db)
    account = None
    telemetry_events: list[LazyDetectionTelemetry] = []

    if extraction.account_hint:
        match = match_account_hint(extraction.account_hint, accounts)
        hint_type = classify_hint_type(extraction.account_hint)
        telemetry_events.append(
            LazyDetectionTelemetry(
                hint_type=hint_type,
                hint_text=extraction.account_hint,
                fuzzy_match_score=match.score,
                resolution=(
                    "linked_existing" if match.status == "matched" else "pending"
                ),
                matched_account_id=match.account.id if match.account else None,
            )
        )
        if match.status == "matched":
            account = match.account
        elif match.status == "ambiguous":
            names = ", ".join(a.name for a in accounts)
            return AskClarification(
                question_es=(f"¿De qué cuenta? Opciones: {names}."),
                awaiting_field="account",
                partial=extraction.model_dump(mode="json"),
                telemetry_events=telemetry_events,
            )
        else:
            return LazyDetectionPrompt(
                message_es=(
                    "No tengo registrada una cuenta llamada "
                    f"{extraction.account_hint}. ¿La creamos? Respondé *crear* "
                    "y la hacemos acá; si no, seguí normal con otra cosa."
                ),
                hint_text=extraction.account_hint,
                origin_extraction=extraction.model_dump(mode="json"),
                telemetry_events=telemetry_events,
            )
    else:
        account = await resolve_account(user, None, db)

    account_required_but_not_chosen = len(accounts) > 1 and account is None
    if account_required_but_not_chosen:
        names = ", ".join(a.name for a in accounts)
        return AskClarification(
            question_es=(
                f"¿De qué cuenta? Opciones: {names}."
            ),
            awaiting_field="account",
            partial=extraction.model_dump(mode="json"),
        )

    # 4. Occurred-at resolution.
    occurred_at = _resolve_occurred_at(extraction.occurred_at_hint, today)

    # 5. Sign. DB convention: negative=expense, positive=income. The
    # extractor always gives a positive magnitude; we apply the sign here.
    magnitude: Decimal = extraction.amount
    is_expense = extraction.intent is Intent.LOG_EXPENSE
    signed_amount = -magnitude if is_expense else magnitude

    # 6. Category pass-through per the YAGNI rule. Whitespace-normalized
    # only; no synonym map.
    category = extraction.category_hint

    payload = {
        "action_type": "log_expense" if is_expense else "log_income",
        "amount": str(signed_amount),
        "currency": resolved_currency,
        "merchant": extraction.merchant,
        "category": category,
        "description": None,
        "transaction_date": occurred_at.isoformat(),
        "account_id": str(account.id) if account else None,
        "account_name": account.name if account else None,
    }

    summary = _build_summary(
        is_expense=is_expense,
        amount=magnitude,
        currency=resolved_currency,
        currency_defaulted=currency_defaulted,
        merchant=extraction.merchant,
        category=category,
        account_name=account.name if account else None,
        occurred_at=occurred_at,
        today=today,
    )

    return ProposeAction(
        action_type=payload["action_type"],
        payload=payload,
        summary_es=summary,
        telemetry_events=telemetry_events,
    )


def _format_amount(amount: Decimal, currency: str) -> str:
    """Costa Rican conventions: ₡5.000 for CRC (period thousands, no
    decimals), $30.00 for USD (comma thousands, two decimals)."""
    if currency == "CRC":
        return "₡" + f"{int(amount):,}".replace(",", ".")
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount} {currency}"


def _build_summary(
    *,
    is_expense: bool,
    amount: Decimal,
    currency: str,
    currency_defaulted: bool,
    merchant: Optional[str],
    category: Optional[str],
    account_name: Optional[str],
    occurred_at: date,
    today: date,
) -> str:
    verb = "Gasto" if is_expense else "Ingreso"
    amt = _format_amount(amount, currency)
    parts: list[str] = [f"{verb} de {amt}"]
    if merchant:
        parts.append(f"en {merchant}")
    if category:
        parts.append(f"({category})")
    if account_name:
        parts.append(f"cuenta {account_name}")
    if occurred_at == today:
        parts.append("hoy")
    elif occurred_at == today - timedelta(days=1):
        parts.append("ayer")
    else:
        parts.append(occurred_at.isoformat())

    lead = " ".join(parts) + "."
    if currency_defaulted:
        lead += f" (Usé {currency} por defecto.)"
    return lead + " ¿Confirmo?"


# ── Phase 6f: conversational goal creation ────────────────────────────────────


_MONTHS_ES: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _add_months(d: date, n: int) -> date:
    """Add n calendar months, clamping the day to the target month length."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _months_between(start: date, end: date) -> int:
    """Whole months from start to end, floored at 1 (avoids /0 in forecasts)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return max(1, months)


def _resolve_goal_target_date(hint: Optional[str], today: date) -> Optional[date]:
    """Resolve a natural-language goal target-date hint to a concrete date.

    Mirrors the LLM-extracts/server-resolves split used for occurred_at: the
    model passes the user's words, this resolves them. Handles ISO dates,
    YYYY-MM, "en N meses/años", "fin de año", and Spanish month names
    (optionally with a year). Anything else → None (goal created without a
    deadline; user can set one later)."""
    if not hint:
        return None
    h = hint.strip().lower()

    try:
        return date.fromisoformat(h)
    except ValueError:
        pass

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", h)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1)

    m = re.search(r"en\s+(\d{1,3})\s+mes", h)
    if m:
        return _add_months(today, int(m.group(1)))
    m = re.search(r"en\s+(\d{1,2})\s+a[nñ]o", h)
    if m:
        return _add_months(today, int(m.group(1)) * 12)

    if "fin de a" in h:  # "fin de año" / "fin de ano"
        eoy = date(today.year, 12, 1)
        return eoy if eoy > today else date(today.year + 1, 12, 1)

    for name, month in _MONTHS_ES.items():
        if name in h:
            year_match = re.search(r"(20\d{2})", h)
            if year_match:
                return date(int(year_match.group(1)), month, 1)
            cand = date(today.year, month, 1)
            if cand <= today:
                cand = date(today.year + 1, month, 1)
            return cand

    return None


def _whole(amount: Decimal, currency: str) -> str:
    return _format_amount(amount.quantize(Decimal("1")), currency)


def _goal_feasibility_line(
    *, monthly: Decimal, currency: str, assessment: Optional[AffordabilityResult]
) -> str:
    """Phase 7: word the deterministic feasibility verdict, now judged against the
    envelope-aware surplus ([[Decision - Unified Monthly Cashflow]]). The engine
    decides; this only phrases it. Non-blocking — the user still confirms either
    way."""
    monthly_str = _whole(monthly, currency)

    # No assessment (cross-currency goal skips the gate).
    if assessment is None:
        return f" Necesitás ~{monthly_str}/mes para llegar."

    # Gated → no trustworthy surplus → no feasibility claim, and the ask is
    # DISTINCT per gate (register income / build budget).
    if assessment.feasible is None:
        if assessment.gate_reason == "no_budget":
            extra = (
                "Todavía no tenés sobres (presupuesto) armados, así que no puedo "
                "confirmar si te alcanza. Armá tus sobres y te digo."
            )
        else:  # no_income (or unknown)
            extra = (
                "No tengo tus ingresos registrados, así que no puedo confirmar si "
                "te alcanza. Registralos y te digo."
            )
        return f" Necesitás ~{monthly_str}/mes para llegar. ({extra})"

    if assessment.feasible:
        return f" Necesitás ~{monthly_str}/mes y te alcanza con tu sobrante."

    # Infeasible — direct + constructive, never a veto. When the user has
    # savings/investing envelopes, offer reallocation as THEIR option (the engine
    # never grabs that money for them — sub-decision B).
    savings = assessment.savings_allocations or Decimal("0")
    realloc = ""
    if savings > 0:
        realloc = (
            f" Eso sí: tenés ~{_whole(savings, currency)}/mes en sobres de "
            "ahorro/inversión — si querés priorizar esta meta, podés reasignar parte."
        )

    if assessment.min_timeline_months_feasible is not None:
        safe = _whole(assessment.safe_surplus or Decimal("0"), currency)
        short = _whole(assessment.shortfall or Decimal("0"), currency)
        return (
            f" Necesitás ~{monthly_str}/mes, pero tu sobrante seguro ronda "
            f"{safe}/mes — te faltarían ~{short}/mes. Podrías extender a "
            f"~{assessment.min_timeline_months_feasible} meses o bajar la meta."
            f"{realloc}"
        )
    # No positive surplus at all (envelopes already consume the income).
    return (
        f" Necesitás ~{monthly_str}/mes, pero ahora tus sobres ya consumen tu "
        f"ingreso. Habría que bajar la meta o liberar algún sobre antes.{realloc}"
    )


def _goal_context_note(context: Optional[FinancialContext]) -> str:
    """Phase 7a: one short voseo heads-up from the financial context — the first
    over-limit envelope, else the soonest upcoming obligation with a known
    amount. Context only; it never changes the verdict and never vetoes."""
    if context is None:
        return ""
    pressure = context.envelope_pressure
    if pressure is not None and pressure.over_limit:
        first = pressure.over_limit[0]
        return f" Ojo: ya te pasaste del sobre {first.name}."
    for o in context.upcoming_obligations:
        if o.amount is not None:
            d = date.fromisoformat(o.due_date)
            return (
                f" Ojo: se viene {o.title} "
                f"({_format_amount(o.amount, o.currency)}) el {d.day}/{d.month}."
            )
    return ""


def _build_goal_summary(
    *,
    name: str,
    target: Decimal,
    currency: str,
    currency_defaulted: bool,
    target_date: Optional[date],
    today: date,
    assessment: Optional[AffordabilityResult] = None,
    context_note: str = "",
) -> str:
    amt = _format_amount(target, currency)
    parts = [f"Nueva meta: {name} — ahorrar {amt}"]
    if target_date is not None:
        parts.append(f"para {target_date.isoformat()}")
    lead = " ".join(parts) + "."
    if target_date is not None:
        months = _months_between(today, target_date)
        monthly = (target / months).quantize(Decimal("1"))
        lead += _goal_feasibility_line(
            monthly=monthly, currency=currency, assessment=assessment
        )
        lead += context_note
    if currency_defaulted:
        lead += f" (Usé {currency} por defecto.)"
    return lead + " ¿Confirmo?"


async def _dispatch_create_goal(
    *,
    extraction: ExtractionResult,
    user: User,
    today: date,
    db: AsyncSession,
) -> DispatcherResult:
    # Target amount is non-negotiable — it's what "the goal" means.
    if extraction.goal_target_amount is None:
        return AskClarification(
            question_es="¿Cuánto querés ahorrar? Decime el monto de la meta (ej: '2 millones').",
            awaiting_field="goal_target_amount",
            partial=extraction.model_dump(mode="json"),
        )
    # A goal needs a name so it's identifiable in the list.
    if not extraction.goal_name:
        return AskClarification(
            question_es="¿Cómo querés llamar la meta? (ej: 'vacaciones', 'fondo de emergencia').",
            awaiting_field="goal_name",
            partial=extraction.model_dump(mode="json"),
        )

    currency = extraction.currency or user.currency
    currency_defaulted = extraction.currency is None
    target: Decimal = extraction.goal_target_amount
    target_date = _resolve_goal_target_date(extraction.goal_target_date, today)

    # Phase 7 feasibility gate: deterministic affordability check, run only when
    # there's a horizon to spread the target over AND the goal is in the user's
    # currency (cross-currency pushback would mix display currencies and lean on
    # the FX placeholder). The engine decides feasibility; the summary only words
    # it. Non-blocking — the proposal still goes out for the user to confirm.
    assessment: Optional[AffordabilityResult] = None
    context_note = ""
    if target_date is not None and currency == (user.currency or "CRC"):
        months = _months_between(today, target_date)
        assessment = await assess_for_user(
            db, user=user, desired_amount=target, timeline_months=months
        )
        # Phase 7a: a non-blocking heads-up from the financial context (an
        # over-limit envelope, or an upcoming bill/event within the horizon).
        context = await gather_financial_context(
            db, user=user, today=today, horizon_days=min(max(60, months * 30), 365)
        )
        context_note = _goal_context_note(context)

    payload = {
        "action_type": "create_goal",
        "name": extraction.goal_name,
        "target_amount": str(target),
        "target_currency": currency,
        "target_date": target_date.isoformat() if target_date else None,
    }
    summary = _build_goal_summary(
        name=extraction.goal_name,
        target=target,
        currency=currency,
        currency_defaulted=currency_defaulted,
        target_date=target_date,
        today=today,
        assessment=assessment,
        context_note=context_note,
    )
    return ProposeAction(
        action_type="create_goal", payload=payload, summary_es=summary
    )


# ── Phase 6f: conversational recurring-income creation ────────────────────────


_FREQUENCY_LABELS_ES: dict[str, str] = {
    "weekly": "semanal",
    "biweekly": "quincenal",
    "monthly": "mensual",
    "annual": "anual",
}

_DEFAULT_INCOME_NAME: dict[str, str] = {
    "salary": "Salario",
    "freelance": "Freelance",
    "other": "Ingreso",
}


def _next_dom(today: date, day: int) -> date:
    """Next occurrence of day-of-month `day`: this month if still ahead, else
    next month (clamped to the month length)."""
    cur_last = calendar.monthrange(today.year, today.month)[1]
    if today.day <= day <= cur_last:
        return date(today.year, today.month, day)
    nxt = _add_months(date(today.year, today.month, 1), 1)
    nxt_last = calendar.monthrange(nxt.year, nxt.month)[1]
    return date(nxt.year, nxt.month, min(day, nxt_last))


def _resolve_next_payment_date(hint: Optional[str], today: date) -> Optional[date]:
    """Resolve a next-payment-date hint. ISO date, "el N"/"día N" (day of
    month), "fin de mes", "hoy"/"mañana". Anything else → None (clarify)."""
    if not hint:
        return None
    h = hint.strip().lower()

    try:
        return date.fromisoformat(h)
    except ValueError:
        pass

    if h in {"hoy"}:
        return today
    if h in {"mañana", "manana"}:
        return today + timedelta(days=1)

    if "fin de mes" in h or "ultimo dia" in h or "último día" in h:
        cur_last = calendar.monthrange(today.year, today.month)[1]
        eom = date(today.year, today.month, cur_last)
        if eom > today:
            return eom
        nxt = _add_months(date(today.year, today.month, 1), 1)
        nxt_last = calendar.monthrange(nxt.year, nxt.month)[1]
        return date(nxt.year, nxt.month, nxt_last)

    m = re.fullmatch(r"(?:el\s+|d[ií]a\s+|el\s+d[ií]a\s+)?(\d{1,2})", h)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            return _next_dom(today, day)

    return None


def _build_income_summary(
    *,
    name: str,
    amount: Decimal,
    currency: str,
    currency_defaulted: bool,
    frequency: str,
    next_date: date,
    income_type: str,
    gross_amount: Optional[Decimal] = None,
) -> str:
    amt = _format_amount(amount, currency)
    freq = _FREQUENCY_LABELS_ES.get(frequency, frequency)
    if gross_amount is not None:
        # Salary captured as gross: show bruto → neto so the user sees the
        # deductions the deterministic calculator applied.
        gamt = _format_amount(gross_amount, currency)
        lead = (
            f"Ingreso recurrente: {name} — salario bruto {gamt} → neto {amt} "
            f"(después de CCSS e impuesto de renta) ({freq}), "
            f"próximo pago {next_date.isoformat()}."
        )
    else:
        lead = (
            f"Ingreso recurrente: {name} — {amt} ({freq}), "
            f"próximo pago {next_date.isoformat()}."
        )
    if currency_defaulted:
        lead += f" (Usé {currency} por defecto.)"
    if income_type == "salary" and currency == "CRC":
        lead += (
            " Después podés derivar aguinaldo y salario escolar desde la "
            "pestaña Ingresos."
        )
    return lead + " ¿Confirmo?"


def _net_from_gross_salary(gross: Decimal) -> Optional[Decimal]:
    """Net take-home for a CRC gross salary via the deterministic calculator.

    Returns None when the calc can't run (unconfigured year / invalid input),
    so the caller falls back to storing the entered amount as-is. The LLM never
    computes this — it's the pure rules-layer ``compute_net_salary``.
    """
    try:
        breakdown = compute_net_salary(gross_monthly=int(gross))
    except (ValueError, UnconfiguredYearError):
        return None
    return Decimal(breakdown.net_monthly)


def _dispatch_create_income(
    *,
    extraction: ExtractionResult,
    user: User,
    today: date,
) -> DispatcherResult:
    # Resolve type/currency first so the amount prompt can ask for GROSS salary.
    income_type = extraction.income_type or "salary"
    currency = extraction.currency or user.currency
    currency_defaulted = extraction.currency is None
    # CR salary calculator only applies to colón salaries (USD salaries and
    # non-salary income keep the entered amount untouched).
    is_cr_salary = income_type == "salary" and currency == "CRC"

    # Amount, frequency, and next payment date are all NOT NULL on the row,
    # so each is gathered before proposing. Amount reuses the shared field.
    if extraction.amount is None:
        question = (
            "¿De cuánto es tu salario bruto mensual, antes de deducciones? "
            "(ej: '800 mil') — calculo el neto por vos."
            if is_cr_salary
            else "¿De cuánto es cada pago? Decime el monto (ej: '800 mil')."
        )
        return AskClarification(
            question_es=question,
            awaiting_field="amount",
            partial=extraction.model_dump(mode="json"),
        )
    if extraction.income_frequency is None:
        return AskClarification(
            question_es="¿Cada cuánto te pagan? (semanal / quincenal / mensual / anual)",
            awaiting_field="income_frequency",
            partial=extraction.model_dump(mode="json"),
        )
    next_date = _resolve_next_payment_date(extraction.income_next_date, today)
    if next_date is None:
        return AskClarification(
            question_es="¿Cuándo es el próximo pago? (ej: 'el 15', 'fin de mes', o una fecha)",
            awaiting_field="income_next_date",
            partial=extraction.model_dump(mode="json"),
        )

    name = _DEFAULT_INCOME_NAME.get(income_type, "Ingreso")

    # For a CR salary, treat the entered amount as GROSS and store the NET as
    # the income amount (the actual take-home), keeping the gross for re-edit.
    gross_amount: Optional[Decimal] = None
    amount: Decimal = extraction.amount
    if is_cr_salary:
        net = _net_from_gross_salary(extraction.amount)
        if net is not None:
            gross_amount = extraction.amount
            amount = net

    payload = {
        "action_type": "create_income",
        "name": name,
        "income_type": income_type,
        "amount": str(amount),
        "currency": currency,
        "frequency": extraction.income_frequency,
        "next_payment_date": next_date.isoformat(),
    }
    if gross_amount is not None:
        payload["gross_monthly"] = str(gross_amount)

    summary = _build_income_summary(
        name=name,
        amount=amount,
        currency=currency,
        currency_defaulted=currency_defaulted,
        frequency=extraction.income_frequency,
        next_date=next_date,
        income_type=income_type,
        gross_amount=gross_amount,
    )
    return ProposeAction(
        action_type="create_income", payload=payload, summary_es=summary
    )


# ── Phase 6f: conversational recurring-bill (gasto fijo) creation ─────────────


_BILL_FREQUENCY_LABELS_ES: dict[str, str] = {
    "weekly": "semanal",
    "biweekly": "quincenal",
    "monthly": "mensual",
    "bimonthly": "bimestral",
    "quarterly": "trimestral",
    "semiannual": "semestral",
    "annual": "anual",
}

# Recurring bills are predominantly utilities/subscriptions; a valid generic
# default keeps the required+validated category from blocking creation. The
# user recategorizes in the Bills screen.
_DEFAULT_BILL_CATEGORY = "servicios"


def _resolve_bill_category(hint: Optional[str]) -> str:
    if hint:
        h = hint.strip().lower()
        if h in VALID_RECURRING_BILL_CATEGORIES:
            return h
    return _DEFAULT_BILL_CATEGORY


def _build_bill_summary(
    *,
    name: str,
    amount: Decimal,
    currency: str,
    currency_defaulted: bool,
    frequency: str,
    day_of_month: Optional[int],
    category: str,
) -> str:
    amt = _format_amount(amount, currency)
    freq = _BILL_FREQUENCY_LABELS_ES.get(frequency, frequency)
    parts = [f"Gasto fijo: {name} — {amt} ({freq})"]
    if day_of_month:
        parts.append(f"el día {day_of_month}")
    parts.append(f"[{category}]")
    lead = " ".join(parts) + "."
    if currency_defaulted:
        lead += f" (Usé {currency} por defecto.)"
    return lead + " ¿Confirmo?"


def _dispatch_create_bill(
    *,
    extraction: ExtractionResult,
    user: User,
    today: date,
) -> DispatcherResult:
    # amount_expected is required (we don't support is_variable_amount via chat
    # yet) and frequency is NOT NULL, so both are gathered before proposing.
    if extraction.amount is None:
        return AskClarification(
            question_es="¿De cuánto es el recibo? Decime el monto (ej: '18 mil').",
            awaiting_field="amount",
            partial=extraction.model_dump(mode="json"),
        )
    if extraction.bill_frequency is None:
        return AskClarification(
            question_es=(
                "¿Cada cuánto se paga? (mensual / bimestral / trimestral / "
                "semestral / anual / semanal / quincenal)"
            ),
            awaiting_field="bill_frequency",
            partial=extraction.model_dump(mode="json"),
        )
    if not extraction.bill_name:
        return AskClarification(
            question_es="¿Cómo se llama este gasto fijo? (ej: 'Luz', 'Internet', 'Alquiler').",
            awaiting_field="bill_name",
            partial=extraction.model_dump(mode="json"),
        )

    currency = extraction.currency or user.currency
    currency_defaulted = extraction.currency is None
    amount: Decimal = extraction.amount
    category = _resolve_bill_category(extraction.category_hint)
    day_of_month = extraction.bill_day_of_month

    payload = {
        "action_type": "create_bill",
        "name": extraction.bill_name,
        "category": category,
        "amount_expected": str(amount),
        "currency": currency,
        "frequency": extraction.bill_frequency,
        "day_of_month": day_of_month,
        "start_date": today.isoformat(),
    }
    summary = _build_bill_summary(
        name=extraction.bill_name,
        amount=amount,
        currency=currency,
        currency_defaulted=currency_defaulted,
        frequency=extraction.bill_frequency,
        day_of_month=day_of_month,
        category=category,
    )
    return ProposeAction(
        action_type="create_bill", payload=payload, summary_es=summary
    )


# ── Phase 6f: conversational debt creation (chat → native form handoff) ───────


# Voseo, CR. Defined here (not in bot/messages_es) because the dispatcher must
# not import from bot/ — that would invert the bot → api layering — and the
# dispatcher already builds all its other user-facing copy inline.
DEBT_FORM_HANDOFF = (
    "Perfecto, te abro el formulario de préstamo con lo que me diste. "
    "Revisá la tasa de interés y confirmá para registrarlo."
)


async def _debt_financing_advice(
    *,
    extraction: ExtractionResult,
    user: User,
    db: AsyncSession,
    currency: str,
) -> Optional[str]:
    """Phase 7 advise-first: when the register-debt extraction already carries
    principal + rate + term, surface the deterministic cost BEFORE the form so
    the user sees the cuota / total interest / disposable fit, not just a blank
    form. Pure French amortization + the affordability engine; the copy only
    words it. Returns None when there isn't enough to simulate."""
    principal = extraction.debt_principal
    rate_pct = extraction.debt_interest_rate
    term = extraction.debt_term_months
    if principal is None or rate_pct is None or term is None or int(term) < 1:
        return None

    months = int(term)
    p = float(principal)
    monthly_rate = float(rate_pct) / 100.0 / 12.0
    cuota = compute_french_payment(p, monthly_rate, months)
    total_interest = max(0.0, cuota * months - p)
    cuota_d = Decimal(str(round(cuota, 2)))
    interest_d = Decimal(str(round(total_interest, 2)))

    line = (
        f"Ojo: a esa tasa la cuota rondaría {_whole(cuota_d, currency)}/mes y "
        f"pagarías ~{_whole(interest_d, currency)} solo en intereses."
    )

    # Does the cuota fit the envelope-aware surplus? Same single source as every
    # other surface ([[Decision - Unified Monthly Cashflow]]); gated → distinct ask.
    cashflow = await compute_monthly_cashflow(db, user=user)
    if cashflow.reliable:
        safe = cashflow.surplus * SAFETY_MARGIN
        if cuota_d > safe:
            line += (
                f" Supera tu sobrante seguro (~{_whole(safe, currency)}/mes), "
                "así que apretaría."
            )
        else:
            line += " Te cabe dentro de tu sobrante seguro."
    elif cashflow.gate_reason == "no_budget":
        line += " (Armá tus sobres para confirmar si la cuota te cabe en tu presupuesto.)"
    else:  # no_income
        line += " (Registrá tus ingresos para confirmar si la cuota te cabe.)"
    return line


async def _dispatch_create_debt(
    *,
    extraction: ExtractionResult,
    user: User,
    db: AsyncSession,
) -> DispatcherResult:
    """Light extraction → hand off to the native debt form.

    Unlike goal/income/bill, debt does NOT confirm/commit in chat: DebtCreate
    needs six required fields + amortization params the native form gathers.
    We pre-fill what the LLM extracted (any of these may be None — the form
    completes them) and signal the client to open the debt-creation screen.

    Amounts are passed as strings (same convention as the log/create payloads)
    so the mobile form parses them without float drift. interest_rate is a
    PERCENT (`interest_rate_pct`); the form converts it to the 0–1 fraction
    DebtCreate stores. current_balance defaults to the principal for a new loan
    (spec open-question 4)."""
    principal = (
        str(extraction.debt_principal)
        if extraction.debt_principal is not None
        else None
    )
    rate_pct = (
        str(extraction.debt_interest_rate)
        if extraction.debt_interest_rate is not None
        else None
    )
    currency = extraction.currency or user.currency
    prefill = {
        "name": extraction.debt_name,
        "original_amount": principal,
        "current_balance": principal,
        "interest_rate_pct": rate_pct,
        "term_months": extraction.debt_term_months,
        "lender": extraction.debt_lender,
        "currency": currency,
    }

    # Advise-first: if we can already price the loan, lead with the
    # deterministic cost so the form opens informed, not blind.
    advice = await _debt_financing_advice(
        extraction=extraction, user=user, db=db, currency=currency
    )
    message = (
        DEBT_FORM_HANDOFF if advice is None else f"{advice}\n\n{DEBT_FORM_HANDOFF}"
    )
    return OpenScreenAction(
        screen="debt_create",
        prefill=prefill,
        message_es=message,
    )
