"""Deterministic Costa Rican retirement projection (rules layer).

IVM (CCSS defined benefit) + ROP (defined contribution, three SUPEN scenarios).
Pure: no LLM / network / DB. B6 is ⛳ operator-validated before user-visible —
the constants are a sourced draft (see ``docs/phase-10-b6-cr-pension-constants.md``).
"""
from .cr_pension import (
    ESCENARIOS,
    IvmProjection,
    RetirementBand,
    RetirementProjection,
    RopScenario,
    Sexo,
    Supuestos,
    compute_ivm_pension,
    project_retirement,
    project_rop,
)
from .rates import (
    DECUMULATION_APPROX,
    DEFAULT_OPC,
    IVM_PROPUESTA_2026,
    SUPEN_DISCLAIMER_ES,
    CuantiaBasicaBracket,
    DecumulationAssumptions,
    IvmRules,
    RopMethodology,
    RopScenarioParams,
    UnconfiguredYearError,
    UnknownOperatorError,
    configured_ivm_years,
    configured_rop,
    get_ivm_rules,
    get_rop_methodology,
)

__all__ = [
    # engine
    "ESCENARIOS",
    "IvmProjection",
    "RetirementBand",
    "RetirementProjection",
    "RopScenario",
    "Sexo",
    "Supuestos",
    "compute_ivm_pension",
    "project_retirement",
    "project_rop",
    # constants / config
    "DECUMULATION_APPROX",
    "DEFAULT_OPC",
    "IVM_PROPUESTA_2026",
    "SUPEN_DISCLAIMER_ES",
    "CuantiaBasicaBracket",
    "DecumulationAssumptions",
    "IvmRules",
    "RopMethodology",
    "RopScenarioParams",
    "UnconfiguredYearError",
    "UnknownOperatorError",
    "configured_ivm_years",
    "configured_rop",
    "get_ivm_rules",
    "get_rop_methodology",
]
