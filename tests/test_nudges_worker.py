"""Nudge fan-out worker + in-process scheduler safe-runner.

`workers/nudges_daily.py` drives the Phase 5d evaluate→deliver pipeline across
active users so proactive messaging fires without an external cron. Per-user
errors are swallowed (one bad user can't abort the run) and the scheduler's
tick is wrapped so the loop survives a failure ("async tasks fail silently
without try/except" is a hard rule).

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.config import settings
from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user import User
from api.services.nudges.phrasing import FixturePhrasingClient


_TODAY = date.today()
# CR is UTC-6; 18:00 UTC = 12:00 CR → outside quiet hours (21:00–07:00), so the
# delivery worker actually sends instead of deferring.
_NOW = datetime(_TODAY.year, _TODAY.month, 15, 18, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def worker_session_factory():
    """A NullPool session factory bound to the test's event loop. The worker
    opens its own sessions (it manages transactions per user), so it can't reuse
    the db_with_user session; a NullPool engine avoids the shared-pool
    "Event loop is closed" teardown the global AsyncSessionLocal would hit."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_near_envelope(session, user_id):
    env = Envelope(
        user_id=user_id, name="Súper", envelope_class="needs",
        limit_amount=Decimal("100000"), currency="CRC",
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    session.add(
        Transaction(
            user_id=user_id, amount=-92000, currency="CRC",
            transaction_date=date(_TODAY.year, _TODAY.month, 10),
            source="manual", status="confirmed", archived=False,
            envelope_id=env.id,
        )
    )
    await session.commit()
    return env.id


@pytest.mark.asyncio
async def test_run_nudges_for_user_evaluates_and_delivers(
    db_with_user, worker_session_factory, monkeypatch
):
    session, user_id = db_with_user
    # Delivery only sends to a user with a telegram_user_id.
    user = await session.get(User, user_id)
    user.telegram_user_id = 555_000_111
    await session.commit()
    await _seed_near_envelope(session, user_id)

    sent: list = []

    async def _fake_send(message):
        sent.append(message)
        return True

    import bot.nudges_send as ns

    monkeypatch.setattr(ns, "telegram_send_fn", _fake_send)

    from workers.nudges_daily import run_nudges_for_user

    result = await run_nudges_for_user(
        user_id=user_id,
        phrasing_client=FixturePhrasingClient(canned_text="Casi gastás Súper."),
        now=_NOW,
        session_factory=worker_session_factory,
    )

    assert result is not None
    created, sent_count = result
    assert created >= 1
    assert sent_count >= 1
    assert any(m.text == "Casi gastás Súper." for m in sent)


@pytest.mark.asyncio
async def test_run_for_all_users_scans_active_user(
    db_with_user, worker_session_factory, monkeypatch
):
    session, user_id = db_with_user
    await _seed_near_envelope(session, user_id)  # no telegram → won't send

    async def _fake_send(_message):
        return True

    import bot.nudges_send as ns

    monkeypatch.setattr(ns, "telegram_send_fn", _fake_send)

    from workers.nudges_daily import run_nudges_for_all_users

    stats = await run_nudges_for_all_users(
        now=_NOW,
        phrasing_client=FixturePhrasingClient(),
        session_factory=worker_session_factory,
    )
    assert stats.users_scanned >= 1
    assert str(user_id) not in stats.failed_user_ids


@pytest.mark.asyncio
async def test_one_failing_user_does_not_raise(
    db_with_user, worker_session_factory, monkeypatch
):
    """A per-user error returns None instead of propagating, so the fan-out
    loop carries on."""
    _session, user_id = db_with_user
    import workers.nudges_daily as wd

    async def _boom(*args, **kwargs):
        raise RuntimeError("evaluator blew up")

    monkeypatch.setattr(wd, "evaluate_all", _boom)

    result = await wd.run_nudges_for_user(
        user_id=user_id,
        phrasing_client=FixturePhrasingClient(),
        now=_NOW,
        session_factory=worker_session_factory,
    )
    assert result is None


@pytest.mark.asyncio
async def test_scheduler_run_once_safe_swallows_errors(monkeypatch):
    """The scheduler tick must never let an exception escape the loop."""
    import workers.nudges_daily as wd
    from api.services.nudges import scheduler

    async def _boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(wd, "run_nudges_for_all_users", _boom)

    # Must not raise.
    await scheduler.run_once_safe()
