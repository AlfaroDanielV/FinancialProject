"""Phase 6d B1 — schema + migrations.

Validates the migration 0016 contract:
    - accounts gained `currency` + `initial_balance` (defaults backfill)
    - recurring_incomes table + CR-cycle CHECK
    - magic_link_tokens table + jti UNIQUE + purpose CHECK
    - lazy_detection_events table + CHECKs (hint_type, resolution, score range)

Tests use the `db_with_user` fixture (real Postgres, NullPool engine
per test) and assert on real DB behavior — CHECK constraints and FK
SET NULL semantics. Pure-Python tests would not catch a migration
that drifts from the model.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.data.categories_cr import CATEGORIES_CR
from api.models.account import Account
from api.models.lazy_detection_event import LazyDetectionEvent
from api.models.magic_link_token import MagicLinkToken
from api.models.recurring_income import RecurringIncome


# ── categorías default ────────────────────────────────────────────────────────


def test_categories_cr_short_list_per_resolution_9_5():
    assert len(CATEGORIES_CR) == 10
    # voseo CR + cycle types as first-class (Resolución 9.5)
    assert "ingreso_salario" in CATEGORIES_CR
    assert "ingreso_extra" in CATEGORIES_CR
    assert "alimentación" in CATEGORIES_CR
    assert "deudas" in CATEGORIES_CR


# ── accounts: nuevas columnas ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accounts_new_columns_default_and_round_trip(db_with_user):
    session, user_id = db_with_user
    acc = Account(
        user_id=user_id,
        name="BAC corriente",
        account_type="checking",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)

    # defaults backfill: server default CRC + 0
    assert acc.currency == "CRC"
    assert acc.initial_balance == Decimal("0.00")

    # explicit overrides survive round-trip
    acc2 = Account(
        user_id=user_id,
        name="USD savings",
        account_type="savings",
        currency="USD",
        initial_balance=Decimal("1500.50"),
    )
    session.add(acc2)
    await session.commit()
    await session.refresh(acc2)
    assert acc2.currency == "USD"
    assert acc2.initial_balance == Decimal("1500.50")


# ── recurring_incomes: CR cycles as first-class ──────────────────────────────


@pytest.mark.asyncio
async def test_recurring_income_salary_round_trip(db_with_user):
    session, user_id = db_with_user
    salary = RecurringIncome(
        user_id=user_id,
        name="Salario base",
        income_type="salary",
        amount=Decimal("850000.00"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.commit()
    await session.refresh(salary)
    assert salary.is_active is True
    assert salary.amount == Decimal("850000.00")


@pytest.mark.asyncio
async def test_aguinaldo_without_base_salary_link_rejected(db_with_user):
    session, user_id = db_with_user
    aguinaldo = RecurringIncome(
        user_id=user_id,
        name="Aguinaldo huérfano",
        income_type="aguinaldo",
        amount=None,
        frequency="annual",
        next_payment_date=date(2026, 12, 15),
        base_salary_link_id=None,
    )
    session.add(aguinaldo)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_salario_escolar_with_base_salary_link_succeeds(db_with_user):
    session, user_id = db_with_user
    salary = RecurringIncome(
        user_id=user_id,
        name="Salario base",
        income_type="salary",
        amount=Decimal("850000.00"),
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.commit()
    await session.refresh(salary)

    salario_escolar = RecurringIncome(
        user_id=user_id,
        name="Salario escolar",
        income_type="salario_escolar",
        amount=None,  # derived from base
        frequency="annual",
        next_payment_date=date(2027, 1, 15),
        base_salary_link_id=salary.id,
    )
    session.add(salario_escolar)
    await session.commit()
    await session.refresh(salario_escolar)
    assert salario_escolar.base_salary_link_id == salary.id


@pytest.mark.asyncio
async def test_recurring_income_invalid_type_rejected(db_with_user):
    session, user_id = db_with_user
    bad = RecurringIncome(
        user_id=user_id,
        name="Tipo inventado",
        income_type="dividendos",  # not in enum
        amount=Decimal("100"),
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_recurring_income_invalid_frequency_rejected(db_with_user):
    session, user_id = db_with_user
    bad = RecurringIncome(
        user_id=user_id,
        name="Cada hora",
        income_type="freelance",
        amount=Decimal("50000"),
        frequency="hourly",  # not in enum
        next_payment_date=date.today(),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_base_salary_delete_cascades_to_derived(db_with_user):
    """Aguinaldo/salario_escolar are tied to a specific employment
    relationship in CR. If the base salary is deleted, the derived rows
    go with it — orphaned rows would violate the CHECK constraint anyway.
    """
    session, user_id = db_with_user
    salary = RecurringIncome(
        user_id=user_id,
        name="Salario base",
        income_type="salary",
        amount=Decimal("850000"),
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.commit()
    await session.refresh(salary)

    aguinaldo = RecurringIncome(
        user_id=user_id,
        name="Aguinaldo",
        income_type="aguinaldo",
        frequency="annual",
        next_payment_date=date(2026, 12, 15),
        base_salary_link_id=salary.id,
    )
    session.add(aguinaldo)
    await session.commit()
    await session.refresh(aguinaldo)
    aguinaldo_id = aguinaldo.id

    # Delete base salary via raw SQL so DB-level CASCADE fires (SQLAlchemy's
    # ORM session.delete only emits the parent DELETE; the cascade is the
    # database's responsibility).
    await session.execute(
        text("DELETE FROM recurring_incomes WHERE id = :sid"),
        {"sid": salary.id},
    )
    await session.commit()

    refreshed = await session.execute(
        select(RecurringIncome).where(RecurringIncome.id == aguinaldo_id)
    )
    row = refreshed.scalar_one_or_none()
    assert row is None, "Derived aguinaldo should cascade-delete with its base salary"


# ── magic_link_tokens ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_magic_link_round_trip_and_selector_unique(db_with_user):
    session, user_id = db_with_user
    selector = "selectorfortest1234567890"
    jti = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)

    token = MagicLinkToken(
        user_id=user_id,
        selector=selector,
        token_hash="$2b$12$fakebcrypt",
        jti=jti,
        purpose="onboarding",
        expires_at=expires,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    assert token.used_at is None

    # Re-using the same selector is rejected by the UNIQUE constraint.
    dup = MagicLinkToken(
        user_id=user_id,
        selector=selector,
        token_hash="$2b$12$other",
        jti=uuid.uuid4(),
        purpose="onboarding",
        expires_at=expires,
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_magic_link_invalid_purpose_rejected(db_with_user):
    session, user_id = db_with_user
    bad = MagicLinkToken(
        user_id=user_id,
        selector="anothertestselector1234",
        token_hash="$2b$12$fake",
        jti=uuid.uuid4(),
        purpose="api_token",  # not in enum
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# ── lazy_detection_events ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lazy_detection_event_round_trip(db_with_user):
    session, user_id = db_with_user
    evt = LazyDetectionEvent(
        user_id=user_id,
        hint_type="bank",
        hint_text="BAC",
        fuzzy_match_score=Decimal("0.92"),
        resolution="linked_existing",
    )
    session.add(evt)
    await session.commit()
    await session.refresh(evt)
    assert evt.resolution == "linked_existing"
    assert evt.fuzzy_match_score == Decimal("0.92")
    assert evt.resolved_at is None


@pytest.mark.asyncio
async def test_lazy_detection_score_out_of_range_rejected(db_with_user):
    session, user_id = db_with_user
    bad = LazyDetectionEvent(
        user_id=user_id,
        hint_type="account",
        hint_text="BAC",
        fuzzy_match_score=Decimal("1.50"),  # > 1
        resolution="pending",
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_lazy_detection_invalid_resolution_rejected(db_with_user):
    session, user_id = db_with_user
    bad = LazyDetectionEvent(
        user_id=user_id,
        hint_type="account",
        hint_text="BAC",
        resolution="completed",  # not in enum
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_lazy_detection_score_null_allowed(db_with_user):
    session, user_id = db_with_user
    evt = LazyDetectionEvent(
        user_id=user_id,
        hint_type="account",
        hint_text="BAC",
        fuzzy_match_score=None,
        resolution="pending",
    )
    session.add(evt)
    await session.commit()
    await session.refresh(evt)
    assert evt.fuzzy_match_score is None


# ── migration sanity ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_applied_alembic_head_is_0016(db_with_user):
    session, _user_id = db_with_user
    result = await session.execute(text("SELECT version_num FROM alembic_version"))
    version = result.scalar_one()
    assert version == "0016", (
        f"Expected migration 0016 applied; alembic_version reports {version}. "
        "Run `alembic upgrade head` against the test database."
    )
