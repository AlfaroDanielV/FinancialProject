"""Phase 6c B8 — privacy endpoints + redaction middleware tests.

Endpoint tests use FastAPI's dependency_overrides to inject a stub
`current_user_via_token` and a real `get_db` session from the
`db_with_user` fixture. Auth-rejection paths use the unovverridden app.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user_via_token
from api.main import app
from api.middleware.sensitive_redaction import (
    REDACTION_MARKER,
    SensitiveRedactionFilter,
    install_on_root_logger,
)
from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import (
    SpendingPatternContent,
    StatedGoalContent,
    StatedPreferenceContent,
)
from api.services.insights import export as export_module
from api.services.insights.persister import persist_insights


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# ── auth: 401 without token ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_my_insights_requires_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/users/me/insights")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_my_insights_requires_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/users/me/insights/export")
    assert resp.status_code == 401


# ── DELETE: returns count + writes audits ───────────────────────────────────


@pytest.mark.asyncio
async def test_delete_my_insights_removes_rows_and_audits(db_with_user):
    session, user_id = db_with_user

    # Seed two insights of different types.
    contents = [
        StatedGoalContent(
            goal_text="Comprar casa",
            target_amount=None,
            target_date=None,
            status="mentioned",
        ),
        SpendingPatternContent(
            category="comida",
            monthly_avg_crc=Decimal("100000"),
            monthly_avg_usd=None,
            trend_pct_3mo=Decimal("0"),
            volatility_score=Decimal("0.10"),
        ),
    ]
    for c in contents:
        if hasattr(c, "raw_quote"):
            setattr(c, "_llm_confidence", Decimal("0.80"))
        source = "llm_extracted" if c.type in {"stated_goal"} else "computed"
        await persist_insights(
            session, user_id=user_id, insights=[c], source=source, now=_utc(2026, 5, 8)
        )
    await session.commit()

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    stub = _StubUser()

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: stub
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete("/api/v1/users/me/insights")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}

    remaining = (
        await session.execute(select(UserInsight).where(UserInsight.user_id == user_id))
    ).scalars().all()
    assert remaining == []

    audits = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "deleted")
        )
    ).scalars().all()
    assert len(audits) == 2
    for row in audits:
        assert row.payload["deletion_reason"] == "api_delete_my_insights"


@pytest.mark.asyncio
async def test_delete_my_insights_zero_count_when_empty(db_with_user):
    session, user_id = db_with_user

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete("/api/v1/users/me/insights")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}


# ── GET export ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_returns_active_rows_and_writes_exported_audit(db_with_user):
    session, user_id = db_with_user

    content = StatedGoalContent(
        goal_text="Ahorrar para el marchamo",
        target_amount=Decimal("500000"),
        target_date=None,
        status="committed",
    )
    setattr(content, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="llm_extracted",
        now=_utc(2026, 5, 8),
    )
    await session.commit()

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights/export")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert body["format_version"] == 1
    assert body["count"] == 1
    assert body["include_expired"] is True
    assert len(body["insights"]) == 1
    assert body["insights"][0]["insight_type"] == "stated_goal"
    assert body["insights"][0]["source"] == "llm_extracted"
    assert "request_id" in body

    # Header echoes the request_id.
    assert resp.headers.get("X-Insights-Export-Request-Id") == body["request_id"]

    # `exported` audit row written.
    audit = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "exported")
        )
    ).scalar_one()
    assert audit.payload["count"] == 1
    assert audit.payload["format"] == "json"
    assert audit.payload["request_id"] == body["request_id"]


@pytest.mark.asyncio
async def test_export_empty_user_returns_zero_count(db_with_user):
    session, user_id = db_with_user

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights/export")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    body = resp.json()
    assert body["count"] == 0
    assert body["insights"] == []
    # Audit row is still emitted — exporting nothing is still an act.
    audit = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "exported")
        )
    ).scalar_one()
    assert audit.payload["count"] == 0


@pytest.mark.asyncio
async def test_export_streaming_kicks_in_above_threshold(db_with_user, monkeypatch):
    """Drop the threshold to 1 row to exercise the StreamingResponse path
    without seeding thousands of insights."""
    session, user_id = db_with_user

    contents = [
        StatedGoalContent(
            goal_text=f"Meta {i}",
            target_amount=None,
            target_date=None,
            status="mentioned",
        )
        for i in range(3)
    ]
    for c in contents:
        await persist_insights(
            session,
            user_id=user_id,
            insights=[c],
            source="llm_extracted",
            now=_utc(2026, 5, 8),
        )
    await session.commit()

    # Force streaming.
    from api.routers import privacy_insights as router_module

    monkeypatch.setattr(router_module, "STREAMING_ROW_THRESHOLD", 1)

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights/export")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    # Streamed body still parses as a single JSON document with the same
    # shape as the non-streaming response.
    body = json.loads(resp.text)
    assert body["user_id"] == str(user_id)
    assert body["format_version"] == 1
    assert len(body["insights"]) == 3
    assert resp.headers.get("X-Insights-Export-Request-Id")


@pytest.mark.asyncio
async def test_export_include_expired_false_excludes_expired(db_with_user):
    session, user_id = db_with_user
    now = datetime.now(timezone.utc)

    # Seed one fresh, one already-expired row.
    fresh = StatedGoalContent(
        goal_text="Fresh goal",
        target_amount=None,
        target_date=None,
        status="mentioned",
    )
    expired = StatedPreferenceContent(
        topic="savings",
        stance="vieja preferencia",
        raw_quote="ya no es relevante",
        extracted_at=now,
    )
    setattr(fresh, "_llm_confidence", Decimal("0.80"))
    setattr(expired, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session, user_id=user_id, insights=[fresh], source="llm_extracted", now=now
    )
    await persist_insights(
        session,
        user_id=user_id,
        insights=[expired],
        source="llm_extracted",
        now=now,
    )
    # Force one row into the past.
    rows = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalars().all()
    expired_row = next(r for r in rows if r.insight_type == "stated_preference")
    expired_row.valid_until = now.replace(year=now.year - 1)
    await session.commit()

    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with_expired = await ac.get(
                "/api/v1/users/me/insights/export?include_expired=true"
            )
            without_expired = await ac.get(
                "/api/v1/users/me/insights/export?include_expired=false"
            )
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert with_expired.json()["count"] == 2
    assert without_expired.json()["count"] == 1
    types_active = [
        i["insight_type"] for i in without_expired.json()["insights"]
    ]
    assert types_active == ["stated_goal"]


# ── redaction middleware unit tests ─────────────────────────────────────────


def test_redaction_filter_redacts_extras_keyed_content():
    flt = SensitiveRedactionFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event",
        args=(),
        exc_info=None,
    )
    # Simulate `log.info("event", extra={"content": <payload>})`.
    record.content = {"raw_quote": "secret"}
    record.user_id = "abc"

    flt.filter(record)
    assert record.content == REDACTION_MARKER
    # Non-sensitive extras pass through.
    assert record.user_id == "abc"


def test_redaction_filter_walks_nested_dicts_in_extras():
    flt = SensitiveRedactionFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event",
        args=(),
        exc_info=None,
    )
    record.payload = {
        "action": "updated",
        "previous_content": {"raw_quote": "ssh"},
        "new_content": {"raw_quote": "ssh2"},
        "dedup_key": "global",
    }
    flt.filter(record)
    assert record.payload["previous_content"] == REDACTION_MARKER
    assert record.payload["new_content"] == REDACTION_MARKER
    # Other fields preserved.
    assert record.payload["action"] == "updated"
    assert record.payload["dedup_key"] == "global"


def test_redaction_filter_walks_args_dicts():
    """logging.LogRecord auto-unwraps a 1-tuple containing a dict into
    just the dict (so format strings can use %(key)s). The filter must
    still scrub the inner sensitive keys."""
    flt = SensitiveRedactionFilter()
    payload = {
        "insight_type": "stated_preference",
        "content": {"raw_quote": "secret stuff"},
    }
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="row=%(insight_type)s",
        args=(payload,),
        exc_info=None,
    )
    flt.filter(record)
    # record.args is the dict itself thanks to LogRecord's auto-unwrap.
    assert record.args["content"] == REDACTION_MARKER
    assert record.args["insight_type"] == "stated_preference"


def test_redaction_filter_walks_args_tuple_of_dicts():
    """Multi-arg case: record.args stays a tuple. Each dict element
    inside the tuple gets walked."""
    flt = SensitiveRedactionFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="a=%s b=%s",
        args=(
            {"insight_type": "x", "content": {"raw_quote": "s"}},
            {"unrelated": "value"},
        ),
        exc_info=None,
    )
    flt.filter(record)
    assert record.args[0]["content"] == REDACTION_MARKER
    assert record.args[1] == {"unrelated": "value"}


def test_redaction_filter_leaves_non_sensitive_records_alone():
    flt = SensitiveRedactionFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    flt.filter(record)
    assert record.args == ("world",)


def test_install_on_root_logger_is_idempotent():
    # Two installs should leave exactly one filter on the root logger.
    install_on_root_logger()
    install_on_root_logger()
    root = logging.getLogger()
    matches = [f for f in root.filters if isinstance(f, SensitiveRedactionFilter)]
    assert len(matches) == 1
