"""P10 B2 — advisory activation: per-turn resolver + continuity session.

Advisory mode is NOT a sticky mode (D12/O5). The spine is per-turn intent:

    advisory_this_turn = flag_enabled
                         AND NOT transactional_lookup(text)
                         AND (session_active OR advisory_question(text))

- A transactional query («¿cuánto tengo?») always gets a plain answer, even
  mid-session — the session never forces coach framing onto a factual lookup.
- The continuity session is an idle-expiring Redis marker started by /asesor
  (or the native "Modo asesor" control) and refreshed on each advisory turn;
  it lapses back to normal on its own. /normal, /cancel and POST /chat/reset
  end it explicitly.
- Detection is DETERMINISTIC (Spanish pattern lists, no LLM): when uncertain,
  default to transactional — the least surprising answer (plan §B2).

Both channels share the same key (`telegram:advisory_session:{user_id}` —
the historical, transport-neutral prefix).
"""
from __future__ import annotations

import unicodedata
import uuid

from redis.asyncio import Redis

from .redis_keys import ADVISORY_SESSION_TTL_S, advisory_session_key


def _fold(text: str) -> str:
    """Lowercase + strip accents so pattern lists stay small."""
    lowered = text.strip().lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(ch) != "Mn"
    )


# Clear factual lookups — always a plain answer, even mid-session. Matched on
# the folded text. Kept to unambiguous balance/spend/list phrasings; a phrase
# like «cuánto necesito ahorrar para la casa» must NOT match (it's planning).
_TRANSACTIONAL_PATTERNS: tuple[str, ...] = (
    "cuanto tengo",
    "cuanto gaste",
    "cuanto he gastado",
    "cuanto llevo gastado",
    "cuanto debo",
    "cuanto me queda",
    "que saldo",
    "mi saldo",
    "el saldo",
    "saldo de",
    "saldos",
    "balance de",
    "ultimos movimientos",
    "ultimas transacciones",
    "movimientos de",
    "mis movimientos",
    "lista de",
    "listame",
    "cuales son mis gastos",
    "desglose",
)

# Planning / what-if / should-I phrasings → advisory framing. Deliberately
# recall-light: missing one costs a plain (still correct) answer; matching a
# transactional one costs coach framing on a lookup — the worse error.
_ADVISORY_PATTERNS: tuple[str, ...] = (
    "me alcanza",
    "me alcanzaria",
    "puedo comprar",
    "podria comprar",
    "puedo pagar",
    "podria pagar",
    "puedo permitirme",
    "me conviene",
    "conviene mas",
    "deberia",
    "vale la pena",
    "que hago con",
    "que puedo hacer con",
    "como salgo",
    "como pago",
    "como puedo ahorrar",
    "ahorrar para",
    "plan para",
    "un plan",
    "planear",
    "planificar",
    "a largo plazo",
    "jubil",  # jubilarme / jubilación
    "pension",
    "retirarme",
    "mi retiro",
    "invertir",
    "inversion",
    "financiar",
    "financiamiento",
    "me endeudo",
    "endeudarme",
    "prima de",
    "casa propia",
    "comprar casa",
    "comprar carro",
    "que pasa si",
    "y si ",
    "escenario",
    "asesor",
    "asesoria",
    "aconsej",
    "consejo",
    "recomend",
    "patrimonio",
    "cuanto necesito",
    "que necesitaria",
)


def is_transactional_lookup(text: str) -> bool:
    folded = _fold(text)
    return any(p in folded for p in _TRANSACTIONAL_PATTERNS)


def is_advisory_question(text: str) -> bool:
    folded = _fold(text)
    return any(p in folded for p in _ADVISORY_PATTERNS)


# ── continuity session (Redis) ────────────────────────────────────────────────


async def start_advisory_session(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.set(
        advisory_session_key(user_id), "1", ex=ADVISORY_SESSION_TTL_S
    )


async def end_advisory_session(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(advisory_session_key(user_id))


async def is_advisory_session_active(*, user_id: uuid.UUID, redis: Redis) -> bool:
    return bool(await redis.exists(advisory_session_key(user_id)))


async def touch_advisory_session(*, user_id: uuid.UUID, redis: Redis) -> None:
    """Refresh the idle TTL — only if a session exists (an implicit advisory
    question never starts a session by itself)."""
    await redis.expire(advisory_session_key(user_id), ADVISORY_SESSION_TTL_S)


# ── the per-turn resolver (the D12 spine) ─────────────────────────────────────


async def advisory_this_turn(
    *, user_id: uuid.UUID, text: str, redis: Redis
) -> bool:
    """True iff THIS turn gets the advisory persona/toolset. Deterministic."""
    from api.config import settings  # local: avoid import-time settings load

    if not settings.advisory_persona_enabled:
        return False
    if is_transactional_lookup(text):
        return False
    if await is_advisory_session_active(user_id=user_id, redis=redis):
        # Refresh the idle window — the user is actively in the conversation.
        await touch_advisory_session(user_id=user_id, redis=redis)
        return True
    return is_advisory_question(text)
