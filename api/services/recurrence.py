"""Phase 4 recurrence / notification engine.

Responsibilities:
    * Materialize future `bill_occurrences` from a `recurring_bills` template.
    * Flip past-due `pending` occurrences to `overdue`.
    * Generate `notification_events` according to the matching rule hierarchy
      (bill-specific > event-specific > category default > global default).
    * Build the unified upcoming feed (bills + events).
    * Link a transaction to a bill occurrence (mark-paid).

All public helpers are idempotent: running them twice must not create
duplicates. The DB enforces `UNIQUE(recurring_bill_id, due_date)` for
occurrences and we de-dup notifications in code.

Timezone: every "today" is computed in America/Costa_Rica. Occurrence dates
are stored as `DATE` (no TZ).
"""
from __future__ import annotations

import calendar
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.bill_occurrence import BillOccurrence
from ..models.custom_event import CustomEvent
from ..models.enums import (
    BillFrequency,
    BillOccurrenceStatus,
    NotificationChannel,
    NotificationScope,
    NotificationStatus,
)
from ..models.notification_event import NotificationEvent
from ..models.notification_rule import NotificationRule
from ..models.recurring_bill import RecurringBill
from ..models.transaction import Transaction

logger = logging.getLogger(__name__)

CR_TZ = ZoneInfo("America/Costa_Rica")

DEFAULT_HORIZON_MONTHS = 6
VARIANCE_WARN_PCT = 0.20


def today_cr() -> date:
    return datetime.now(CR_TZ).date()


# ─── date math ────────────────────────────────────────────────────────────────


def _clamp_day(year: int, month: int, day: int) -> date:
    """Return date(year, month, min(day, last_day_of_month))."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _add_months(d: date, months: int, preferred_day: int | None = None) -> date:
    """Advance `d` by `months`, clamping to month-end when needed.

    If preferred_day is provided, it's used instead of d.day. This matters for
    templates with day_of_month=31 in February.
    """
    target = d + relativedelta(months=months)
    day = preferred_day if preferred_day is not None else d.day
    return _clamp_day(target.year, target.month, day)


def compute_next_dates(
    bill: RecurringBill,
    *,
    anchor: date,
    until: date,
) -> list[date]:
    """Return every due date strictly after `anchor` and up to/including `until`.

    `anchor` is either the last existing occurrence's due_date or
    bill.start_date - one period. Callers handle that shift.
    """
    freq = BillFrequency(bill.frequency)
    end = bill.end_date if bill.end_date else until
    horizon = min(until, end)
    dates: list[date] = []

    if freq == BillFrequency.CUSTOM:
        if not bill.recurrence_rule:
            return []
        # dtstart must be provided; rrulestr accepts RRULE fragments if dtstart
        # is passed explicitly. We use bill.start_date as dtstart.
        start_dt = datetime.combine(bill.start_date, datetime.min.time())
        rule = rrulestr(bill.recurrence_rule, dtstart=start_dt)
        for dt in rule:
            d = dt.date() if isinstance(dt, datetime) else dt
            if d <= anchor:
                continue
            if d > horizon:
                break
            dates.append(d)
        return dates

    # Compute step
    if freq == BillFrequency.WEEKLY:
        step = timedelta(days=7)
        current = anchor + step if anchor >= bill.start_date else bill.start_date
        while current <= horizon:
            if current > anchor:
                dates.append(current)
            current += step
        return dates

    if freq == BillFrequency.BIWEEKLY:
        step = timedelta(days=14)
        current = anchor + step if anchor >= bill.start_date else bill.start_date
        while current <= horizon:
            if current > anchor:
                dates.append(current)
            current += step
        return dates

    months_by_freq = {
        BillFrequency.MONTHLY: 1,
        BillFrequency.BIMONTHLY: 2,
        BillFrequency.QUARTERLY: 3,
        BillFrequency.SEMIANNUAL: 6,
        BillFrequency.ANNUAL: 12,
    }
    step_months = months_by_freq[freq]
    preferred_day = bill.day_of_month if bill.day_of_month else bill.start_date.day

    # Walk from bill.start_date, emitting occurrences strictly after anchor
    # and within horizon. This is stateless / idempotent.
    cursor = bill.start_date
    # If start_date day != preferred_day, re-anchor to preferred_day in the
    # same month (clamped). This is the first due date.
    cursor = _clamp_day(cursor.year, cursor.month, preferred_day)

    # Safety cap so a misconfigured template can't loop forever.
    max_iterations = 2000
    i = 0
    while cursor <= horizon and i < max_iterations:
        if cursor > anchor and cursor >= bill.start_date:
            dates.append(cursor)
        cursor = _add_months(cursor, step_months, preferred_day)
        i += 1
    return dates


# ─── core operations ──────────────────────────────────────────────────────────


async def generate_occurrences(
    bill: RecurringBill,
    session: AsyncSession,
    *,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
    today: date | None = None,
) -> int:
    """Materialize upcoming occurrences for a bill. Returns number created."""
    if not bill.is_active:
        return 0

    today = today or today_cr()
    horizon = today + relativedelta(months=horizon_months)

    # Latest existing occurrence for this bill
    last_row = await session.execute(
        select(BillOccurrence.due_date)
        .where(BillOccurrence.recurring_bill_id == bill.id)
        .order_by(BillOccurrence.due_date.desc())
        .limit(1)
    )
    last_due = last_row.scalar_one_or_none()
    # Anchor is "the date just before the first one we need". If there's no
    # history, anchor = start_date - 1 day so start_date itself can be emitted.
    anchor = last_due if last_due else (bill.start_date - timedelta(days=1))

    dates = compute_next_dates(bill, anchor=anchor, until=horizon)
    if not dates:
        return 0

    rows = [
        {
            "id": uuid.uuid4(),
            "recurring_bill_id": bill.id,
            "due_date": d,
            "amount_expected": bill.amount_expected,
            "status": BillOccurrenceStatus.PENDING.value,
        }
        for d in dates
    ]
    stmt = (
        pg_insert(BillOccurrence.__table__)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["recurring_bill_id", "due_date"])
        .returning(BillOccurrence.id)
    )
    result = await session.execute(stmt)
    created = len(result.fetchall())
    logger.info(
        "generate_occurrences: bill=%s created=%d (candidates=%d, horizon=%s)",
        bill.id,
        created,
        len(dates),
        horizon,
    )
    return created


async def generate_occurrences_all(
    session: AsyncSession,
    *,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
) -> int:
    """Run generate_occurrences for every active bill. Returns total created."""
    result = await session.execute(
        select(RecurringBill).where(RecurringBill.is_active.is_(True))
    )
    bills = list(result.scalars().all())
    total = 0
    for bill in bills:
        total += await generate_occurrences(
            bill, session, horizon_months=horizon_months
        )
    logger.info(
        "generate_occurrences_all: bills=%d total_created=%d", len(bills), total
    )
    return total


async def cancel_future_pending(bill_id: uuid.UUID, session: AsyncSession) -> int:
    """Mark every future PENDING occurrence of a bill as CANCELLED.

    Used when a recurring_bill is soft-deleted. Past or paid occurrences are
    untouched.
    """
    today = today_cr()
    result = await session.execute(
        select(BillOccurrence).where(
            BillOccurrence.recurring_bill_id == bill_id,
            BillOccurrence.status == BillOccurrenceStatus.PENDING.value,
            BillOccurrence.due_date >= today,
        )
    )
    occurrences = list(result.scalars().all())
    for occ in occurrences:
        occ.status = BillOccurrenceStatus.CANCELLED.value
    return len(occurrences)


async def delete_future_pending(
    bill_id: uuid.UUID, session: AsyncSession
) -> int:
    """Physically delete future PENDING occurrences of a bill.

    Used when the bill's schedule (frequency / day_of_month / start / end /
    recurrence_rule) changes and we need to regenerate. Past or paid
    occurrences are left alone.
    """
    today = today_cr()
    result = await session.execute(
        select(BillOccurrence).where(
            BillOccurrence.recurring_bill_id == bill_id,
            BillOccurrence.status == BillOccurrenceStatus.PENDING.value,
            BillOccurrence.due_date >= today,
        )
    )
    occurrences = list(result.scalars().all())
    for occ in occurrences:
        await session.delete(occ)
    return len(occurrences)


async def mark_overdue(session: AsyncSession) -> int:
    """Flip PENDING occurrences with due_date < today to OVERDUE. Returns count."""
    today = today_cr()
    result = await session.execute(
        select(BillOccurrence).where(
            BillOccurrence.status == BillOccurrenceStatus.PENDING.value,
            BillOccurrence.due_date < today,
        )
    )
    occurrences = list(result.scalars().all())
    for occ in occurrences:
        occ.status = BillOccurrenceStatus.OVERDUE.value
    logger.info("mark_overdue: flipped=%d (today=%s)", len(occurrences), today)
    return len(occurrences)


# ─── notification rule resolution ─────────────────────────────────────────────


@dataclass
class _ResolvedRule:
    advance_days: list[int]
    source_scope: NotificationScope


async def _resolve_rule_for_bill(
    bill: RecurringBill, session: AsyncSession
) -> _ResolvedRule | None:
    """Specific rule > category default > global default."""
    # bill-specific
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.scope == NotificationScope.BILL.value,
            NotificationRule.recurring_bill_id == bill.id,
            NotificationRule.is_active.is_(True),
        )
    )
    rule = result.scalar_one_or_none()
    if rule:
        return _ResolvedRule(rule.advance_days, NotificationScope.BILL)

    # category default
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.scope == NotificationScope.CATEGORY_DEFAULT.value,
            NotificationRule.category == bill.category,
            NotificationRule.is_active.is_(True),
        )
    )
    rule = result.scalar_one_or_none()
    if rule:
        return _ResolvedRule(rule.advance_days, NotificationScope.CATEGORY_DEFAULT)

    return await _resolve_global_default(session)


async def _resolve_rule_for_event(
    event: CustomEvent, session: AsyncSession
) -> _ResolvedRule | None:
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.scope == NotificationScope.EVENT.value,
            NotificationRule.custom_event_id == event.id,
            NotificationRule.is_active.is_(True),
        )
    )
    rule = result.scalar_one_or_none()
    if rule:
        return _ResolvedRule(rule.advance_days, NotificationScope.EVENT)

    return await _resolve_global_default(session)


async def _resolve_global_default(
    session: AsyncSession,
) -> _ResolvedRule | None:
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.scope == NotificationScope.GLOBAL_DEFAULT.value,
            NotificationRule.is_active.is_(True),
        )
    )
    rule = result.scalar_one_or_none()
    if rule:
        return _ResolvedRule(rule.advance_days, NotificationScope.GLOBAL_DEFAULT)
    return None


# ─── notification generation ──────────────────────────────────────────────────


def _bill_snapshot(bill: RecurringBill, occ: BillOccurrence) -> dict:
    return {
        "kind": "bill",
        "bill_id": str(bill.id),
        "bill_name": bill.name,
        "bill_provider": bill.provider,
        "bill_category": bill.category,
        "amount_expected": float(occ.amount_expected) if occ.amount_expected else None,
        "currency": bill.currency,
        "due_date": occ.due_date.isoformat(),
    }


def _event_snapshot(event: CustomEvent) -> dict:
    return {
        "kind": "event",
        "event_id": str(event.id),
        "title": event.title,
        "event_type": event.event_type,
        "event_date": event.event_date.isoformat(),
        "amount": float(event.amount) if event.amount else None,
        "currency": event.currency,
    }


async def _existing_notification_keys(
    session: AsyncSession,
    *,
    bill_occurrence_ids: list[uuid.UUID],
    custom_event_ids: list[uuid.UUID],
) -> set[tuple[str, uuid.UUID, int]]:
    """Return a set of (kind, target_id, advance_days) for notifications that
    already exist. Used for idempotency.
    """
    keys: set[tuple[str, uuid.UUID, int]] = set()
    if bill_occurrence_ids:
        result = await session.execute(
            select(
                NotificationEvent.bill_occurrence_id,
                NotificationEvent.advance_days,
            ).where(NotificationEvent.bill_occurrence_id.in_(bill_occurrence_ids))
        )
        for occ_id, adv in result.all():
            keys.add(("bill", occ_id, adv))
    if custom_event_ids:
        result = await session.execute(
            select(
                NotificationEvent.custom_event_id,
                NotificationEvent.advance_days,
            ).where(NotificationEvent.custom_event_id.in_(custom_event_ids))
        )
        for ev_id, adv in result.all():
            keys.add(("event", ev_id, adv))
    return keys


async def compute_pending_notifications(session: AsyncSession) -> int:
    """Create notification_events for every pending/overdue occurrence and
    every active custom_event, using the resolved rule. Idempotent.

    Returns the number of new notification rows created.
    """
    # Candidate bill occurrences
    occ_result = await session.execute(
        select(BillOccurrence)
        .options(selectinload(BillOccurrence.recurring_bill))
        .where(
            BillOccurrence.status.in_(
                [
                    BillOccurrenceStatus.PENDING.value,
                    BillOccurrenceStatus.OVERDUE.value,
                ]
            )
        )
    )
    occurrences = list(occ_result.scalars().all())

    ev_result = await session.execute(
        select(CustomEvent).where(CustomEvent.is_active.is_(True))
    )
    events = list(ev_result.scalars().all())

    existing = await _existing_notification_keys(
        session,
        bill_occurrence_ids=[o.id for o in occurrences],
        custom_event_ids=[e.id for e in events],
    )

    created_rows: list[NotificationEvent] = []

    for occ in occurrences:
        bill = occ.recurring_bill
        rule = await _resolve_rule_for_bill(bill, session)
        if rule is None:
            continue
        snapshot = _bill_snapshot(bill, occ)
        for adv in rule.advance_days:
            key = ("bill", occ.id, int(adv))
            if key in existing:
                continue
            created_rows.append(
                NotificationEvent(
                    bill_occurrence_id=occ.id,
                    trigger_date=occ.due_date - timedelta(days=int(adv)),
                    advance_days=int(adv),
                    channel=NotificationChannel.IN_APP.value,
                    status=NotificationStatus.PENDING.value,
                    payload_snapshot=snapshot,
                )
            )
            existing.add(key)

    for event in events:
        rule = await _resolve_rule_for_event(event, session)
        if rule is None:
            continue
        snapshot = _event_snapshot(event)
        for adv in rule.advance_days:
            key = ("event", event.id, int(adv))
            if key in existing:
                continue
            created_rows.append(
                NotificationEvent(
                    custom_event_id=event.id,
                    trigger_date=event.event_date - timedelta(days=int(adv)),
                    advance_days=int(adv),
                    channel=NotificationChannel.IN_APP.value,
                    status=NotificationStatus.PENDING.value,
                    payload_snapshot=snapshot,
                )
            )
            existing.add(key)

    for row in created_rows:
        session.add(row)

    logger.info(
        "compute_pending_notifications: bills=%d events=%d created=%d",
        len(occurrences),
        len(events),
        len(created_rows),
    )
    return len(created_rows)


# ─── feed ─────────────────────────────────────────────────────────────────────


@dataclass
class FeedEntry:
    item_type: str  # "bill" | "event"
    id: uuid.UUID
    date: date
    title: str
    amount: Optional[float]
    currency: str
    status: Optional[str]
    category: Optional[str]
    provider: Optional[str]
    recurring_bill_id: Optional[uuid.UUID]
    is_overdue: bool


async def get_upcoming_feed(
    session: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    include_overdue: bool = True,
) -> list[FeedEntry]:
    """Combine bill_occurrences (joined with their bill) and custom_events,
    sorted by date ascending. Overdue occurrences are returned first if
    include_overdue=True.
    """
    today = today_cr()

    occ_q = (
        select(BillOccurrence)
        .options(selectinload(BillOccurrence.recurring_bill))
        .where(
            and_(
                BillOccurrence.due_date >= from_date,
                BillOccurrence.due_date <= to_date,
                BillOccurrence.status.in_(
                    [
                        BillOccurrenceStatus.PENDING.value,
                        BillOccurrenceStatus.OVERDUE.value,
                        BillOccurrenceStatus.PARTIALLY_PAID.value,
                    ]
                ),
            )
        )
    )

    occ_overdue_q = None
    if include_overdue:
        occ_overdue_q = (
            select(BillOccurrence)
            .options(selectinload(BillOccurrence.recurring_bill))
            .where(
                and_(
                    BillOccurrence.due_date < from_date,
                    BillOccurrence.status == BillOccurrenceStatus.OVERDUE.value,
                )
            )
        )

    occ_result = await session.execute(occ_q)
    occurrences = list(occ_result.scalars().all())

    if occ_overdue_q is not None:
        overdue_result = await session.execute(occ_overdue_q)
        occurrences.extend(overdue_result.scalars().all())

    ev_result = await session.execute(
        select(CustomEvent).where(
            CustomEvent.is_active.is_(True),
            CustomEvent.event_date >= from_date,
            CustomEvent.event_date <= to_date,
        )
    )
    events = list(ev_result.scalars().all())

    entries: list[FeedEntry] = []
    for occ in occurrences:
        bill = occ.recurring_bill
        entries.append(
            FeedEntry(
                item_type="bill",
                id=occ.id,
                date=occ.due_date,
                title=bill.name,
                amount=float(occ.amount_expected) if occ.amount_expected else None,
                currency=bill.currency,
                status=occ.status,
                category=bill.category,
                provider=bill.provider,
                recurring_bill_id=bill.id,
                is_overdue=(
                    occ.status == BillOccurrenceStatus.OVERDUE.value
                    or (
                        occ.status == BillOccurrenceStatus.PENDING.value
                        and occ.due_date < today
                    )
                ),
            )
        )
    for event in events:
        entries.append(
            FeedEntry(
                item_type="event",
                id=event.id,
                date=event.event_date,
                title=event.title,
                amount=float(event.amount) if event.amount else None,
                currency=event.currency,
                status=None,
                category=None,
                provider=None,
                recurring_bill_id=None,
                is_overdue=event.event_date < today,
            )
        )

    entries.sort(key=lambda e: (not e.is_overdue, e.date, e.title))
    return entries


# ─── link transaction to occurrence ───────────────────────────────────────────


@dataclass
class MarkPaidResult:
    occurrence: BillOccurrence
    amount_delta_pct: Optional[float]
    warning: Optional[str]


async def link_transaction_to_occurrence(
    occurrence_id: uuid.UUID,
    transaction_id: Optional[uuid.UUID],
    session: AsyncSession,
    *,
    amount_paid: Optional[float] = None,
    paid_at: Optional[datetime] = None,
    notes: Optional[str] = None,
) -> MarkPaidResult:
    """Mark a bill_occurrence paid. If transaction_id is provided, validate it
    exists and use its amount/occurred_at as defaults.

    Sets status = PAID if amount_paid >= amount_expected, else PARTIALLY_PAID.
    Warns (non-blocking) when the paid amount diverges >20% from expected.
    """
    occ_result = await session.execute(
        select(BillOccurrence).where(BillOccurrence.id == occurrence_id)
    )
    occ = occ_result.scalar_one_or_none()
    if occ is None:
        raise ValueError("Bill occurrence not found")

    if occ.status in (
        BillOccurrenceStatus.PAID.value,
        BillOccurrenceStatus.CANCELLED.value,
    ):
        raise ValueError(
            f"Occurrence already in terminal state: {occ.status}"
        )

    resolved_amount = amount_paid
    resolved_paid_at = paid_at

    if transaction_id is not None:
        txn_result = await session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        txn = txn_result.scalar_one_or_none()
        if txn is None:
            raise ValueError("Transaction not found")
        if resolved_amount is None:
            resolved_amount = abs(float(txn.amount))
        if resolved_paid_at is None:
            resolved_paid_at = datetime.combine(
                txn.transaction_date, datetime.min.time(), tzinfo=CR_TZ
            )

    if resolved_paid_at is None:
        resolved_paid_at = datetime.now(CR_TZ)

    expected = float(occ.amount_expected) if occ.amount_expected else None
    warning: Optional[str] = None
    delta_pct: Optional[float] = None
    if expected and resolved_amount is not None and expected > 0:
        delta_pct = abs(resolved_amount - expected) / expected
        if delta_pct > VARIANCE_WARN_PCT:
            warning = (
                f"El monto pagado ({resolved_amount:.2f}) difiere "
                f"{delta_pct * 100:.1f}% del esperado ({expected:.2f})."
            )

    if resolved_amount is not None:
        occ.amount_paid = Decimal(str(resolved_amount))
    occ.paid_at = resolved_paid_at
    occ.transaction_id = transaction_id
    if notes is not None:
        occ.notes = notes

    if (
        expected is not None
        and resolved_amount is not None
        and resolved_amount + 0.005 < expected
    ):
        occ.status = BillOccurrenceStatus.PARTIALLY_PAID.value
    else:
        occ.status = BillOccurrenceStatus.PAID.value

    return MarkPaidResult(
        occurrence=occ, amount_delta_pct=delta_pct, warning=warning
    )


# ─── convenience used by routers ──────────────────────────────────────────────


async def fetch_bill(
    bill_id: uuid.UUID, session: AsyncSession
) -> RecurringBill | None:
    result = await session.execute(
        select(RecurringBill).where(RecurringBill.id == bill_id)
    )
    return result.scalar_one_or_none()


async def fetch_occurrence(
    occurrence_id: uuid.UUID, session: AsyncSession
) -> BillOccurrence | None:
    result = await session.execute(
        select(BillOccurrence).where(BillOccurrence.id == occurrence_id)
    )
    return result.scalar_one_or_none()


def iter_as_dicts(entries: Iterable[FeedEntry]) -> list[dict]:
    return [e.__dict__ for e in entries]
