"""Request schema for the CR net-salary calculator endpoint.

The response is the structured breakdown produced by
``app.domain.payroll.compute_net_salary`` (serialized verbatim), so there's no
hand-maintained response model to drift from the rules layer.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class NetSalaryRequest(BaseModel):
    model_config = {"extra": "forbid"}

    gross_monthly: int = Field(..., gt=0, description="Salario bruto mensual (CRC)")
    year: Optional[int] = Field(None, ge=2000, le=2100)
    hijos: int = Field(0, ge=0)
    conyuge: bool = False
    isr_base_mode: Literal["gross", "gross_minus_ccss"] = "gross"
    solidarista_pct: float = Field(0.0, ge=0, le=100)
