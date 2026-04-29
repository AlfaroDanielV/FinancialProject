"""Unit tests for app.queries.dateutil.

Pin the temporal anchors against fixed datetimes so tests are deterministic.
The expected values for 2026-04-27 (martes) come from the Phase 6a Block 6
spec — these are the anchors the LLM relies on, regression-detect any drift.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.queries.dateutil import (
    build_date_context,
    spanish_long_date,
    spanish_month_name,
)


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_anchors_for_tuesday_2026_04_27_costa_rica() -> None:
    # 2026-04-27 18:00 UTC == 12:00 America/Costa_Rica (UTC-6, no DST).
    ctx = build_date_context("America/Costa_Rica", _utc(2026, 4, 27, 18, 0))

    assert ctx["today"] == "2026-04-27"
    assert ctx["yesterday"] == "2026-04-26"

    # Tuesday → ISO week is Mon 2026-04-27 ... wait, 2026-04-27 IS a Monday.
    # Verify carefully: 2026-04-27 = Monday (weekday=0).
    # If the user runs us on a Monday, this_week_start == today.
    assert ctx["this_week_start"] == "2026-04-27"
    assert ctx["this_week_end"] == "2026-05-03"
    assert ctx["last_week_start"] == "2026-04-20"
    assert ctx["last_week_end"] == "2026-04-26"

    assert ctx["last_7_days_start"] == "2026-04-21"
    assert ctx["last_7_days_end"] == "2026-04-27"

    # Month-to-date: starts at first of April, ends today (not Apr 30).
    assert ctx["this_month_start"] == "2026-04-01"
    assert ctx["this_month_end"] == "2026-04-27"

    # Last full calendar month.
    assert ctx["last_month_start"] == "2026-03-01"
    assert ctx["last_month_end"] == "2026-03-31"

    assert ctx["this_year_start"] == "2026-01-01"
    assert ctx["this_year_end"] == "2026-12-31"

    assert ctx["timezone_name"] == "America/Costa_Rica"


def test_anchors_for_tuesday_2026_04_28() -> None:
    # 2026-04-28 is a Tuesday.
    ctx = build_date_context("America/Costa_Rica", _utc(2026, 4, 28, 18, 0))

    # ISO week containing Tuesday 2026-04-28 is Mon 2026-04-27 → Sun 2026-05-03.
    assert ctx["this_week_start"] == "2026-04-27"
    assert ctx["this_week_end"] == "2026-05-03"

    # Month-to-date.
    assert ctx["this_month_start"] == "2026-04-01"
    assert ctx["this_month_end"] == "2026-04-28"


def test_january_first_falls_back_to_december_for_last_month() -> None:
    # 2026-01-01, last month must roll back into 2025-12.
    ctx = build_date_context("America/Costa_Rica", _utc(2026, 1, 1, 18, 0))

    assert ctx["today"] == "2026-01-01"
    assert ctx["last_month_start"] == "2025-12-01"
    assert ctx["last_month_end"] == "2025-12-31"
    assert ctx["this_year_start"] == "2026-01-01"
    assert ctx["this_year_end"] == "2026-12-31"


def test_late_evening_in_user_tz_resolves_correct_local_date() -> None:
    # 2026-04-28 04:00 UTC == 2026-04-27 22:00 America/Costa_Rica.
    # Local date is still the 27th — the user is "still on Monday" locally.
    ctx = build_date_context("America/Costa_Rica", _utc(2026, 4, 28, 4, 0))

    assert ctx["today"] == "2026-04-27"
    assert ctx["this_month_end"] == "2026-04-27"
    # Header text is in user TZ.
    assert "lunes 27 de abril" in ctx["header_text"]


def test_invalid_timezone_falls_back_to_costa_rica() -> None:
    ctx = build_date_context("Mars/Phobos", _utc(2026, 4, 27, 18, 0))
    assert ctx["timezone_name"] == "America/Costa_Rica"
    assert ctx["today"] == "2026-04-27"


def test_none_timezone_falls_back_to_costa_rica() -> None:
    ctx = build_date_context(None, _utc(2026, 4, 27, 18, 0))
    assert ctx["timezone_name"] == "America/Costa_Rica"


def test_header_uses_spanish_locale() -> None:
    # 2026-04-27 is a Monday.
    ctx = build_date_context("America/Costa_Rica", _utc(2026, 4, 27, 18, 0))
    assert ctx["header_text"].startswith("lunes")
    assert "abril" in ctx["header_text"]
    assert "2026" in ctx["header_text"]
    # Time text is HH:MM in user TZ.
    assert ctx["time_text"] == "12:00"


def test_spanish_long_date_helper() -> None:
    from datetime import date

    assert spanish_long_date(date(2026, 4, 27)) == "lunes 27 de abril de 2026"
    assert spanish_long_date(date(2026, 12, 1)) == "martes 1 de diciembre de 2026"


def test_spanish_month_name_helper() -> None:
    assert spanish_month_name(1) == "enero"
    assert spanish_month_name(4) == "abril"
    assert spanish_month_name(12) == "diciembre"


@pytest.mark.parametrize(
    "now_utc,expected_iso_week",
    [
        # Monday 2026-04-27 → starts Apr 27.
        (_utc(2026, 4, 27, 18, 0), ("2026-04-27", "2026-05-03")),
        # Sunday 2026-05-03 → starts Apr 27 (still inside same ISO week).
        (_utc(2026, 5, 3, 18, 0), ("2026-04-27", "2026-05-03")),
        # Wednesday 2026-04-29 → starts Apr 27.
        (_utc(2026, 4, 29, 18, 0), ("2026-04-27", "2026-05-03")),
    ],
)
def test_iso_week_boundaries_monday_to_sunday(
    now_utc: datetime, expected_iso_week: tuple[str, str]
) -> None:
    ctx = build_date_context("America/Costa_Rica", now_utc)
    assert (ctx["this_week_start"], ctx["this_week_end"]) == expected_iso_week
