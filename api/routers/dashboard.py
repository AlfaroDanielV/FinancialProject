from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.user import User
from ..schemas.dashboard import (
    CashFlowResponse,
    DailyCashFlowResponse,
    DashboardPeriod,
    DashboardSummary,
)
from ..services.dashboard.summary import (
    get_cash_flow,
    get_daily_cash_flow,
    get_dashboard_summary,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    period: DashboardPeriod = Query(default="month_current"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    return await get_dashboard_summary(db, user=user, period=period)


@router.get("/cash-flow", response_model=CashFlowResponse)
async def dashboard_cash_flow(
    from_month: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}$"),
    to_month: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    try:
        return await get_cash_flow(
            db, user=user, from_month=from_month, to_month=to_month
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/daily-cash-flow", response_model=DailyCashFlowResponse)
async def dashboard_daily_cash_flow(
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
        return await get_daily_cash_flow(db, user=user, from_date=start, to_date=end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
