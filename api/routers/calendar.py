from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models.user import User
from ..schemas.notifications import UpcomingFeedItem, UpcomingFeedResponse
from ..services import recurrence

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get("/upcoming", response_model=UpcomingFeedResponse)
async def upcoming_feed(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    include_overdue: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    entries = await recurrence.get_upcoming_feed(
        db,
        user.id,
        from_date=from_date,
        to_date=to_date,
        include_overdue=include_overdue,
    )
    items = [
        UpcomingFeedItem(
            item_type=e.item_type,
            id=e.id,
            date=e.date,
            title=e.title,
            amount=e.amount,
            currency=e.currency,
            status=e.status,
            category=e.category,
            provider=e.provider,
            recurring_bill_id=e.recurring_bill_id,
            is_overdue=e.is_overdue,
        )
        for e in entries
    ]
    return UpcomingFeedResponse(
        items=items, from_date=from_date, to_date=to_date
    )
