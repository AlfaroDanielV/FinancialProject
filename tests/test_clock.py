"""Unit tests for the timezone-aware date boundary helper.

The server runs in UTC; Costa Rica is UTC−6. A capture at 23:30 CR on the 19th
is 05:30Z on the 20th — a naive `date.today()` would report the 20th. These
tests freeze `now` at that instant and assert `user_today` reports the CR day.

See `Decision - Timezone-Aware Date Boundaries (Server UTC vs CR)`.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

from api.services import clock

# 05:30Z on 2026-06-20 == 23:30 CR on 2026-06-19 (UTC−6). The naive UTC date is
# already the 20th; the user's calendar day is still the 19th.
_FROZEN_UTC = _dt.datetime(2026, 6, 20, 5, 30, tzinfo=_dt.timezone.utc)
_CR_DAY = _dt.date(2026, 6, 19)


class _FrozenDateTime:
    """Stand-in for `datetime` whose `.now(tz)` is pinned to `_FROZEN_UTC`."""

    @classmethod
    def now(cls, tz=None):
        return _FROZEN_UTC.astimezone(tz) if tz is not None else _FROZEN_UTC


def _freeze(monkeypatch):
    monkeypatch.setattr(clock, "datetime", _FrozenDateTime)


def test_user_today_uses_cr_calendar_day_near_midnight(monkeypatch):
    _freeze(monkeypatch)
    user = SimpleNamespace(timezone="America/Costa_Rica")
    assert clock.user_today(user) == _CR_DAY  # the 19th, not the UTC 20th


def test_user_today_falls_back_to_cr_on_invalid_timezone(monkeypatch):
    _freeze(monkeypatch)
    # A null/bogus tz must fall back to CR (not UTC — UTC is the bug).
    assert clock.user_today(SimpleNamespace(timezone="Not/A_Zone")) == _CR_DAY
    assert clock.user_today(SimpleNamespace(timezone=None)) == _CR_DAY


def test_today_cr_for_userless_callers(monkeypatch):
    _freeze(monkeypatch)
    assert clock.today_cr() == _CR_DAY


def test_cr_tz_is_costa_rica():
    assert str(clock.CR_TZ) == "America/Costa_Rica"
