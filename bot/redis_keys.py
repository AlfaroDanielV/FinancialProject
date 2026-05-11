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


# ── Gmail onboarding (Phase 6b) ──────────────────────────────────────────────
# State machine for the /conectar_gmail flow. Set on /conectar_gmail,
# advanced by the OAuth callback (via pubsub) and subsequent samples,
# cleared when activation completes or /desconectar_gmail runs.
GMAIL_ONBOARDING_TTL_S = 30 * 60


def gmail_onboarding_key(user_id: uuid.UUID | str) -> str:
    return f"gmail_onboarding:{user_id}"


# /revisar_correos cooldown: 1 manual scan per user every 30 min.
GMAIL_MANUAL_SCAN_COOLDOWN_S = 30 * 60


def gmail_manual_scan_cooldown_key(user_id: uuid.UUID | str) -> str:
    return f"gmail_manual_scan_cooldown:{user_id}"


# Gmail sender discovery: exploratory keyword scans are wider than normal
# whitelisted scans, so keep a separate short cooldown.
GMAIL_DISCOVERY_COOLDOWN_S = 10 * 60


def gmail_discovery_cooldown_key(user_id: uuid.UUID | str) -> str:
    return f"gmail_discovery_cooldown:{user_id}"


# Shadow summary accumulator. The notifier appends transaction IDs
# created during shadow window. The daily worker reads the previous
# day's set at 8am CR and sends it as a digest. TTL gives us 48h to
# recover from a missed cron run before we'd lose data.
GMAIL_SHADOW_SUMMARY_TTL_S = 48 * 60 * 60


def gmail_shadow_summary_key(
    user_id: uuid.UUID | str, date_iso: str
) -> str:
    """`date_iso` is YYYY-MM-DD in the user's timezone (or UTC if we
    don't know — see notifier for the resolution)."""
    return f"gmail_shadow_summary:{user_id}:{date_iso}"


# ── Memory commands (Phase 6c B7) ────────────────────────────────────────────
# /editar_memoria stashes the insight_id the user is editing here while
# waiting for their natural-language correction. Cleared on success,
# /cancel, or TTL expiry. Same 5-minute window as pending/clarification —
# anything longer and the user has forgotten what they tapped.
MEMORY_EDIT_TTL_S = 300


def memory_edit_key(user_id: uuid.UUID | str) -> str:
    return f"telegram:memory_edit:{user_id}"


# /recalcular_memoria cooldown (Phase 6c B10): one manual nightly recompute
# per user per hour. The full ACA Job runs nightly anyway — this is a
# user-driven "warm me up" trigger, not a way to thrash compute. Atomic
# SETNX with this TTL gives us the rate limit without bookkeeping.
RECALC_COOLDOWN_S = 3600


def recalc_cooldown_key(user_id: uuid.UUID | str) -> str:
    return f"telegram:recalc_cooldown:{user_id}"


# /agregar_muestra optional sample collection.
# Set on /agregar_muestra; cleared on next photo/text from same user OR
# on TTL expiry. Independent of the onboarding state machine.
#
# Indexed by telegram_user_id (BIGINT) on purpose: the message filter
# runs on every bot text and the most common case is "no, not in this
# state". Avoiding a DB lookup-by-tg-id-for-user.id in the filter is a
# real perf win on the hot path.
GMAIL_OPTIONAL_SAMPLE_TTL_S = 10 * 60


def gmail_optional_sample_key(telegram_user_id: int | str) -> str:
    return f"gmail_optional_sample:tg:{telegram_user_id}"
