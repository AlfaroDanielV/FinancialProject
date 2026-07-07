"""Clarification round-trip state.

When the dispatcher returns AskClarification, the pipeline stashes the
partial extraction + awaiting_field in Redis so the user's next message is
merged back in instead of re-extracted as a fresh intent. Re-extraction
loses context: "Promerica Visa Platinum" on its own has intent=unknown.

Deterministic on purpose. The LLM already ran once on the original message;
the clarification reply answers a specific known question, so keyword
matching (for intent/currency) or raw pass-through (for account) is enough
and stays inside the Phase 5b "dispatcher stays deterministic" rule.

TTL is enforced by Redis (see CLARIFICATION_TTL_S). On timeout the next
user message just runs through the normal extractor — no special handling.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from redis.asyncio import Redis

from api.models.user import User
from api.services.llm_extractor import (
    GOAL_NO_DATE_SENTINEL,
    ExtractionResult,
    Intent,
)

from .redis_keys import CLARIFICATION_TTL_S, clarification_key


@dataclass
class ClarificationState:
    """What we stashed when the last dispatch returned AskClarification.

    `partial` is the full serialized ExtractionResult (model_dump(mode="json"))
    from that dispatch. `question_es` is re-sent verbatim when the user's
    reply can't be interpreted.

    Phase 7f: `options` are tappable answers (account names) rendered as
    buttons on both channels; `nonce` rejects stale Telegram taps
    (`clarify:{nonce}:{idx}`). Both default empty so pre-7f states and
    questions without options keep working.
    """

    partial: dict[str, Any]
    awaiting_field: str
    question_es: str
    options: list[str] = dataclass_field(default_factory=list)
    nonce: str = ""
    # SMART goals: how many times we've re-asked THIS question after an
    # uninterpretable reply. Bails to help copy after a cap so a digit-free
    # answer can't trap the user in an endless loop. Defaulted → pre-existing
    # states stay valid (same precedent as `options`/`nonce`).
    attempts: int = 0
    # SMART goals infeasible decision-point: the two deterministic alternatives
    # the affordability engine computed, stashed so the chosen chip rewrites the
    # right field WITHOUT the merge step re-running the engine. Empty otherwise.
    goal_alt_date: str = ""
    goal_alt_amount: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "ClarificationState":
        return cls(**json.loads(raw))


async def save_clarification(
    *, user_id: uuid.UUID, state: ClarificationState, redis: Redis
) -> None:
    await redis.setex(
        clarification_key(user_id), CLARIFICATION_TTL_S, state.to_json()
    )


async def load_clarification(
    *, user_id: uuid.UUID, redis: Redis
) -> Optional[ClarificationState]:
    raw = await redis.get(clarification_key(user_id))
    if not raw:
        return None
    try:
        return ClarificationState.from_json(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def clear_clarification(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(clarification_key(user_id))


def merge_reply(
    state: ClarificationState, text: str, user: User
) -> Optional[ExtractionResult]:
    """Fold the user's free-text reply into `state.partial` based on
    `awaiting_field`. Returns a fresh ExtractionResult on success; None
    when the reply can't be interpreted so the caller can re-ask.
    """
    reply = text.strip()
    if not reply:
        return None

    merged = dict(state.partial)
    field = state.awaiting_field

    if field == "account":
        # Raw pass-through. resolve_account does rapidfuzz matching over
        # the user's active accounts; if the reply is nonsense it returns
        # None and the dispatcher asks again.
        merged["account_hint"] = reply
    elif field == "transfer_from":
        # Phase 7b transfers — raw pass-through; the dispatcher fuzzy-matches
        # and re-asks listing the account names if the reply is nonsense.
        merged["transfer_from_hint"] = reply
    elif field == "transfer_to":
        merged["transfer_to_hint"] = reply
    elif field == "reallocate_from":
        # Phase 8 B4 reallocation — raw pass-through; the dispatcher matches the
        # sobre by name and re-asks (listing the envelope names) if unmatched.
        merged["reallocate_from_hint"] = reply
    elif field == "reallocate_to":
        merged["reallocate_to_hint"] = reply
    elif field == "amount":
        amount = _parse_amount_es(reply)
        if amount is None:
            return None
        merged["amount"] = str(amount)
    elif field == "intent":
        intent = _parse_intent_es(reply)
        if intent is None:
            return None
        merged["intent"] = intent.value
        # A direct answer to "¿es gasto, ingreso o consulta?" is
        # higher-confidence than whatever the original fuzzy extraction
        # was. Bump above CONFIDENCE_FLOOR so the dispatcher acts.
        merged["confidence"] = 0.8
        if intent in (Intent.LOG_EXPENSE, Intent.LOG_INCOME):
            merged["dispatcher"] = "write"
        elif intent is Intent.QUERY:
            merged["dispatcher"] = "query"
        else:
            merged["dispatcher"] = "control"
    elif field == "goal_target_amount":
        # Phase 6f conversational goal creation — same amount parser as the
        # transaction amount clarification.
        amount = _parse_amount_es(reply)
        if amount is None:
            return None
        merged["goal_target_amount"] = str(amount)
    elif field == "goal_name":
        # Raw pass-through; the goal name is whatever the user typed. The
        # dispatcher re-asks if it's junk (a bare amount / a confirm word).
        merged["goal_name"] = reply
    elif field == "goal_target_date":
        # SMART-T: the deadline the user picked or typed. "Sin fecha" (or a bare
        # no) is an explicit opt-out → a sentinel the dispatcher treats as
        # "proceed without a deadline"; anything else is a raw hint the
        # dispatcher re-resolves (and re-asks on if unparseable / in the past).
        if _is_no_date(reply):
            merged["goal_target_date"] = GOAL_NO_DATE_SENTINEL
        else:
            merged["goal_target_date"] = reply
    elif field == "goal_infeasible":
        # SMART-A decision-point: the user chose how to make an unaffordable
        # goal affordable (or to create it anyway). The alternatives were
        # computed by the engine and stashed on the state — this only routes
        # the choice onto the right field; the re-dispatch re-checks feasibility.
        choice = _parse_goal_infeasible_choice(reply)
        if choice is None:
            return None
        if choice == "extend" and state.goal_alt_date:
            merged["goal_target_date"] = state.goal_alt_date
        elif choice == "reduce" and state.goal_alt_amount:
            merged["goal_target_amount"] = state.goal_alt_amount
        elif choice == "force":
            merged["goal_force_create"] = True
        else:
            return None
    elif field == "income_frequency":
        # Phase 6f conversational income creation.
        freq = _parse_frequency_es(reply)
        if freq is None:
            return None
        merged["income_frequency"] = freq
    elif field == "income_next_date":
        # Raw pass-through; the dispatcher re-resolves and re-asks if needed.
        merged["income_next_date"] = reply
    elif field == "bill_frequency":
        freq = _parse_bill_frequency_es(reply)
        if freq is None:
            return None
        merged["bill_frequency"] = freq
    elif field == "bill_name":
        merged["bill_name"] = reply
    elif field == "bill_target":
        # Flexible Payment Dates — mark_bill_paid target. Raw pass-through; the
        # dispatcher re-resolves the bill by name and re-asks if it still misses.
        merged["bill_target_hint"] = reply
    elif field == "debt_target":
        # Flexible Payment Dates — record_debt_payment target. Raw pass-through.
        merged["debt_target_hint"] = reply
    elif field == "transfer_direction":
        # Transfer-receipt direction the dispatcher couldn't derive — the user
        # picked ingreso / gasto / entre mis cuentas. Set the intent and clear
        # the receipt flag so the re-dispatch routes directly (no re-run of the
        # direction rule, no re-ask).
        intent = _parse_transfer_direction_es(reply)
        if intent is None:
            return None
        merged["intent"] = intent.value
        merged["dispatcher"] = "write"
        merged["confidence"] = 0.9
        merged["is_transfer_receipt"] = False
        if intent is Intent.LOG_INCOME and not merged.get("merchant"):
            merged["merchant"] = merged.get("sender_name")
        elif intent is Intent.LOG_EXPENSE and not merged.get("merchant"):
            merged["merchant"] = merged.get("recipient_name")
    else:
        return None

    try:
        return ExtractionResult.model_validate(merged)
    except Exception:
        return None


# ── tiny Spanish parsers ──────────────────────────────────────────────────────
# Kept small on purpose. If a user's reply slips past these, we re-ask; we
# don't grow the tables preemptively (see YAGNI normalization memory).


_AMOUNT_RE = re.compile(r"(-?\d+(?:[.,]\d+)*)")
# Word-boundary SEARCH (not fullmatch): a clarification reply is often a phrase,
# not a bare number — "2 millones para diciembre" must still parse to 2 000 000,
# not silently fall to _AMOUNT_RE and create a ₡2 goal (the 2026-07 goal bug).
# `\b` after "mil" won't match inside "millones" (both sides word chars), and
# _MILLON_RE is checked first, so "mil"/"k" and "millón/millones" stay disjoint.
_MIL_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mil|k)\b", re.IGNORECASE)
_MILLON_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mill[oó]n(?:es)?)\b", re.IGNORECASE
)


def _parse_amount_es(text: str) -> Optional[Decimal]:
    t = text.strip().lower()
    for sym in ("₡", "$", "crc", "usd", "colones", "dólares", "dolares"):
        t = t.replace(sym, "")
    t = t.strip()

    millon = _MILLON_RE.search(t)
    if millon:
        base = millon.group(1).replace(",", ".")
        try:
            return Decimal(base) * 1_000_000
        except InvalidOperation:
            return None

    mil = _MIL_RE.search(t)
    if mil:
        base = mil.group(1).replace(",", ".")
        try:
            return Decimal(base) * 1000
        except InvalidOperation:
            return None

    m = _AMOUNT_RE.search(t)
    if not m:
        return None
    raw = m.group(1)

    # CR convention: "72.679,00" means 72679.00. Normalize before Decimal.
    if "." in raw and "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            raw = parts[0] + "." + parts[1]
        else:
            raw = raw.replace(",", "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")

    try:
        v = Decimal(raw)
    except InvalidOperation:
        return None
    if v <= 0:
        return None
    return v


# GOAL_NO_DATE_SENTINEL is imported from the extractor package (shared with the
# api dispatcher; see its definition there). "Sin fecha" / a bare no maps to it.
_NO_DATE_WORDS = frozenset(
    {"sin fecha", "sin plazo", "ninguna", "ninguno", "no", "no sé", "no se"}
)


def _is_no_date(text: str) -> bool:
    return text.strip().lower() in _NO_DATE_WORDS


# The three infeasible-goal chips → a stable choice token. Order matters: the
# "reduce" keyword ("bajar la meta") and "extend" ("extender el plazo") are
# checked before the catch-all "force" so a longer phrase can't be misread.
def _parse_goal_infeasible_choice(text: str) -> Optional[str]:
    t = text.strip().lower()
    if any(kw in t for kw in ("extend", "plazo", "más tiempo", "mas tiempo")):
        return "extend"
    if any(kw in t for kw in ("bajar", "baja", "reducir", "menos", "meta más")):
        return "reduce"
    if any(kw in t for kw in ("igual", "así", "asi", "tal cual", "crear", "crea")):
        return "force"
    return None


_INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.LOG_EXPENSE: (
        "gasto",
        "gasté",
        "gaste",
        "pagué",
        "pague",
        "compré",
        "compre",
        "compra",
    ),
    Intent.LOG_INCOME: (
        "ingreso",
        "me pagaron",
        "recibí",
        "recibi",
        "entró",
        "entro",
        "salario",
    ),
    Intent.QUERY: (
        "últimas",
        "ultimas",
        "recientes",
        "movimientos",
        "balance",
        "total",
        "consulta",
        "cuánto",
        "cuanto",
    ),
}


def _parse_intent_es(text: str) -> Optional[Intent]:
    t = text.strip().lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return intent
    return None


# Direction of a transfer receipt: ingreso / gasto / transferencia interna.
# Checked transfer-first so "entre mis cuentas" wins over a stray income/expense
# keyword.
_TRANSFER_DIRECTION_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.LOG_TRANSFER: (
        "entre mis", "mis cuentas", "entre cuentas", "interna", "propias",
        "transferencia entre",
    ),
    Intent.LOG_INCOME: (
        "ingreso", "recib", "me transfir", "me pagaron", "entró", "entro",
        "me lleg",
    ),
    Intent.LOG_EXPENSE: (
        "gasto", "gasté", "gaste", "pagué", "pague", "envié", "envie",
        "mandé", "mande", "le pasé", "le pase",
    ),
}


def _parse_transfer_direction_es(text: str) -> Optional[Intent]:
    t = text.strip().lower()
    for intent in (Intent.LOG_TRANSFER, Intent.LOG_INCOME, Intent.LOG_EXPENSE):
        if any(kw in t for kw in _TRANSFER_DIRECTION_KEYWORDS[intent]):
            return intent
    return None


# Frequency keywords, checked biweekly-first so "quincenal" wins over a bare
# "semana"/"mes" partial. Spanish → recurring_incomes enum.
_FREQUENCY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "biweekly": ("quincenal", "quincena", "cada quincena", "bisemanal", "cada 15", "cada dos semanas"),
    "weekly": ("semanal", "cada semana", "por semana", "semana"),
    "monthly": ("mensual", "cada mes", "al mes", "por mes", "mes"),
    "annual": ("anual", "cada año", "al año", "por año", "una vez al año", "año"),
}


def _parse_frequency_es(text: str) -> Optional[str]:
    t = text.strip().lower()
    for freq in ("biweekly", "weekly", "monthly", "annual"):
        if any(kw in t for kw in _FREQUENCY_KEYWORDS[freq]):
            return freq
    return None


# Recurring bills add bimonthly/quarterly/semiannual on top of the income set.
# "monthly" is checked last (its keywords are specific — "mensual"/"cada mes" —
# so they don't collide with bi/tri/semestral).
_BILL_FREQUENCY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "biweekly": ("quincenal", "quincena", "cada quincena"),
    "weekly": ("semanal", "cada semana", "por semana", "semana"),
    "bimonthly": ("bimestral", "bimensual", "cada dos meses", "cada 2 meses"),
    "quarterly": ("trimestral", "cada trimestre", "cada tres meses", "cada 3 meses"),
    "semiannual": ("semestral", "cada semestre", "cada seis meses", "cada 6 meses"),
    "annual": ("anual", "cada año", "al año", "por año", "una vez al año", "año"),
    "monthly": ("mensual", "cada mes", "al mes", "por mes"),
}


def _parse_bill_frequency_es(text: str) -> Optional[str]:
    t = text.strip().lower()
    for freq in (
        "biweekly", "weekly", "bimonthly", "quarterly",
        "semiannual", "annual", "monthly",
    ):
        if any(kw in t for kw in _BILL_FREQUENCY_KEYWORDS[freq]):
            return freq
    return None
