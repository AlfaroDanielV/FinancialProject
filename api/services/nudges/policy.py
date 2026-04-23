"""Anti-saturation policy for Phase 5d engagement nudges.

Four rules, applied in the order listed. Changing any of the constants
here is the one place to change them — the rest of the codebase imports
from this module, never hardcodes.

Rule 1 — GLOBAL RATE LIMIT per user.
    Max one nudge delivered per user every RATE_LIMIT_WINDOW_HOURS,
    UNLESS the nudge's priority is 'high'. Only upcoming_bill with the
    underlying due_date inside UPCOMING_BILL_HIGH_PRIORITY_HOURS earns
    the high label; everything else is 'normal'.

Rule 2 — PER-TYPE SILENCING.
    If a user dismisses the same nudge_type SILENCE_DISMISS_THRESHOLD
    times within SILENCE_LOOKBACK_DAYS, that type is silenced for
    SILENCE_DURATION_DAYS. Silences live in user_nudge_silences; they
    are inserted at the moment the 2nd dismiss crosses the threshold,
    not computed on every evaluator pass.

Rule 3 — QUIET HOURS (per user timezone).
    Nothing is delivered between QUIET_HOURS_START_HOUR and
    QUIET_HOURS_END_HOUR local time. Nudges generated during the quiet
    window stay pending; the next delivery pass sends them.

Rule 4 — DEDUP.
    Two nudges with the same (user_id, dedup_key) never coexist. The
    uq_user_nudges_user_dedup UNIQUE constraint guards this at the DB
    level; evaluators use INSERT ... ON CONFLICT DO NOTHING so repeat
    runs are no-ops.

Evaluator-specific thresholds (what counts as "5 transactions in 7 days",
"48h stale", "72h upcoming") live here too, so the whole policy is
auditable in one screen.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ── Rule 1: global rate limit ────────────────────────────────────────────────
RATE_LIMIT_WINDOW_HOURS = 48


# ── Rule 2: per-type silencing ───────────────────────────────────────────────
SILENCE_DISMISS_THRESHOLD = 2
SILENCE_LOOKBACK_DAYS = 30
SILENCE_DURATION_DAYS = 14


# ── Rule 3: quiet hours (user-local 24h clock) ───────────────────────────────
QUIET_HOURS_START_HOUR = 21  # inclusive
QUIET_HOURS_END_HOUR = 7     # exclusive


# ── Evaluator thresholds ─────────────────────────────────────────────────────
MISSING_INCOME_TXN_WINDOW_DAYS = 7
MISSING_INCOME_MIN_TXN_COUNT = 5
MISSING_INCOME_LOOKBACK_DAYS = 30

STALE_PENDING_THRESHOLD_HOURS = 48

UPCOMING_BILL_WINDOW_HOURS = 72
UPCOMING_BILL_HIGH_PRIORITY_HOURS = 24


# ── Silence reasons (string enum, free-form) ─────────────────────────────────
REASON_AUTO_DISMISSED_2X = "auto_dismissed_2x"
REASON_MANUAL_USER_REQUEST = "manual_user_request"


# ── Helpers: quiet-hours math ────────────────────────────────────────────────


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def is_in_quiet_hours(dt_utc: datetime, tz_name: str) -> bool:
    """True if dt_utc lands inside the user's local quiet window."""
    local = dt_utc.astimezone(_tz(tz_name))
    h = local.hour
    if QUIET_HOURS_START_HOUR < QUIET_HOURS_END_HOUR:
        # Non-wrapping window (not our case today, but defended).
        return QUIET_HOURS_START_HOUR <= h < QUIET_HOURS_END_HOUR
    # Window wraps midnight: 21:00 .. 07:00.
    return h >= QUIET_HOURS_START_HOUR or h < QUIET_HOURS_END_HOUR


def next_delivery_window(dt_utc: datetime, tz_name: str) -> datetime:
    """If dt_utc is outside quiet hours, return it unchanged. Otherwise
    return the next local 07:00, converted back to UTC. Used by the
    delivery worker to decide what to defer."""
    tz = _tz(tz_name)
    if not is_in_quiet_hours(dt_utc, tz_name):
        return dt_utc
    local = dt_utc.astimezone(tz)
    if local.hour >= QUIET_HOURS_START_HOUR:
        target = (local + timedelta(days=1)).replace(
            hour=QUIET_HOURS_END_HOUR, minute=0, second=0, microsecond=0
        )
    else:
        target = local.replace(
            hour=QUIET_HOURS_END_HOUR, minute=0, second=0, microsecond=0
        )
    return target.astimezone(ZoneInfo("UTC"))
