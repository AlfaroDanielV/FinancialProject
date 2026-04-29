"""Centralized Redis key naming and TTL constants.

Naming convention: `telegram:<purpose>:<scope>`. Every Phase 5b state that
must survive a restart belongs here — ad-hoc keys in handlers are a smell.
"""
from __future__ import annotations

import uuid

# Pairing code → user_id (one-shot).
PAIRING_TTL_S = 300

# Staged proposal awaiting Sí/No/Editar.
PENDING_TTL_S = 300

# Partial extraction awaiting the user's answer to a follow-up question.
CLARIFICATION_TTL_S = 300

# Last committed action id, for /undo.
LAST_ACTION_TTL_S = 24 * 60 * 60

# Per-user rate limit window.
RATE_WINDOW_S = 60
RATE_LIMIT_PER_WINDOW = 30


def pairing_key(code: str) -> str:
    return f"telegram:pairing:{code}"


def pending_key(user_id: uuid.UUID | str) -> str:
    return f"telegram:pending:{user_id}"


def clarification_key(user_id: uuid.UUID | str) -> str:
    return f"telegram:clarification:{user_id}"


def last_action_key(user_id: uuid.UUID | str) -> str:
    return f"telegram:last_action:{user_id}"


def rate_key(user_id: uuid.UUID | str, minute_bucket: int) -> str:
    return f"telegram:rate:{user_id}:{minute_bucket}"


# `token_budget_key` was removed in bloque 8.5: the daily token budget
# moved to api.services.budget (DB-backed). See docs/phase-6a-decisions.md
# entry 2026-04-29.
