from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.queries.tools.user_context import _describe_insight

from ..database import get_db
from ..dependencies import current_user
from ..models.user import User
from ..schemas.dashboard import DashboardInsight, DashboardInsightsResponse
from ..schemas.insights import validate_insight_content
from ..services.insights.memory_view import group_for, load_active_insights

router = APIRouter(prefix="/api/v1/users/me/insights", tags=["insights"])


@router.get("/summary", response_model=DashboardInsightsResponse)
async def insights_summary(
    limit: int = Query(default=3, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> DashboardInsightsResponse:
    rows = await load_active_insights(
        db, user_id=user.id, now=datetime.now(timezone.utc)
    )
    items: list[DashboardInsight] = []
    for row in rows:
        try:
            content = validate_insight_content(row.content)
        except Exception:  # noqa: BLE001 - defensive read path
            continue
        items.append(
            DashboardInsight(
                id=str(row.id),
                insight_type=row.insight_type,
                group=group_for(row.insight_type),
                description=_describe_insight(content),
                confidence=row.confidence,
                source=row.source,
                valid_until=row.valid_until.isoformat(),
                user_locked=row.user_locked,
                updated_at=row.updated_at.isoformat(),
            )
        )
        if len(items) >= limit:
            break
    return DashboardInsightsResponse(items=items)
