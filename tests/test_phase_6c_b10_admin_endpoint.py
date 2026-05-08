"""Phase 6c B10 — admin endpoint tests."""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user_via_token
from api.main import app


@pytest.mark.asyncio
async def test_run_nightly_requires_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/admin/insights/run-nightly")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_run_nightly_runs_for_caller(db_with_user):
    session, user_id = db_with_user

    class _Stub:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _Stub()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/v1/admin/insights/run-nightly")
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert "insights_proposed" in body
    assert "persisted" in body
    assert "gaps" in body


@pytest.mark.asyncio
async def test_run_nightly_rejects_cross_tenant_user_id(db_with_user):
    session, user_id = db_with_user

    class _Stub:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    other_user_id = uuid.uuid4()
    app.dependency_overrides[current_user_via_token] = lambda: _Stub()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/insights/run-nightly?user_id={other_user_id}"
            )
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_run_nightly_accepts_matching_user_id(db_with_user):
    session, user_id = db_with_user

    class _Stub:
        def __init__(self) -> None:
            self.id = user_id

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user_via_token] = lambda: _Stub()
    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/insights/run-nightly?user_id={user_id}"
            )
    finally:
        app.dependency_overrides.pop(current_user_via_token, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(user_id)
