"""Deterministic dispatcher for Telegram bot intents.

Consumes an `ExtractionResult` from the LLM extractor plus user context and
returns one of a small set of `DispatcherResult` variants that describe
what the handler should do next. No LLM calls. No DB writes. No policy
baked into the LLM layer.

The Phase 5b spec's core rule: the LLM extracts, the dispatcher decides.
Everything downstream of this module is deterministic.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from .accounts import resolve_account, list_active
from .llm_extractor import ExtractionResult, Intent
from .transactions import window_bounds


# ── result variants ───────────────────────────────────────────────────────────


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


@dataclass(frozen=True)
class AskClarification:
    """Dispatcher needs one field to proceed. The handler asks the user and
    stages `partial` alongside `awaiting_field` in Redis so the next
    message can be merged into a fresh extraction."""

    question_es: str
    awaiting_field: str  # "amount" | "account" | "intent" | "currency"
    partial: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunQuery:
    """No confirmation needed — execute and reply. Windows are pre-resolved
    to concrete [start, end] dates so the handler never parses strings."""

    query_kind: str  # "recent" | "balance"
    window_start: date
    window_end: date
    limit: int = 5


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
    AskClarification,
    RunQuery,
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

    # Below the confidence floor, clarify for log/query intents rather than
    # guess. The model was honest about its own uncertainty; respect it.
    if extraction.confidence < CONFIDENCE_FLOOR:
        return AskClarification(
            question_es=(
                "No estoy seguro de lo que querés hacer. "
                "¿Es un gasto, un ingreso, o una consulta?"
            ),
            awaiting_field="intent",
            partial=extraction.model_dump(mode="json"),
        )

    if intent is Intent.QUERY_RECENT:
        window_value = extraction.query_window or "this_week"
        start, end = window_bounds(window_value, today)
        return RunQuery(
            query_kind="recent",
            window_start=start,
            window_end=end,
            limit=DEFAULT_RECENT_LIMIT,
        )

    if intent is Intent.QUERY_BALANCE:
        window_value = extraction.query_window or "this_month"
        start, end = window_bounds(window_value, today)
        return RunQuery(
            query_kind="balance",
            window_start=start,
            window_end=end,
        )

    if intent in (Intent.LOG_EXPENSE, Intent.LOG_INCOME):
        return await _dispatch_log(
            extraction=extraction, user=user, today=today, db=db
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

    # 3. Account resolution. None is acceptable when the user has zero
    # accounts configured (the txn goes to account_id=null). Ambiguous
    # matches with multiple accounts → clarify.
    accounts = await list_active(user, db)
    account = await resolve_account(user, extraction.account_hint, db)
    account_ambiguous = (
        len(accounts) > 1 and account is None and bool(extraction.account_hint)
    )
    account_required_but_not_chosen = (
        len(accounts) > 1 and account is None and not extraction.account_hint
    )
    if account_ambiguous or account_required_but_not_chosen:
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
