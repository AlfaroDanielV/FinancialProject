"""Deterministic CR retirement engine — unit + golden tests (B6).

IVM figures are hand-computed from the Reglamento del Seguro de IVM (reforma
vigente 2024). The ROP projection is float-based (a multi-decade projection is
approximate by nature), so ROP assertions lock the exact, deterministic pieces
(monthly contribution, the pesimista<esperado<optimista ordering, the n=0 edge)
and bound the rest — never a brittle compound-interest float. The engine is pure,
so these are plain (non-async) tests.

⚠️ B6 is ⛳ operator-validated before user-visible; these lock the *draft* formula
+ constants (see docs/phase-10-b6-cr-pension-constants.md).
"""
from __future__ import annotations

import pytest

from app.domain.retirement import (
    ESCENARIOS,
    IVM_PROPUESTA_2026,
    SUPEN_DISCLAIMER_ES,
    UnconfiguredYearError,
    UnknownOperatorError,
    compute_ivm_pension,
    configured_ivm_years,
    project_retirement,
    project_rop,
)

# The art. 24 divisor (salario mínimo ocupación no calificada). Passed explicitly
# so the tests don't depend on the flagged default constant. 2022 value used in
# the CICR worked example.
SALMIN = 326_253.57


# ── IVM: golden worked example (CICR) ───────────────────────────────────────


def test_ivm_cicr_worked_example_full_pension():
    """CICR example: SPR ₡620.000, 400 cuotas → 52,5% + 8,33% = ₡377.146.

    620.000 / 326.253,57 = 1,90 → <2 SM → cuantía básica 52,5% = ₡325.500.
    Cuantía adicional 0,0833%/mes × (400−300) = 8,33% → ₡51.646. Total ₡377.146.
    """
    r = compute_ivm_pension(
        salario_referencia=620_000, cuotas=400, year=2026, salario_minimo=SALMIN
    )
    assert r.elegible is True
    assert r.es_proporcional is False
    assert r.cuantia_basica_pct == pytest.approx(0.525)
    assert r.cuantia_adicional_pct == pytest.approx(0.0833)
    assert r.pension_mensual == 377_146
    assert r.aplico_minimo is False
    assert r.aplico_maximo is False


# ── IVM: bracket table lookup ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "spr,expected_pct",
    [
        (600_000, 0.525),    # 1,84 SM  → <2
        (700_000, 0.510),    # 2,15 SM  → 2–<3
        (1_100_000, 0.494),  # 3,37 SM  → 3–<4
        (1_500_000, 0.478),  # 4,60 SM  → 4–<5
        (1_800_000, 0.462),  # 5,52 SM  → 5–<6
        (2_000_000, 0.446),  # 6,13 SM  → 6–<8
        (3_000_000, 0.430),  # 9,20 SM  → ≥8
    ],
)
def test_ivm_bracket_selection(spr, expected_pct):
    r = compute_ivm_pension(
        salario_referencia=spr, cuotas=300, year=2026, salario_minimo=SALMIN
    )
    assert r.cuantia_basica_pct == pytest.approx(expected_pct)


def test_ivm_bracket_boundary_is_exclusive_upper():
    # Just at/above 2 SM lands in the "2 a <3" bracket (51%), not the <2 (52.5%).
    just_over_2sm = 653_000  # 2,0015 SM
    r = compute_ivm_pension(
        salario_referencia=just_over_2sm, cuotas=300, year=2026, salario_minimo=SALMIN
    )
    assert r.cuantia_basica_pct == pytest.approx(0.510)


# ── IVM: proporcional (180–299 cuotas) ──────────────────────────────────────


def test_ivm_proporcional_240_cuotas():
    # 240/300 of the basic pension; no adicional, no postergación.
    r = compute_ivm_pension(
        salario_referencia=620_000, cuotas=240, year=2026, salario_minimo=SALMIN
    )
    assert r.es_proporcional is True
    assert r.cuantia_adicional_pct == 0.0
    # 620.000 × 52,5% × (240/300) = ₡260.400
    assert r.pension_mensual == 260_400


def test_ivm_below_min_cuotas_not_eligible():
    r = compute_ivm_pension(
        salario_referencia=620_000, cuotas=179, year=2026, salario_minimo=SALMIN
    )
    assert r.elegible is False
    assert r.pension_mensual == 0


def test_ivm_proporcional_not_floored_to_full_minimum():
    # A deliberately-reduced 180-cuota benefit must NOT be lifted to the full
    # ordinary minimum (art. 29 — ⛳-pending; the doc doesn't authorize it).
    # 300.000 × 52,5% × 180/300 = ₡94.500, below the ₡162.295 ordinary minimum.
    r = compute_ivm_pension(
        salario_referencia=300_000, cuotas=180, year=2026, salario_minimo=SALMIN
    )
    assert r.es_proporcional is True
    assert r.aplico_minimo is False
    assert r.pension_mensual == 94_500


# ── IVM: money floor + ceiling ──────────────────────────────────────────────


def test_ivm_minimum_floor_applies():
    # 300.000 × 52,5% = ₡157.500 < mínima ₡162.295 → floored.
    r = compute_ivm_pension(
        salario_referencia=300_000, cuotas=300, year=2026, salario_minimo=SALMIN
    )
    assert r.aplico_minimo is True
    assert r.pension_mensual == 162_295


def test_ivm_maximum_ceiling_applies_without_postergacion():
    # High SPR, long career → capped at the ordinary max ₡1.666.062.
    r = compute_ivm_pension(
        salario_referencia=4_000_000, cuotas=480, year=2026, salario_minimo=SALMIN
    )
    assert r.aplico_maximo is True
    assert r.pension_mensual == 1_666_062


def test_ivm_deferred_proporcional_still_capped_at_ordinary_max():
    # Regression: a high-earner proporcional pension that ALSO passes
    # meses_postergacion must still hit the ordinary max (postergación isn't
    # granted on a proporcional, so the max is not bypassed).
    # SPR 10M / 240 cuotas → 43% × 0,8 = ₡3.440.000 → capped to ₡1.666.062.
    r = compute_ivm_pension(
        salario_referencia=10_000_000,
        cuotas=240,
        year=2026,
        salario_minimo=SALMIN,
        meses_postergacion=24,  # ignored (proporcional) — must NOT lift the cap
    )
    assert r.es_proporcional is True
    assert r.postergacion_pct == 0.0
    assert r.aplico_maximo is True
    assert r.pension_mensual == 1_666_062


# ── IVM: postergación (on the SPR, not the pension) ─────────────────────────


def test_ivm_postergacion_adds_and_skips_ordinary_max():
    base = compute_ivm_pension(
        salario_referencia=1_000_000, cuotas=300, year=2026, salario_minimo=SALMIN
    )
    deferred = compute_ivm_pension(
        salario_referencia=1_000_000,
        cuotas=300,
        year=2026,
        salario_minimo=SALMIN,
        meses_postergacion=24,
    )
    # 3,07 SM → 49,4%. Postergación 0,1333%/mes × 24 = 3,1992% of the SPR.
    assert deferred.postergacion_pct == pytest.approx(0.031992)
    # base 1.000.000×49,4% = ₡494.000; deferred + 1.000.000×3,1992% = ₡525.992
    assert base.pension_mensual == 494_000
    assert deferred.pension_mensual == 525_992
    assert deferred.aplico_maximo is False  # ordinary max skipped with postergación


def test_ivm_postergacion_capped_at_125pct_of_spr():
    # Absurd deferral would exceed 125% SPR → pct capped there.
    r = compute_ivm_pension(
        salario_referencia=1_000_000,
        cuotas=600,  # basic 49,4% + adicional 0,0833%×300 = 24,99%
        year=2026,
        salario_minimo=SALMIN,
        meses_postergacion=600,  # +79,98% → total would be >150%
    )
    # Capped at 125% → ₡1.250.000 (no ordinary max because postergación > 0).
    assert r.pension_mensual == 1_250_000
    assert r.aplico_cap is True
    # pension_antes_topes reflects the UNcapped pct (0,494+0,2499+0,7998=154,37%),
    # so the exposed component pcts reconcile with it; aplico_cap explains the gap.
    assert r.pension_antes_topes == 1_543_700


# ── IVM: the reform proposal as a labeled "what-if" (NOT law) ────────────────


def test_ivm_propuesta_2026_is_harsher_and_not_default():
    """Injecting IVM_PROPUESTA_2026 (40-43%, 360 cuotas) yields a lower pension.

    Same person, 300 cuotas: under current law that's a FULL pension; under the
    proposal 300 < 360 → proporcional (reduced). The proposal is never returned by
    get_ivm_rules — it must be injected explicitly.
    """
    vigente = compute_ivm_pension(
        salario_referencia=800_000, cuotas=300, year=2026, salario_minimo=SALMIN
    )
    propuesta = compute_ivm_pension(
        salario_referencia=800_000,
        cuotas=300,
        salario_minimo=SALMIN,
        rules=IVM_PROPUESTA_2026,
    )
    assert vigente.es_proporcional is False
    assert propuesta.es_proporcional is True  # 300 < 360
    assert propuesta.pension_mensual < vigente.pension_mensual
    assert IVM_PROPUESTA_2026.status == "propuesta"


# ── ROP: exact contribution + scenario ordering ─────────────────────────────


def test_rop_monthly_contribution_is_exact():
    # aporte = salario × 4,25% × densidad (blended). Deterministic → exact.
    scenarios = project_rop(
        saldo_actual=0, salario_mensual=1_000_000, edad_actual=30, year=2026
    )
    by_name = {s.escenario: s for s in scenarios}
    assert by_name["pesimista"].aporte_mensual == 20_315   # 42.500 × 0,478
    assert by_name["esperado"].aporte_mensual == 26_265    # 42.500 × 0,618
    assert by_name["optimista"].aporte_mensual == 30_430   # 42.500 × 0,716


def test_rop_scenarios_are_ordered_and_positive():
    scenarios = project_rop(
        saldo_actual=5_000_000, salario_mensual=800_000, edad_actual=35, year=2026
    )
    assert tuple(s.escenario for s in scenarios) == ESCENARIOS
    pens = [s.pension_mensual for s in scenarios]
    saldos = [s.saldo_proyectado_vp for s in scenarios]
    assert pens[0] < pens[1] < pens[2]      # pesimista < esperado < optimista
    assert saldos[0] < saldos[1] < saldos[2]
    assert all(p > 0 for p in pens)
    # 30 years of contributions grows the balance well past the starting saldo.
    assert saldos[1] > 5_000_000


def test_rop_real_rate_below_nominal():
    scenarios = project_rop(
        saldo_actual=0, salario_mensual=1_000_000, edad_actual=40, year=2026
    )
    for s in scenarios:
        assert s.tasa_real < s.tasa_nominal  # discounted by 3% inflation


def test_rop_at_retirement_age_no_growth_bands_collapse():
    # edad_actual == edad_retiro → no accumulation; return rate is irrelevant, so
    # all three bands give the same pension (balance ÷ same annuity factor).
    scenarios = project_rop(
        saldo_actual=20_000_000, salario_mensual=1_000_000, edad_actual=65, year=2026
    )
    assert all(s.saldo_proyectado_vp == 20_000_000 for s in scenarios)
    pens = {s.pension_mensual for s in scenarios}
    assert len(pens) == 1  # collapsed


def test_rop_sex_changes_annuity():
    # A shorter life expectancy (M) spreads the balance over fewer months → higher
    # monthly pension than the blended default; F (longer) → lower.
    male = project_rop(
        saldo_actual=20_000_000, salario_mensual=1_000_000, edad_actual=65,
        year=2026, sexo="M",
    )[0]
    female = project_rop(
        saldo_actual=20_000_000, salario_mensual=1_000_000, edad_actual=65,
        year=2026, sexo="F",
    )[0]
    assert male.pension_mensual > female.pension_mensual


# ── full projection: IVM + ROP → three total bands ──────────────────────────


def test_project_retirement_composes_three_bands():
    proj = project_retirement(
        salario_referencia=800_000,
        cuotas=420,
        saldo_rop=8_000_000,
        salario_mensual=800_000,
        edad_actual=40,
        year=2026,
        salario_minimo=SALMIN,
    )
    assert tuple(b.escenario for b in proj.bandas) == ESCENARIOS
    # IVM is a defined benefit — identical across the three bands.
    assert len({b.pension_ivm for b in proj.bandas}) == 1
    # Each total = its IVM + its ROP; totals strictly increasing.
    for b in proj.bandas:
        assert b.pension_total == b.pension_ivm + b.pension_rop
    totals = [b.pension_total for b in proj.bandas]
    assert totals[0] < totals[1] < totals[2]
    # Replacement ratio tracks the totals.
    reempl = [b.tasa_reemplazo for b in proj.bandas]
    assert reempl[0] < reempl[1] < reempl[2]
    assert all(r > 0 for r in reempl)


def test_project_retirement_carries_disclaimer_and_assumptions():
    proj = project_retirement(
        salario_referencia=800_000,
        cuotas=360,
        saldo_rop=5_000_000,
        salario_mensual=800_000,
        edad_actual=45,
        year=2026,
        salario_minimo=SALMIN,
    )
    assert proj.disclaimer == SUPEN_DISCLAIMER_ES
    # The load-bearing normed clause must be present (verbatim guardrail).
    assert "a cargo de la operadora" in proj.disclaimer
    assert proj.opc == "Popular Pensiones"
    # supuestos is a frozen dataclass, not a mutable dict (payroll parity).
    assert proj.supuestos.inflacion == 0.03
    assert proj.supuestos.aporte_rop == 0.0425
    assert proj.meses_hasta_retiro == (65 - 45) * 12


# ── error handling ──────────────────────────────────────────────────────────


def test_unconfigured_ivm_year_raises():
    with pytest.raises(UnconfiguredYearError):
        compute_ivm_pension(salario_referencia=800_000, cuotas=300, year=2099)


def test_unknown_opc_raises():
    with pytest.raises(UnknownOperatorError):
        project_rop(
            saldo_actual=0, salario_mensual=800_000, edad_actual=40,
            opc="banco-fantasma", year=2026,
        )


def test_unconfigured_rop_year_raises():
    with pytest.raises(UnknownOperatorError):
        project_rop(
            saldo_actual=0, salario_mensual=800_000, edad_actual=40, year=2099
        )


@pytest.mark.parametrize("bad", [0, -1, -100_000])
def test_ivm_non_positive_spr_raises(bad):
    with pytest.raises(ValueError):
        compute_ivm_pension(salario_referencia=bad, cuotas=300, year=2026)


@pytest.mark.parametrize("bad", [0, -1])
def test_ivm_non_positive_salario_minimo_raises(bad):
    # A bad divisor must fail loudly, not silently return the 52,5% bracket.
    with pytest.raises(ValueError):
        compute_ivm_pension(
            salario_referencia=3_000_000, cuotas=300, year=2026, salario_minimo=bad
        )


def test_ivm_surfaces_divisor_vintage():
    # Default divisor → its 2022 vintage is visible for auditability.
    default = compute_ivm_pension(salario_referencia=800_000, cuotas=300, year=2026)
    assert default.salario_minimo_year == 2022
    assert default.salario_minimo_usado == 326_254  # round(₡326.253,57)
    # Caller-supplied divisor → vintage unknown (None), value echoed.
    supplied = compute_ivm_pension(
        salario_referencia=800_000, cuotas=300, year=2026, salario_minimo=370_000
    )
    assert supplied.salario_minimo_year is None
    assert supplied.salario_minimo_usado == 370_000


def test_rop_negative_saldo_raises():
    with pytest.raises(ValueError):
        project_rop(
            saldo_actual=-1, salario_mensual=800_000, edad_actual=40, year=2026
        )


def test_rop_past_retirement_age_raises():
    # Already past 65 → caller error, not a silent "project as if retiring today".
    with pytest.raises(ValueError):
        project_rop(
            saldo_actual=5_000_000, salario_mensual=800_000, edad_actual=70, year=2026
        )


def test_2026_is_configured():
    assert 2026 in configured_ivm_years()
