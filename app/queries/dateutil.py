"""Spanish-localized date anchors for the query dispatcher system prompt.

`build_date_context` returns the full set of temporal anchors the LLM needs
to resolve relative date language ("esta semana", "el mes pasado",
"últimos 7 días") into concrete YYYY-MM-DD ranges.

Convention notes (see CLAUDE.md / phase-6a-decisions.md for context):
- Week is ISO (lunes a domingo).
- "Esta semana" = current ISO week, not last 7 days rolling.
- "Este mes" = month-to-date (first of calendar month → today inclusive).
- "Mes pasado" = full calendar month previous.
- All dates are calendar dates in the user's timezone.

No external locale library is used — Babel/locale would be one more
dependency for a 24-string mapping. If we ever need date formatting in
more languages, revisit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_FALLBACK_TZ = "America/Costa_Rica"

_DAYS_ES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)

_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _resolve_tz(user_tz: str | None) -> ZoneInfo:
    """Return a valid ZoneInfo, falling back to Costa Rica on bogus input.

    Mirrors the bot pipeline's behavior so a bad timezone string in the DB
    never crashes the dispatcher.
    """
    try:
        return ZoneInfo(user_tz or _FALLBACK_TZ)
    except Exception:
        return ZoneInfo(_FALLBACK_TZ)


def spanish_long_date(d: date) -> str:
    """'martes 27 de abril de 2026'."""
    return (
        f"{_DAYS_ES[d.weekday()]} {d.day} de "
        f"{_MONTHS_ES[d.month - 1]} de {d.year}"
    )


def spanish_month_name(month: int) -> str:
    return _MONTHS_ES[month - 1]


def _last_day_of_previous_month(today_local: date) -> date:
    return today_local.replace(day=1) - timedelta(days=1)


def _first_day_of_previous_month(today_local: date) -> date:
    last = _last_day_of_previous_month(today_local)
    return last.replace(day=1)


def build_date_context(user_tz: str | None, now: datetime) -> dict[str, str]:
    """Return all temporal anchors for the system prompt.

    Args:
        user_tz: IANA timezone name. Falls back to America/Costa_Rica
            on None or invalid input.
        now: A timezone-aware datetime. Tests can pin this to a fixed
            instant; production passes datetime.now(tz=UTC) or similar.

    Returns:
        Dict with keys: today, yesterday, this_week_start, this_week_end,
        last_week_start, last_week_end, last_7_days_start, last_7_days_end,
        this_month_start, this_month_end, last_month_start, last_month_end,
        this_year_start, this_year_end, header_text, time_text,
        timezone_name. All YYYY-MM-DD strings except the *_text fields.

    Convention: this_month_end == today (month-to-date). last_month_end
    is the last calendar day of the previous month.
    """
    tz = _resolve_tz(user_tz)
    local = now.astimezone(tz)
    today = local.date()

    yesterday = today - timedelta(days=1)

    # ISO week: Monday=0 ... Sunday=6.
    this_week_start = today - timedelta(days=today.weekday())
    this_week_end = this_week_start + timedelta(days=6)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(days=1)

    last_7_days_end = today
    last_7_days_start = today - timedelta(days=6)

    this_month_start = today.replace(day=1)
    this_month_end = today  # month-to-date

    last_month_end = _last_day_of_previous_month(today)
    last_month_start = _first_day_of_previous_month(today)

    this_year_start = today.replace(month=1, day=1)
    this_year_end = today.replace(month=12, day=31)

    header_text = spanish_long_date(today)
    time_text = local.strftime("%H:%M")

    return {
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "this_week_start": this_week_start.isoformat(),
        "this_week_end": this_week_end.isoformat(),
        "last_week_start": last_week_start.isoformat(),
        "last_week_end": last_week_end.isoformat(),
        "last_7_days_start": last_7_days_start.isoformat(),
        "last_7_days_end": last_7_days_end.isoformat(),
        "this_month_start": this_month_start.isoformat(),
        "this_month_end": this_month_end.isoformat(),
        "last_month_start": last_month_start.isoformat(),
        "last_month_end": last_month_end.isoformat(),
        "this_year_start": this_year_start.isoformat(),
        "this_year_end": this_year_end.isoformat(),
        "header_text": header_text,
        "time_text": time_text,
        "timezone_name": str(tz.key),
    }
