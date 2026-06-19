"""Year-keyed Costa Rican payroll rate tables (CCSS obrero + ISR asalariados).

These change annually (e.g. IVM moved 4.17% → 4.33% on 2026-01-01). They are
deliberately DATA, not logic: adding a 2027 set is an edit to ``_RATES`` below,
never a change to ``cr_salary.py``. The calculator takes a ``year`` and raises
``UnconfiguredYearError`` for any year without a table — we never silently fall
back to a stale year.

Sources (2026):
- CCSS obrero 10.83% = SEM 5.50% + IVM 4.33% + Banco Popular Obrero 1.00%.
- ISR asalariados monthly, marginal — Decreto 45333-H, "Tramos de renta
  (salario bruto)".
- Créditos fiscales mensuales: por hijo ₡1.710, por cónyuge ₡2.590.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class CcssRates:
    """Employee-side (obrero) CCSS contribution rates, as fractions of gross."""

    sem: Decimal
    ivm: Decimal
    banco_popular: Decimal

    @property
    def total(self) -> Decimal:
        return self.sem + self.ivm + self.banco_popular


@dataclass(frozen=True)
class IsrBracket:
    """One marginal ISR tramo. ``hasta=None`` means the open-ended top tramo.

    The rate applies ONLY to the portion of the base within
    ``(desde, hasta]`` — progressive, never flat-on-bracket.
    """

    desde: Decimal
    hasta: Optional[Decimal]
    rate: Decimal


@dataclass(frozen=True)
class CreditosFiscales:
    """Monthly tax credits subtracted from the computed ISR (floored at 0)."""

    por_hijo: Decimal
    por_conyuge: Decimal


@dataclass(frozen=True)
class YearRates:
    year: int
    ccss: CcssRates
    isr_brackets: tuple[IsrBracket, ...]
    creditos: CreditosFiscales


class UnconfiguredYearError(ValueError):
    """Raised when no rate table is configured for the requested year."""


# ── the data ─────────────────────────────────────────────────────────────────
# To add 2027: append a YearRates entry. Do NOT touch the calculator.

_RATES: dict[int, YearRates] = {
    2026: YearRates(
        year=2026,
        ccss=CcssRates(
            sem=Decimal("0.0550"),
            ivm=Decimal("0.0433"),
            banco_popular=Decimal("0.0100"),
        ),
        isr_brackets=(
            IsrBracket(Decimal("0"), Decimal("918000"), Decimal("0.00")),
            IsrBracket(Decimal("918000"), Decimal("1347000"), Decimal("0.10")),
            IsrBracket(Decimal("1347000"), Decimal("2364000"), Decimal("0.15")),
            IsrBracket(Decimal("2364000"), Decimal("4727000"), Decimal("0.20")),
            IsrBracket(Decimal("4727000"), None, Decimal("0.25")),
        ),
        creditos=CreditosFiscales(
            por_hijo=Decimal("1710"),
            por_conyuge=Decimal("2590"),
        ),
    ),
}


def get_year_rates(year: int) -> YearRates:
    try:
        return _RATES[year]
    except KeyError as exc:
        configured = ", ".join(str(y) for y in sorted(_RATES))
        raise UnconfiguredYearError(
            f"No hay tablas de impuestos configuradas para el año {year}. "
            f"Años disponibles: {configured}. Agregá las tasas en "
            f"app/domain/payroll/rates.py."
        ) from exc


def configured_years() -> tuple[int, ...]:
    return tuple(sorted(_RATES))
