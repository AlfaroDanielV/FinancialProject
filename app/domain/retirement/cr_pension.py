"""Deterministic Costa Rican retirement projection (rules layer).

Projects the monthly pension at age 65 as the sum of two pillars:

- **IVM** (CCSS, defined benefit): a deterministic function of the reference
  salary + cuotas, straight from the Reglamento del Seguro de IVM. Same in every
  scenario — a defined benefit does not depend on markets.
- **ROP** (defined contribution): the individual balance is projected to 65 under
  the operadora's **three SUPEN scenarios** (pesimista / esperado / optimista) and
  converted to a monthly Retiro-Programado pension. The bands come entirely from
  the ROP return spread.

This is the rules layer: NO LLM, NO network, NO DB anywhere in the calculation
path. The advisory LLM may only route to it and narrate its structured output —
it must never recompute or invent a figure (LLM extracts, rules decide). And per
the SUPEN mandate the output is **always three bands, never a single confident
number** (``SUPEN_DISCLAIMER_ES`` is carried on every projection).

⚠️ Precision note: unlike ``cr_salary`` (exact to the colón by law), a *projection*
compounds over decades under assumptions, so figures are approximate — computed in
float and rounded to whole colones only at the boundary. The balance→pension step
uses a documented annuity stand-in for SUPEN's VANU table (see
``rates.DecumulationAssumptions``); B6 is ⛳ operator-validated before user-visible.
Full provenance: ``docs/phase-10-b6-cr-pension-constants.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from .rates import (
    DECUMULATION_APPROX,
    DEFAULT_OPC,
    SUPEN_DISCLAIMER_ES,
    IvmRules,
    RopMethodology,
    get_ivm_rules,
    get_rop_methodology,
)

Sexo = Literal["M", "F"]

_WHOLE = Decimal("1")
_ZERO = Decimal("0")

# The three SUPEN scenarios, in the mandated order (never reordered — the
# pesimista band must never be dropped from narration).
ESCENARIOS: tuple[str, ...] = ("pesimista", "esperado", "optimista")


def _round(value: Decimal | float) -> int:
    """Round to whole colones, half-up."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return int(value.quantize(_WHOLE, rounding=ROUND_HALF_UP))


# ── structured output ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IvmProjection:
    salario_referencia: int
    cuotas: int
    elegible: bool                 # False if cuotas < mínimo proporcional
    es_proporcional: bool          # True when 180 ≤ cuotas < 300 (reduced)
    cuantia_basica_pct: float
    cuantia_adicional_pct: float
    postergacion_pct: float        # the pre-cap component percentages sum to
    pension_antes_topes: int       # pension_antes_topes / SPR (before any tope)
    aplico_minimo: bool            # money floor bound
    aplico_maximo: bool            # ordinary money ceiling bound
    aplico_cap: bool               # 125%-of-SPR pct cap bound (see components note)
    pension_mensual: int
    salario_minimo_usado: int      # the bracket-table divisor actually applied
    salario_minimo_year: Optional[int]  # its vintage (None = caller-supplied)
    year: int


@dataclass(frozen=True)
class RopScenario:
    escenario: str                 # pesimista | esperado | optimista
    tasa_nominal: float
    tasa_real: float
    densidad: float
    aporte_mensual: int            # today's colones (real)
    saldo_proyectado_vp: int       # balance at 65, present value (today's colones)
    pension_mensual: int           # Retiro Programado (VANU stand-in)


@dataclass(frozen=True)
class RetirementBand:
    escenario: str
    pension_ivm: int
    pension_rop: int
    pension_total: int
    tasa_reemplazo: float          # pension_total / salario_base


@dataclass(frozen=True)
class Supuestos:
    """Assumptions carried on a projection (frozen — no mutable dict, per the
    app/domain/payroll all-immutable precedent)."""

    opc: str
    metodologia: str
    inflacion: float
    aporte_rop: float
    ivm_year: int
    vanu: str


@dataclass(frozen=True)
class RetirementProjection:
    edad_actual: int
    edad_retiro: int
    meses_hasta_retiro: int
    salario_base: int
    ivm: IvmProjection
    rop: tuple[RopScenario, ...]           # pesimista, esperado, optimista
    bandas: tuple[RetirementBand, ...]     # pesimista, esperado, optimista
    opc: str
    year: int
    supuestos: Supuestos
    disclaimer: str


# ── IVM (defined benefit) ────────────────────────────────────────────────────


def _cuantia_basica_pct(rules: IvmRules, multiplo: Decimal) -> Decimal:
    """Bracket lookup: first bracket whose exclusive upper bound exceeds the
    multiple; the open-ended top bracket (``hasta_multiplo=None``) catches the
    rest. Lower earners land in the higher-rate brackets."""
    for bracket in rules.cuantia_basica:
        if bracket.hasta_multiplo is None or multiplo < bracket.hasta_multiplo:
            return bracket.rate
    return rules.cuantia_basica[-1].rate  # unreachable (last is open-ended)


def compute_ivm_pension(
    *,
    salario_referencia: int,
    cuotas: int,
    year: Optional[int] = None,
    meses_postergacion: int = 0,
    salario_minimo: Optional[int | float | Decimal] = None,
    rules: Optional[IvmRules] = None,
) -> IvmProjection:
    """Pure IVM (CCSS) monthly pension.

    Args:
        salario_referencia: the SPR = average of the best 300 inflation-adjusted
            salaries (CRC, > 0). The caller supplies it; this function does not
            reconstruct the salary history.
        cuotas: months contributed at retirement.
        year: IVM rules year (default: current calendar year). Unconfigured →
            ``UnconfiguredYearError``.
        meses_postergacion: months the retirement is deferred past eligibility
            (adds the postergación bonus, only when the full right is met).
        salario_minimo: the "salario mínimo de ocupación no calificada" divisor
            for the bracket table. Defaults to the versioned constant — pass the
            current MTSS-decree value explicitly until the 2026 value is confirmed
            (see the constants doc §7).
        rules: inject an ``IvmRules`` directly (e.g. ``IVM_PROPUESTA_2026`` for a
            "what if the reform passes" projection). Overrides ``year``.
    """
    if salario_referencia <= 0:
        raise ValueError("salario_referencia debe ser mayor que cero.")
    if cuotas < 0:
        raise ValueError("cuotas no puede ser negativo.")
    if meses_postergacion < 0:
        raise ValueError("meses_postergacion no puede ser negativo.")

    resolved_year = year if year is not None else date.today().year
    ivm = rules if rules is not None else get_ivm_rules(resolved_year)
    spr = Decimal(salario_referencia)
    if salario_minimo is not None:
        divisor = Decimal(str(salario_minimo))
        salmin_year: Optional[int] = None  # caller-supplied — vintage unknown here
    else:
        divisor = ivm.salario_minimo_referencia
        salmin_year = ivm.salario_minimo_year
    # A non-positive divisor must fail loudly, not fall through to multiplo=0 (which
    # would hand back the lowest-earner 52,5% bracket — a plausible wrong number).
    if divisor <= _ZERO:
        raise ValueError("salario_minimo debe ser mayor que cero.")
    salmin_usado = _round(divisor)

    # Not enough cuotas for any pension.
    if cuotas < ivm.cuotas_min_proporcional:
        return IvmProjection(
            salario_referencia=salario_referencia,
            cuotas=cuotas,
            elegible=False,
            es_proporcional=False,
            cuantia_basica_pct=0.0,
            cuantia_adicional_pct=0.0,
            postergacion_pct=0.0,
            pension_antes_topes=0,
            aplico_minimo=False,
            aplico_maximo=False,
            aplico_cap=False,
            pension_mensual=0,
            salario_minimo_usado=salmin_usado,
            salario_minimo_year=salmin_year,
            year=ivm.year,
        )

    multiplo = spr / divisor
    basica_pct = _cuantia_basica_pct(ivm, multiplo)

    es_proporcional = cuotas < ivm.cuotas_full
    aplico_cap = False
    if es_proporcional:
        # 180 ≤ cuotas < 300: ordinary pension × (cuotas / cuotas_full). No
        # cuantía adicional and no postergación (the full right is not consolidated).
        adicional_pct = _ZERO
        postergacion_pct = _ZERO
        factor = Decimal(cuotas) / Decimal(ivm.cuotas_full)
        pension_pct_uncapped = basica_pct * factor
        pension_pct = pension_pct_uncapped
    else:
        adicional_pct = ivm.cuantia_adicional_pct_mensual * Decimal(cuotas - ivm.cuotas_full)
        postergacion_pct = ivm.postergacion_pct_mensual * Decimal(meses_postergacion)
        pension_pct_uncapped = basica_pct + adicional_pct + postergacion_pct
        # Cap: ordinaria + adicional + postergación ≤ 125% del salario de referencia.
        pension_pct = min(pension_pct_uncapped, ivm.postergacion_cap_pct_spr)
        aplico_cap = pension_pct < pension_pct_uncapped

    # pension_antes_topes reflects the UNcapped percentage, so the exposed
    # component pcts (básica + adicional + postergación) reconcile with it; the
    # three aplico_* flags then explain the gap down to pension_mensual.
    pension_antes = spr * pension_pct_uncapped
    pension_final = spr * pension_pct  # after the 125% pct cap

    aplico_minimo = False
    aplico_maximo = False
    # The ordinary money minimum applies to a consolidated (full) pension, not to a
    # deliberately-reduced proporcional benefit (art. 29 — ⛳-pending, doc §7).
    if not es_proporcional and pension_final < ivm.pension_minima:
        pension_final = ivm.pension_minima
        aplico_minimo = True
    # Ordinary money ceiling applies whenever NO postergación bonus was granted
    # (incl. the proporcional branch, where postergación is always 0). With
    # postergación the 125% pct cap governs instead.
    if postergacion_pct == _ZERO and pension_final > ivm.pension_maxima_ordinaria:
        pension_final = ivm.pension_maxima_ordinaria
        aplico_maximo = True

    return IvmProjection(
        salario_referencia=salario_referencia,
        cuotas=cuotas,
        elegible=True,
        es_proporcional=es_proporcional,
        cuantia_basica_pct=float(basica_pct),
        cuantia_adicional_pct=float(adicional_pct),
        postergacion_pct=float(postergacion_pct),
        pension_antes_topes=_round(pension_antes),
        aplico_minimo=aplico_minimo,
        aplico_maximo=aplico_maximo,
        aplico_cap=aplico_cap,
        pension_mensual=_round(pension_final),
        salario_minimo_usado=salmin_usado,
        salario_minimo_year=salmin_year,
        year=ivm.year,
    )


# ── ROP (defined contribution — three scenarios) ─────────────────────────────


def _months_of_life(sexo: Optional[Sexo]) -> int:
    if sexo == "M":
        return DECUMULATION_APPROX.esperanza_vida_meses_hombre
    if sexo == "F":
        return DECUMULATION_APPROX.esperanza_vida_meses_mujer
    return DECUMULATION_APPROX.esperanza_vida_meses_blend


def _annuity_factor(months: int, monthly_rate: float) -> float:
    """Present value of ₡1/month for ``months`` at ``monthly_rate`` (VANU stand-in)."""
    if months <= 0:
        return 1.0
    if abs(monthly_rate) < 1e-12:
        return float(months)
    return (1.0 - (1.0 + monthly_rate) ** (-months)) / monthly_rate


def project_rop(
    *,
    saldo_actual: int,
    salario_mensual: int,
    edad_actual: int,
    opc: str = DEFAULT_OPC,
    year: Optional[int] = None,
    sexo: Optional[Sexo] = None,
    edad_retiro: Optional[int] = None,
    methodology: Optional[RopMethodology] = None,
) -> tuple[RopScenario, ...]:
    """Project the ROP balance to retirement under the three SUPEN scenarios.

    Works in REAL terms (returns net of the operadora's inflation assumption),
    holding contributions constant in today's colones, so the projected balance is
    already a present value — matching SUPEN's Retiro-Programado-at-present-value
    mandate. Contributions = ``salario_mensual × aporte_rop × densidad``.

    Args:
        saldo_actual: current ROP balance (CRC, ≥ 0).
        salario_mensual: current monthly salary that the 4,25% aporte is based on
            (also the replacement-ratio denominator upstream), CRC > 0.
        edad_actual: current age in whole years.
        opc / year: which operadora's annual methodology (default Popular, current
            year). Unknown → ``UnknownOperatorError``.
        sexo: "M"/"F" picks the life-expectancy for the annuity; ``None`` = blended.
        edad_retiro: override the retirement age (default: the methodology's 65).
        methodology: inject a ``RopMethodology`` directly (overrides opc/year).
    """
    if saldo_actual < 0:
        raise ValueError("saldo_actual no puede ser negativo.")
    if salario_mensual <= 0:
        raise ValueError("salario_mensual debe ser mayor que cero.")
    if edad_actual <= 0:
        raise ValueError("edad_actual debe ser mayor que cero.")

    resolved_year = year if year is not None else date.today().year
    meth = methodology if methodology is not None else get_rop_methodology(opc, resolved_year)
    retiro = edad_retiro if edad_retiro is not None else meth.edad_retiro
    if retiro <= 0:
        raise ValueError("edad_retiro debe ser mayor que cero.")
    if edad_actual > retiro:
        # Already past the retirement age — this is a projection TO retirement, so
        # an out-of-range age is a caller error, not a silent "project as if today".
        raise ValueError("edad_actual no puede ser mayor que la edad de retiro.")

    n = max(0, (retiro - edad_actual) * 12)
    inflacion = float(meth.inflacion)
    aporte = float(meth.aporte_rop)
    life_months = _months_of_life(sexo)
    rm_post = (1.0 + float(DECUMULATION_APPROX.tasa_real_post_retiro)) ** (1.0 / 12.0) - 1.0
    annuity = _annuity_factor(life_months, rm_post)

    scenarios: list[RopScenario] = []
    for escenario in ESCENARIOS:
        params = meth.scenario(escenario)
        r_nom = float(params.tasa_nominal)
        densidad = float(params.densidad)
        r_real = (1.0 + r_nom) / (1.0 + inflacion) - 1.0
        rm = (1.0 + r_real) ** (1.0 / 12.0) - 1.0
        aporte_mensual = float(salario_mensual) * aporte * densidad

        if n == 0:
            fv = float(saldo_actual)
        elif abs(rm) < 1e-12:
            fv = float(saldo_actual) + aporte_mensual * n
        else:
            growth = (1.0 + rm) ** n
            fv = float(saldo_actual) * growth + aporte_mensual * ((growth - 1.0) / rm)

        pension = fv / annuity if annuity > 0 else 0.0
        scenarios.append(
            RopScenario(
                escenario=escenario,
                tasa_nominal=r_nom,
                tasa_real=round(r_real, 6),
                densidad=densidad,
                aporte_mensual=_round(aporte_mensual),
                saldo_proyectado_vp=_round(fv),
                pension_mensual=_round(pension),
            )
        )
    return tuple(scenarios)


# ── composition: the full projection (3 bands) ──────────────────────────────


def project_retirement(
    *,
    salario_referencia: int,
    cuotas: int,
    saldo_rop: int,
    salario_mensual: int,
    edad_actual: int,
    opc: str = DEFAULT_OPC,
    year: Optional[int] = None,
    sexo: Optional[Sexo] = None,
    meses_postergacion: int = 0,
    salario_minimo: Optional[int | float | Decimal] = None,
    edad_retiro: Optional[int] = None,
) -> RetirementProjection:
    """Full retirement projection = IVM (defined benefit) + ROP (three scenarios).

    Returns three total bands (pesimista / esperado / optimista); the spread comes
    entirely from the ROP return scenarios (IVM is identical across bands). The
    replacement ratio is ``pension_total / salario_mensual``. Carries
    ``SUPEN_DISCLAIMER_ES`` — narration must present a band and its uncertainty,
    never a single confident number.
    """
    if salario_mensual <= 0:
        raise ValueError("salario_mensual debe ser mayor que cero.")

    resolved_year = year if year is not None else date.today().year
    ivm = compute_ivm_pension(
        salario_referencia=salario_referencia,
        cuotas=cuotas,
        year=resolved_year,
        meses_postergacion=meses_postergacion,
        salario_minimo=salario_minimo,
    )
    meth = get_rop_methodology(opc, resolved_year)
    retiro = edad_retiro if edad_retiro is not None else meth.edad_retiro
    rop = project_rop(
        saldo_actual=saldo_rop,
        salario_mensual=salario_mensual,
        edad_actual=edad_actual,
        opc=opc,
        year=resolved_year,
        sexo=sexo,
        edad_retiro=retiro,
    )

    bandas: list[RetirementBand] = []
    for scenario in rop:
        total = ivm.pension_mensual + scenario.pension_mensual
        bandas.append(
            RetirementBand(
                escenario=scenario.escenario,
                pension_ivm=ivm.pension_mensual,
                pension_rop=scenario.pension_mensual,
                pension_total=total,
                tasa_reemplazo=round(total / salario_mensual, 4),
            )
        )

    supuestos = Supuestos(
        opc=meth.opc,
        metodologia=meth.source,
        inflacion=float(meth.inflacion),
        aporte_rop=float(meth.aporte_rop),
        ivm_year=ivm.year,
        vanu="aproximación (renta a valor presente sobre esperanza de vida CR); "
        "pendiente tabla VANU oficial de SUPEN",
    )

    return RetirementProjection(
        edad_actual=edad_actual,
        edad_retiro=retiro,
        meses_hasta_retiro=max(0, (retiro - edad_actual) * 12),
        salario_base=salario_mensual,
        ivm=ivm,
        rop=rop,
        bandas=tuple(bandas),
        opc=meth.opc,
        year=resolved_year,
        supuestos=supuestos,
        disclaimer=SUPEN_DISCLAIMER_ES,
    )
