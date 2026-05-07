"""Onboarding state machine for the /conectar_gmail flow.

Lives entirely in Redis (TTL 30 min) so a webhook restart mid-onboarding
doesn't lose the user's place. State transitions (post-addenda):

    awaiting_oauth   → user has been shown the consent URL; we're
                       waiting for the callback to fire.
    oauth_done       → callback succeeded; brief intermediate state,
                       usually replaced by selecting_banks within the
                       same handler turn.
    selecting_banks  → user is choosing banks (preset taps + custom
                       emails). pending_senders accumulates here.
    confirming       → user tapped "Listo"; bot showed the sender list
                       and is waiting for tap on Activar / Editar /
                       Cancelar.
    active           → integration is live. We DROP the onboarding key
                       at this point — gmail_credentials.activated_at
                       is the source of truth from here on.

The pre-addenda `awaiting_sample` state is gone. Sample collection
moved to a separate, optional `awaiting_optional_sample` flow driven by
`/agregar_muestra` (Block D), with its own state machine entry point.

Why not a Postgres table: short-lived UI state. A Postgres row per
onboarding session would be tens of inserts/day max but pure ephemera —
"what message do we expect next from this user". Redis is the right
home; the schema stays lean.

Storage shape: a flat JSON document under `gmail_onboarding:{user_id}`.
Renewing TTL on every transition is the safest default — a slow user
mid-flow gets a full 30 min from their last action, not from the
initial /conectar_gmail.

`pending_senders` shape:
    list of {"email": str, "bank_name": str | None, "source": str}
ordered by add-time. We store as list (not set) because we need the
order to render the summary back to the user, and JSON has no native
set anyway. Idempotent at append-time: handlers de-dup on email before
appending.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis

from .redis_keys import GMAIL_ONBOARDING_TTL_S, gmail_onboarding_key


# Allowed transitions. Source of truth for the state graph; any caller
# trying to jump outside this map raises early.
#
# `selecting_banks → selecting_banks` is allowed because every preset
# tap or custom-email message bumps us through the same state to refresh
# the TTL (and the JSON payload). Treating it as a no-op transition
# keeps the call sites uniform.
_TRANSITIONS: dict[str, set[str]] = {
    "awaiting_oauth": {"oauth_done", "selecting_banks"},
    "oauth_done": {"selecting_banks"},
    "selecting_banks": {"selecting_banks", "confirming"},
    "confirming": {"selecting_banks", "active"},
}


@dataclass
class OnboardingState:
    state: str
    telegram_chat_id: int
    started_at: str
    # post-addenda fields. Default empty so existing-on-disk JSON from
    # the old schema still loads via from_json with sensible defaults.
    pending_senders: list[dict[str, Any]] = field(default_factory=list)
    # Telegram message_id of the active bank-selection prompt. Stored so
    # we can edit the message in place when the user taps a preset
    # (instead of spamming the chat with a new prompt every tap).
    selection_message_id: Optional[int] = None
    # Sub-state inside selecting_banks: when the user just tapped a
    # preset bank button, we remember which one so the next text
    # message they send is associated with that bank. Cleared once the
    # email lands. None means "next text uses domain inference".
    awaiting_bank: Optional[str] = None
    # Pre-addenda fields kept for backwards-compat of any in-flight
    # state JSON that survived a deploy. They're unused in the new flow.
    sample_attempts: int = 0
    pending_analysis: Optional[dict[str, Any]] = field(default=None)
    last_sample_id: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "OnboardingState":
        data = json.loads(raw)
        # Tolerate forward-compat fields by ignoring anything we don't
        # know — `cls(**data)` would raise on unknown keys otherwise.
        # We don't need them; the writer is always the same module.
        known = {f for f in cls.__dataclass_fields__}
        cleaned = {k: v for k, v in data.items() if k in known}
        return cls(**cleaned)


# ── helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get(user_id: uuid.UUID, redis: Redis) -> Optional[OnboardingState]:
    raw = await redis.get(gmail_onboarding_key(user_id))
    if raw is None:
        return None
    return OnboardingState.from_json(raw)


async def set_state(
    user_id: uuid.UUID, state: OnboardingState, redis: Redis
) -> None:
    """Write the state with a fresh TTL.

    Always renews the TTL — `state` represents user activity, so 30 min
    of inactivity is the timeout window. Persistent (non-renewing) TTL
    would punish slow users.
    """
    await redis.set(
        gmail_onboarding_key(user_id),
        state.to_json(),
        ex=GMAIL_ONBOARDING_TTL_S,
    )


async def clear(user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(gmail_onboarding_key(user_id))


# ── state transitions ────────────────────────────────────────────────────────


class InvalidTransition(RuntimeError):
    """Caller tried to move from state A to a state not in _TRANSITIONS[A]."""


async def begin(
    *, user_id: uuid.UUID, telegram_chat_id: int, redis: Redis
) -> OnboardingState:
    """Start onboarding fresh. If a stale state exists for this user, we
    overwrite it — the new /conectar_gmail call wins."""
    state = OnboardingState(
        state="awaiting_oauth",
        telegram_chat_id=telegram_chat_id,
        started_at=_now_iso(),
    )
    await set_state(user_id, state, redis)
    return state


async def transition(
    *, user_id: uuid.UUID, to: str, redis: Redis
) -> OnboardingState:
    """Move the user's onboarding to `to`, validating the transition.

    Raises InvalidTransition if the current state can't reach `to`. If
    no state is in Redis, raises FileNotFoundError-style RuntimeError —
    callers handle this as "session expired".
    """
    current = await get(user_id, redis)
    if current is None:
        raise RuntimeError("no onboarding session for user")

    allowed = _TRANSITIONS.get(current.state, set())
    if to not in allowed:
        raise InvalidTransition(
            f"cannot transition from {current.state!r} to {to!r}"
        )

    current.state = to
    if to == "awaiting_sample":
        # Resetting pending_analysis on each ask keeps confirming-state
        # cleanups simple: only the most recent analysis is staged.
        current.pending_analysis = None
    await set_state(user_id, current, redis)
    return current


async def add_pending_sender(
    *,
    user_id: uuid.UUID,
    email: str,
    bank_name: Optional[str],
    source: str,
    redis: Redis,
) -> tuple[OnboardingState, bool]:
    """Append (email, bank, source) to pending_senders if not already
    present. Returns (state, was_new). Idempotent on `email`.

    Soft-checks the cap at the call site, not here — the handler
    decides what to say to the user when the cap is hit.
    """
    current = await get(user_id, redis)
    if current is None:
        raise RuntimeError("no onboarding session for user")
    if current.state != "selecting_banks":
        raise InvalidTransition(
            f"add_pending_sender only valid from selecting_banks "
            f"(was {current.state!r})"
        )
    norm = email.strip().lower()
    for entry in current.pending_senders:
        if entry.get("email") == norm:
            return current, False
    current.pending_senders.append(
        {"email": norm, "bank_name": bank_name, "source": source}
    )
    await set_state(user_id, current, redis)
    return current, True


async def remove_pending_sender(
    *, user_id: uuid.UUID, email: str, redis: Redis
) -> OnboardingState:
    """Drop a sender from pending_senders. No-op if not present."""
    current = await get(user_id, redis)
    if current is None:
        raise RuntimeError("no onboarding session for user")
    norm = email.strip().lower()
    current.pending_senders = [
        e for e in current.pending_senders if e.get("email") != norm
    ]
    await set_state(user_id, current, redis)
    return current


async def set_selection_message_id(
    *, user_id: uuid.UUID, message_id: int, redis: Redis
) -> None:
    """Remember which Telegram message holds the live bank-selection
    keyboard. Used by handlers to edit-in-place on each preset tap."""
    current = await get(user_id, redis)
    if current is None:
        raise RuntimeError("no onboarding session for user")
    current.selection_message_id = message_id
    await set_state(user_id, current, redis)


async def set_awaiting_bank(
    *, user_id: uuid.UUID, bank_name: Optional[str], redis: Redis
) -> OnboardingState:
    """Mark the user as waiting to type the email for `bank_name`. Pass
    None to clear (e.g. after the email lands)."""
    current = await get(user_id, redis)
    if current is None:
        raise RuntimeError("no onboarding session for user")
    if current.state != "selecting_banks":
        raise InvalidTransition(
            f"set_awaiting_bank only valid from selecting_banks "
            f"(was {current.state!r})"
        )
    current.awaiting_bank = bank_name
    await set_state(user_id, current, redis)
    return current
