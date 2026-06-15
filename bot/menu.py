"""Chat `/menu` + `/resumen` — deterministic, LLM-free command surfaces.

`/menu` shows tappable shortcuts (commands + example prompts). In the native
chat each item is a chip whose label is posted back as a message, so tapping it
"sends" the command/prompt; the `open_screen="menu"` marker tells the app to
keep those chips repeatable (you can tap several). `/resumen` asks for a period
then returns a plain-text expense table for it.

Both run in `process_message`'s command short-circuit (before the LLM) and are
mirrored by thin Telegram handlers (`bot/handlers.py`). No LLM, no writes.

Import note: this module imports the reply dataclasses from `bot.pipeline` at
top level; `bot.pipeline` imports this module LAZILY (inside `process_message`),
so there is no import cycle.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.fx import convert
from api.services.transactions import PeriodExpenseRow, period_expense_breakdown

from . import messages_es
from .formatting import format_amount
from .pipeline import BotReply, ConfirmButton, OpenScreen


# Items posted verbatim when tapped. Commands route through process_message's
# short-circuit; prompts go to the LLM like any typed question.
_MENU_COMMANDS: list[str] = ["/resumen", "/deshacer", "/cancelar", "/help"]
_MENU_PROMPTS: list[str] = [
    "¿Cuánto gasté esta semana?",
    "¿Cuánto me queda en mis sobres?",
    "¿Cuál es mi patrón de gastos?",
    "¿Cuáles son mis movimientos sin cuenta?",
    "Registrá ₡5.000 en el súper",
    "¿Me alcanza para ahorrar 2 millones este año?",
]

# Native chips post these; Telegram auto-links the same underscore commands.
_PERIOD_CHIPS = ["/resumen_mes", "/resumen_semana", "/resumen_hoy"]

_MONTHS_ES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "set", "oct", "nov", "dic",
]

# A `menu`-screen open_screen marker keeps the chips repeatable in the native
# chat (Telegram ignores open_screen). callback_data is inert — the native chat
# posts the chip LABEL as text; there is no `menu:` callback handler by design.
_MENU_SCREEN = "menu"


def _inert(label: str) -> ConfirmButton:
    return ConfirmButton(label, f"menu:{label}")


def build_menu_reply() -> BotReply:
    """Native chat menu: short intro + tappable chips + the `menu` marker."""
    buttons = [_inert(c) for c in _MENU_COMMANDS] + [_inert(p) for p in _MENU_PROMPTS]
    return BotReply(
        text=messages_es.MENU_INTRO,
        buttons=buttons,
        open_screen=OpenScreen(screen=_MENU_SCREEN, prefill={}),
    )


def build_menu_text() -> str:
    """Telegram menu: plain text. Telegram auto-links the /comandos; prompts are
    examples to type."""
    commands = "\n".join(_MENU_COMMANDS)
    prompts = "\n".join(f"• {p}" for p in _MENU_PROMPTS)
    return (
        f"{messages_es.MENU_INTRO}\n\n"
        f"Comandos:\n{commands}\n\n"
        f"Ejemplos (escribilos):\n{prompts}"
    )


def _user_today(user: User) -> date:
    try:
        tz = ZoneInfo(user.timezone)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _fmt_date(value: date) -> str:
    return f"{value.day} {_MONTHS_ES[value.month - 1]}"


def _parse_period(text: str) -> str:
    """'/resumen' | '/resumen mes' | '/resumen_semana' → '' | 'mes' | 'semana' | 'hoy'."""
    rest = text.strip().lower().removeprefix("/resumen")
    return rest.lstrip("_ ").strip()


def _resolve_window(period: str, today: date) -> tuple[date, date, str]:
    """(start, end inclusive, human label). Default → semana (ISO week)."""
    if period in {"mes", "mensual", "month"}:
        return today.replace(day=1), today, "este mes"
    if period in {"hoy", "dia", "día", "day", "today"}:
        return today, today, "hoy"
    return today - timedelta(days=today.weekday()), today, "esta semana"


def _envelope_cell(name: str | None) -> str:
    return name if name else messages_es.MENU_NO_ENVELOPE_EMOJI


def _format_table(
    rows: list[PeriodExpenseRow], *, user_currency: str, title: str, range_label: str
) -> str:
    # Total in the user's currency so a mixed-currency period still adds up.
    total = sum(
        (convert(r.amount.copy_abs(), r.currency, user_currency) for r in rows),
        Decimal("0"),
    )
    head = (
        f"Resumen — {title} ({range_label})\n"
        f"Total: {format_amount(total, user_currency)} en {len(rows)} gasto(s)\n\n"
        f"{messages_es.RESUMEN_TABLE_HEADER}"
    )
    lines = [
        " · ".join(
            [
                format_amount(r.amount, r.currency),
                r.category or "Sin categoría",
                _fmt_date(r.txn_date),
                _envelope_cell(r.envelope_name),
            ]
        )
        for r in rows
    ]
    return head + "\n" + "\n".join(lines)


async def handle_resumen(text: str, *, user: User, db: AsyncSession) -> BotReply:
    """`/resumen` → period chips; `/resumen <period>` → the expense table."""
    period = _parse_period(text)
    if not period:
        return BotReply(
            text=messages_es.RESUMEN_ASK_PERIOD,
            buttons=[_inert(c) for c in _PERIOD_CHIPS],
            open_screen=OpenScreen(screen=_MENU_SCREEN, prefill={}),
        )

    today = _user_today(user)
    start, end, label = _resolve_window(period, today)
    rows = await period_expense_breakdown(db, user_id=user.id, start=start, end=end)
    if not rows:
        return BotReply(text=messages_es.RESUMEN_EMPTY_TPL.format(period=label))

    range_label = (
        _fmt_date(start) if start == end else f"{_fmt_date(start)}–{_fmt_date(end)}"
    )
    user_currency = getattr(user, "currency", None) or "CRC"
    return BotReply(
        text=_format_table(
            rows, user_currency=user_currency, title=label, range_label=range_label
        )
    )
