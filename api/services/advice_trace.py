"""Phase 7e — append-only advice trace.

Every deterministic verdict surfaced to the user is recorded as an
``advice_events`` row with its full input + result payloads, so the engines
stop being stateless oracles and start producing a labeled dataset
(advice → action → outcome; the outcome labeler is future work).

Hard properties:

- **Never blocks, never breaks the advice path.** Recording opens its OWN
  short-lived session and commits independently — the emitting surfaces
  (query tools, the write dispatcher's propose path, nudge evaluators) have
  transaction lifecycles we must not entangle with. Failures are logged
  loudly (no silent failures) and swallowed.
- **Telemetry, not financial state.** This is the same class of write as
  ``llm_query_dispatches`` — the query dispatcher's read-only rule (no user
  financial state mutation) is preserved.
- **Append-only.** Emitting surfaces never update rows; only the future
  outcome-labeling worker will fill ``outcome_*``.

``kind`` values in use: ``assess_purchase``, ``savings_capacity``,
``goal_feasibility``, ``card_analysis``, ``over_commitment_nudge``.
Validation is app-side by design (kinds grow with every advice surface).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

# The settable proxy (defaults to api.database.AsyncSessionLocal) instead of
# the raw factory: tests swap in a per-event-loop NullPool engine via
# set_query_session_factory, and this recorder must work from query tools,
# the write dispatcher AND workers — same independent-session need.
from app.queries.session import AsyncSessionLocal

from api.models.advice_event import AdviceEvent

log = logging.getLogger("api.services.advice_trace")

KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "assess_purchase",
        "savings_capacity",
        "goal_feasibility",
        "card_analysis",
        "over_commitment_nudge",
    }
)


def verdict_from(
    *, feasible: Optional[bool], gate_reason: Optional[str]
) -> str:
    """Canonical verdict string for affordability-shaped results."""
    if gate_reason:
        return f"gated:{gate_reason}"
    if feasible is True:
        return "feasible"
    if feasible is False:
        return "not_feasible"
    return "unknown"


async def record_advice_event(
    *,
    user_id: uuid.UUID,
    kind: str,
    verdict: str,
    inputs: dict[str, Any],
    result: dict[str, Any],
    surface: Optional[str] = None,
    subject_type: Optional[str] = None,
    subject_id: Optional[uuid.UUID] = None,
) -> None:
    """Best-effort append. Safe to call from any surface; never raises."""
    if kind not in KNOWN_KINDS:
        # New kinds are welcome — but flag the unregistered literal loudly so
        # a typo doesn't silently fork the dataset.
        log.warning("advice_event_unknown_kind kind=%s", kind)
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                AdviceEvent(
                    user_id=user_id,
                    kind=kind,
                    verdict=verdict[:30],
                    surface=surface,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    inputs=inputs,
                    result=result,
                )
            )
            await session.commit()
    except Exception:
        log.exception(
            "advice_event_record_failed kind=%s user=%s", kind, user_id
        )
