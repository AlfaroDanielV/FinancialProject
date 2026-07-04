"""Year-keyed Costa Rican pension constants (IVM defined-benefit + ROP DC).

Two natures, kept apart on purpose:

- **IVM (CCSS basic regime)** is a *defined benefit* fixed by the Reglamento del
  Seguro de IVM — deterministic LAW. The formula (bracket table, cuantía
  adicional, postergación) has been stable since the 2021 reform took force on
  2024-01-11; only the operational params (min/max pension, the salario-mínimo
  divisor) change yearly. Encoded here as ``IvmRules`` keyed by year.

- **ROP (Régimen Obligatorio de Pensiones Complementarias)** is *defined
  contribution*. SUPEN (Acuerdo SP-A-243-2021) mandates a three-scenario
  projection but does NOT set the rates — **each operadora derives its own triad
  from its own historical return series and republishes it every January**. So a
  ROP triad is versioned per ``(opc, year)``; hardcoding a single national triad
  would be wrong. Encoded here as ``RopMethodology`` keyed by ``(opc, year)``.

Like ``app/domain/payroll/rates.py`` this is DATA, not logic: adding a 2027 set
is an edit here, never a change to ``cr_pension.py``. An unconfigured year/OPC
raises — we never silently fall back to a stale set.

⚠️ B6 is operator-⛳-gated: these constants are a *sourced draft* pending the
operator's sign-off against the primary Reglamento IVM + the chosen OPC's
methodology manual. Full provenance + open items: ``docs/phase-10-b6-cr-pension-constants.md``.

Sources (2026):
- IVM: Reglamento del Seguro de IVM arts. 23-29 (reforma sesión 9229, vigente
  2024-01-11). Cuantía básica table 52,5%→43%; adicional 0,0833%/mes sobre 300
  cuotas; postergación 0,1333%/mes sobre el salario de referencia (NO sobre la
  pensión), tope 125%. Mínima ₡162.295 (feb-2026, oficio GP-0607-2026); máxima
  ordinaria ≈₡1.666.062 (art. 28).
- ROP Popular: "Manual de Metodología de Proyecciones Enero 2026" — ROP
  pesimista 4,06% / esperado 8,82% / optimista 13,22% (anual nominal CRC, neta de
  comisiones); densidad por percentiles (P35/P30 pes, P45 opt); inflación 3%;
  comisión saldo 0,35%; aporte ROP 4,25% (1% obrero + 3,25% patrono, Ley 7983).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# The SUPEN-normed disclaimer, VERBATIM from the primary source (constants doc
# §3.2; reproduced byte-identically by Popular + OPC-CCSS). The regulator's own
# "estimation, not promise" — the hope-anchor / D6 guardrail stated by a regulated
# entity. Narration MUST surface it. Keep byte-identical to the doc (the load-
# bearing "a cargo de la operadora" is what names whose promise is disclaimed).
SUPEN_DISCLAIMER_ES = (
    "Las proyecciones tienen, exclusivamente, fines informativos o ilustrativos, "
    "sin que, de ninguna forma, constituya la promesa o compromiso, a cargo de la "
    "operadora, de pagar el monto estimado de pensión resultante."
)


class UnconfiguredYearError(ValueError):
    """Raised when no rate table is configured for the requested year."""


class UnknownOperatorError(ValueError):
    """Raised when no ROP methodology is configured for the requested OPC/year."""


# ── IVM (defined benefit — LAW) ──────────────────────────────────────────────


@dataclass(frozen=True)
class CuantiaBasicaBracket:
    """One bracket of the art. 24 replacement table.

    ``hasta_multiplo`` is the EXCLUSIVE upper bound expressed in "salarios
    mínimos" (SPR / salario mínimo de ocupación no calificada). ``None`` marks the
    open-ended top bracket. Lower earners get the higher rate.
    """

    hasta_multiplo: Optional[Decimal]
    rate: Decimal


@dataclass(frozen=True)
class IvmRules:
    year: int
    status: str  # "vigente" | "propuesta"
    cuantia_basica: tuple[CuantiaBasicaBracket, ...]
    cuantia_adicional_pct_mensual: Decimal  # per month contributed over cuotas_full
    cuotas_full: int
    cuotas_min_proporcional: int
    postergacion_pct_mensual: Decimal       # per month deferred, on the SPR (art. 23)
    postergacion_cap_pct_spr: Decimal       # ordinary+adicional+postergación cap
    edad_retiro: int
    pension_minima: Decimal
    pension_maxima_ordinaria: Decimal
    salario_minimo_referencia: Decimal      # divisor for the bracket table
    salario_minimo_year: int                # which MTSS decree year the divisor is from
    source: str


# ── ROP (defined contribution — operator methodology, versioned) ─────────────


@dataclass(frozen=True)
class RopScenarioParams:
    escenario: str            # "pesimista" | "esperado" | "optimista"
    tasa_nominal: Decimal     # annual, net of fees, CRC
    densidad: Decimal         # contribution density (blended H/M)


@dataclass(frozen=True)
class RopMethodology:
    opc: str
    year: int
    scenarios: tuple[RopScenarioParams, ...]  # pesimista, esperado, optimista
    inflacion: Decimal
    aporte_rop: Decimal                       # total ROP contribution, fraction of salary
    comision_saldo_anual: Decimal             # informational — published rates already net
    edad_retiro: int
    source: str

    def scenario(self, escenario: str) -> RopScenarioParams:
        for s in self.scenarios:
            if s.escenario == escenario:
                return s
        raise KeyError(escenario)


@dataclass(frozen=True)
class DecumulationAssumptions:
    """Retiro-Programado balance→pension conversion — APPROXIMATION.

    ⚠️ The official conversion divides the projected balance by SUPEN's VANU
    (Valor Actuarial Necesario Unitario) table, by sex, which is not public. This
    is a documented stand-in: an annuity factor over CR life expectancy at 65 at a
    conservative post-retirement real return. Swap for the official VANU at the ⛳
    operator-validation gate. Life expectancies are approximate (CR ≈ 18 yr men /
    21 yr women remaining at 65).
    """

    esperanza_vida_meses_hombre: int
    esperanza_vida_meses_mujer: int
    esperanza_vida_meses_blend: int
    tasa_real_post_retiro: Decimal
    source: str


# ── the data ─────────────────────────────────────────────────────────────────
# To add 2027: append entries below. Do NOT touch cr_pension.py.

_CUANTIA_BASICA_2024 = (
    CuantiaBasicaBracket(Decimal("2"), Decimal("0.525")),
    CuantiaBasicaBracket(Decimal("3"), Decimal("0.510")),
    CuantiaBasicaBracket(Decimal("4"), Decimal("0.494")),
    CuantiaBasicaBracket(Decimal("5"), Decimal("0.478")),
    CuantiaBasicaBracket(Decimal("6"), Decimal("0.462")),
    CuantiaBasicaBracket(Decimal("8"), Decimal("0.446")),
    CuantiaBasicaBracket(None, Decimal("0.430")),
)

_IVM_RULES: dict[int, IvmRules] = {
    2026: IvmRules(
        year=2026,
        status="vigente",
        cuantia_basica=_CUANTIA_BASICA_2024,
        cuantia_adicional_pct_mensual=Decimal("0.000833"),  # 0,0833%/mes sobre 300
        cuotas_full=300,
        cuotas_min_proporcional=180,
        postergacion_pct_mensual=Decimal("0.001333"),        # 0,1333%/mes sobre el SPR
        postergacion_cap_pct_spr=Decimal("1.25"),
        edad_retiro=65,
        pension_minima=Decimal("162295"),                    # feb-2026
        pension_maxima_ordinaria=Decimal("1666062"),         # art. 28 — CONFIRM 2026 value
        # ⚠️ divisor de la tabla: valor 2022 verificado; el valor 2026 del salario
        # mínimo de ocupación no calificada (Decreto MTSS) queda pendiente (§7 del
        # doc). Pasá salario_minimo explícito hasta cerrar ese dato.
        salario_minimo_referencia=Decimal("326253.57"),
        salario_minimo_year=2022,
        source="Reglamento IVM arts. 23-29 (reforma 9229, vigente 2024-01-11)",
    ),
}

# Reform proposal — NOT law. Never returned by get_ivm_rules(); a labeled
# "what-if" set so the engine can answer "así se vería si la reforma pasa"
# without presenting it as reality. Presented in JD CCSS sesión 9603 (2026-05-04);
# only a "Mesa Técnica Nacional" was created; est. implementation H1-2028.
IVM_PROPUESTA_2026 = IvmRules(
    year=2026,
    status="propuesta",
    cuantia_basica=(
        # Single flattened range 40%-43% (not a full bracket table yet).
        CuantiaBasicaBracket(Decimal("2"), Decimal("0.430")),
        CuantiaBasicaBracket(None, Decimal("0.400")),
    ),
    cuantia_adicional_pct_mensual=Decimal("0.000833"),
    cuotas_full=360,  # 300 → 360 (25 → 30 años)
    cuotas_min_proporcional=180,
    postergacion_pct_mensual=Decimal("0.001333"),
    postergacion_cap_pct_spr=Decimal("1.25"),
    edad_retiro=65,
    pension_minima=Decimal("162295"),
    pension_maxima_ordinaria=Decimal("1666062"),
    salario_minimo_referencia=Decimal("326253.57"),
    salario_minimo_year=2022,
    source="PROPUESTA — JD CCSS sesión 9603 (2026-05-04). NO vigente.",
)

_ROP_METHODOLOGIES: dict[tuple[str, int], RopMethodology] = {
    ("popular", 2026): RopMethodology(
        opc="Popular Pensiones",
        year=2026,
        scenarios=(
            # Densidades = promedio simple H/M de los percentiles de Popular
            # (pes P35H/P30M, opt P45; esperado intermedio).
            RopScenarioParams("pesimista", Decimal("0.0406"), Decimal("0.478")),
            RopScenarioParams("esperado", Decimal("0.0882"), Decimal("0.618")),
            RopScenarioParams("optimista", Decimal("0.1322"), Decimal("0.716")),
        ),
        inflacion=Decimal("0.03"),          # BCCR meta (bajo revisión 2026)
        aporte_rop=Decimal("0.0425"),       # Ley 7983 (1% obrero + 3,25% patrono)
        comision_saldo_anual=Decimal("0.0035"),  # informativo: las tasas ya son netas
        edad_retiro=65,
        source="Popular Pensiones — Manual de Metodología de Proyecciones Enero 2026",
    ),
}

# Balance→pension conversion stand-in (see DecumulationAssumptions warning).
DECUMULATION_APPROX = DecumulationAssumptions(
    esperanza_vida_meses_hombre=216,   # ≈18 años
    esperanza_vida_meses_mujer=252,    # ≈21 años
    esperanza_vida_meses_blend=234,    # ≈19,5 años
    tasa_real_post_retiro=Decimal("0.02"),
    source="Aproximación (esperanza de vida CR a los 65 + renta a valor presente). "
    "Reemplazar por la tabla VANU oficial de SUPEN en el gate ⛳.",
)

DEFAULT_OPC = "popular"


def get_ivm_rules(year: int) -> IvmRules:
    try:
        return _IVM_RULES[year]
    except KeyError as exc:
        configured = ", ".join(str(y) for y in sorted(_IVM_RULES))
        raise UnconfiguredYearError(
            f"No hay reglas IVM configuradas para el año {year}. "
            f"Años disponibles: {configured}. Agregalas en "
            f"app/domain/retirement/rates.py."
        ) from exc


def get_rop_methodology(opc: str, year: int) -> RopMethodology:
    key = (opc.lower(), year)
    try:
        return _ROP_METHODOLOGIES[key]
    except KeyError as exc:
        configured = ", ".join(f"{o}/{y}" for o, y in sorted(_ROP_METHODOLOGIES))
        raise UnknownOperatorError(
            f"No hay metodología ROP configurada para {opc!r}/{year}. "
            f"Configuradas: {configured}. Cada OPC republica su terna cada enero; "
            f"agregala en app/domain/retirement/rates.py."
        ) from exc


def configured_ivm_years() -> tuple[int, ...]:
    return tuple(sorted(_IVM_RULES))


def configured_rop() -> tuple[tuple[str, int], ...]:
    return tuple(sorted(_ROP_METHODOLOGIES))
