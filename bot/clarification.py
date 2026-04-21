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
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from redis.asyncio import Redis

from api.models.user import User
from api.services.llm_extractor import ExtractionResult, Intent

from .redis_keys import CLARIFICATION_TTL_S, clarification_key


@dataclass
class ClarificationState:
    """What we stashed when the last dispatch returned AskClarification.

    `partial` is the full serialized ExtractionResult (model_dump(mode="json"))
    from that dispatch. `question_es` is re-sent verbatim when the user's
    reply can't be interpreted.
    """

    partial: dict[str, Any]
    awaiting_field: str
    question_es: str

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
_MIL_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(mil|k)\s*$", re.IGNORECASE)


def _parse_amount_es(text: str) -> Optional[Decimal]:
    t = text.strip().lower()
    for sym in ("₡", "$", "crc", "usd", "colones", "dólares", "dolares"):
        t = t.replace(sym, "")
    t = t.strip()

    mil = _MIL_RE.match(t)
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
    Intent.QUERY_RECENT: (
        "últimas",
        "ultimas",
        "recientes",
        "movimientos",
    ),
    Intent.QUERY_BALANCE: (
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
