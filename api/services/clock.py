"""Single source for the user's local calendar date.

The server runs in UTC; Costa Rica is UTC−6, so after 18:00 CR a naive
`date.today()` (server-local = UTC) already reports tomorrow. Absolute
timestamps (`created_at`, etc.) stay UTC — they are instants, not calendar
days. Any user-facing "today" (a captured `transaction_date`, an anchor
`effective_date`, a dashboard month boundary) must be resolved here, at the
boundary, from `user.timezone`.

See `Decision - Timezone-Aware Date Boundaries (Server UTC vs CR)`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # avoid a runtime import cycle; only needed for the hint
    from api.models.user import User

CR_TZ = ZoneInfo("America/Costa_Rica")


def user_today(user: "User") -> date:
    """The user's local calendar date.

    Falls back to Costa Rica (not UTC) when `user.timezone` is null/invalid —
    a UTC fallback is the very bug this module fixes, and the product is
    CR-first. Every 5a-migrated row already carries a valid tz.
    """
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = CR_TZ
    return datetime.now(tz).date()


def today_cr() -> date:
    """Costa Rica's calendar date — for callers with no user in scope."""
    return datetime.now(CR_TZ).date()
