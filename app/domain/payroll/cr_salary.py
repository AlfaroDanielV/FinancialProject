"""Deterministic Costa Rican net-salary calculator.

Gross monthly salary → full net-pay breakdown (CCSS obrero + impuesto sobre la
renta al trabajo + optional asociación solidarista). This is the rules layer:
NO LLM, NO network, NO DB anywhere in the calculation path. The LLM may only
route to it and read its structured output — it must never recompute or invent a
figure (the core invariant: LLM extracts, rules decide).

Scope: ordinary monthly gross only. Aguinaldo, salario escolar, horas extra,
liquidación and vacaciones are out of scope (aguinaldo/horas extra are also
exempt from the ISR monthly base by law and are not modeled here). Employer-side
cargas sociales (patrono ~26.83%) are out of scope.

ISR base: defaults to the gross salary (official Hacienda default — the 2026
Decreto 45333-H table is labeled "Tramos de renta (salario bruto)"). The
``gross_minus_ccss`` mode nets CCSS out of the base first; many local calculators
(elempleo) and the older Ley 7092 art. 33 reading do this, so we keep it
available but NOT default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from .rates import get_year_rates

IsrBaseMode = Literal["gross", "gross_minus_ccss"]

_WHOLE = Decimal("1")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _round(value: Decimal) -> int:
    """Round to whole colones, half-up (CRC has no minor unit in practice)."""
    return int(value.quantize(_WHOLE, rounding=ROUND_HALF_UP))


# ── structured output ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CcssBreakdown:
    sem: int
    ivm: int
    banco_popular: int
    total: int


@dataclass(frozen=True)
class IsrTramoDetail:
    tramo: int
    rate: float
    taxable_in_tramo: int
    tax_in_tramo: int


@dataclass(frozen=True)
class IsrBreakdown:
    base: int
    tax_before_credits: int
    creditos_aplicados: int
    tax: int
    tramos: tuple[IsrTramoDetail, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SalaryBreakdown:
    gross_monthly: int
    ccss: CcssBreakdown
    isr: IsrBreakdown
    solidarista: int
    net_monthly: int
    effective_rate: float  # total deductions / gross
    year: int
    isr_base_mode: IsrBaseMode


# ── the calculation ────────────────────────────────────────────────────────────


def compute_net_salary(
    *,
    gross_monthly: int,
    year: Optional[int] = None,
    hijos: int = 0,
    conyuge: bool = False,
    isr_base_mode: IsrBaseMode = "gross",
    solidarista_pct: float | Decimal = 0,
) -> SalaryBreakdown:
    """Pure function: gross monthly CRC → net-pay breakdown.

    Args:
        gross_monthly: ordinary monthly gross salary in CRC (> 0).
        year: rate-table year; defaults to the current calendar year. An
            unconfigured year raises ``UnconfiguredYearError``.
        hijos / conyuge: drive the monthly créditos fiscales (subtracted from the
            tax, floored at 0).
        isr_base_mode: ``"gross"`` (default, Hacienda) or ``"gross_minus_ccss"``.
        solidarista_pct: optional employee asociación-solidarista contribution as
            a PERCENT of gross (e.g. 5 for 5%). Reduces take-home pay but does NOT
            change the CCSS or ISR base (it's a voluntary, separate deduction).
    """
    if gross_monthly <= 0:
        raise ValueError("gross_monthly debe ser un entero mayor que cero.")
    if hijos < 0:
        raise ValueError("hijos no puede ser negativo.")
    sol_pct = Decimal(str(solidarista_pct))
    if sol_pct < 0:
        raise ValueError("solidarista_pct no puede ser negativo.")
    if isr_base_mode not in ("gross", "gross_minus_ccss"):
        raise ValueError(
            "isr_base_mode debe ser 'gross' o 'gross_minus_ccss'."
        )

    resolved_year = year if year is not None else date.today().year
    rates = get_year_rates(resolved_year)
    gross = Decimal(gross_monthly)

    # ── CCSS obrero (on gross) ──────────────────────────────────────────────
    sem = _round(gross * rates.ccss.sem)
    ivm = _round(gross * rates.ccss.ivm)
    banco_popular = _round(gross * rates.ccss.banco_popular)
    ccss_total = sem + ivm + banco_popular
    ccss = CcssBreakdown(sem=sem, ivm=ivm, banco_popular=banco_popular, total=ccss_total)

    # ── ISR base ────────────────────────────────────────────────────────────
    if isr_base_mode == "gross":
        isr_base = gross
    else:  # gross_minus_ccss — nets out CCSS first (non-default; documented)
        isr_base = gross - Decimal(ccss_total)

    # ── marginal ISR over the tramos ────────────────────────────────────────
    tramos: list[IsrTramoDetail] = []
    tax_before = _ZERO
    for index, bracket in enumerate(rates.isr_brackets, start=1):
        upper = bracket.hasta if bracket.hasta is not None else isr_base
        taxable = min(isr_base, upper) - bracket.desde
        if taxable < _ZERO:
            taxable = _ZERO
        tax_in = taxable * bracket.rate
        tax_before += tax_in
        # Only surface tramos that actually produce tax. The exempt (0%) slice
        # is implied by isr.base and the first taxed tramo's `desde`.
        if taxable > _ZERO and bracket.rate > _ZERO:
            tramos.append(
                IsrTramoDetail(
                    tramo=index,
                    rate=float(bracket.rate),
                    taxable_in_tramo=_round(taxable),
                    tax_in_tramo=_round(tax_in),
                )
            )

    # ── créditos fiscales (subtract from tax, floor at 0) ───────────────────
    creditos_total = rates.creditos.por_hijo * Decimal(hijos) + (
        rates.creditos.por_conyuge if conyuge else _ZERO
    )
    tax_after = tax_before - creditos_total
    if tax_after < _ZERO:
        tax_after = _ZERO
    creditos_aplicados = tax_before - tax_after  # what was actually used

    isr = IsrBreakdown(
        base=_round(isr_base),
        tax_before_credits=_round(tax_before),
        creditos_aplicados=_round(creditos_aplicados),
        tax=_round(tax_after),
        tramos=tuple(tramos),
    )

    # ── asociación solidarista (optional, on gross; reduces take-home) ──────
    solidarista = _round(gross * sol_pct / _HUNDRED)

    # ── net + effective rate ────────────────────────────────────────────────
    # Net is built from the ROUNDED components so they reconcile exactly:
    # gross == net + ccss.total + isr.tax + solidarista (no ±1 drift).
    net_monthly = gross_monthly - ccss.total - isr.tax - solidarista
    total_deductions = ccss.total + isr.tax + solidarista
    effective_rate = round(total_deductions / gross_monthly, 4)

    return SalaryBreakdown(
        gross_monthly=gross_monthly,
        ccss=ccss,
        isr=isr,
        solidarista=solidarista,
        net_monthly=net_monthly,
        effective_rate=effective_rate,
        year=resolved_year,
        isr_base_mode=isr_base_mode,
    )
