"""Net-salary calculator endpoint (CR payroll, rules layer).

`POST /api/v1/payroll/net-salary` runs the deterministic
``app.domain.payroll.compute_net_salary`` and returns its structured breakdown.
No DB, no LLM — the same pure function the chat tool routes to, so the native
Ingresos calculator and the chat answer can never disagree.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import current_user
from ..models.user import User
from ..schemas.payroll import NetSalaryRequest

from app.domain.payroll import UnconfiguredYearError, compute_net_salary

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])


@router.post("/net-salary")
async def net_salary(
    payload: NetSalaryRequest,
    user: User = Depends(current_user),
) -> dict:
    try:
        result = compute_net_salary(
            gross_monthly=payload.gross_monthly,
            year=payload.year,
            hijos=payload.hijos,
            conyuge=payload.conyuge,
            isr_base_mode=payload.isr_base_mode,
            solidarista_pct=payload.solidarista_pct,
        )
    except UnconfiguredYearError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(result)
