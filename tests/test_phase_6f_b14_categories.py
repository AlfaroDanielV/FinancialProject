"""Phase 6f B14 — Categories backend contracts.

Covers:
1. GET /categories seeds defaults on first call and returns them.
2. POST /categories creates a new category (201).
3. POST /categories 409 on duplicate active name.
4. PATCH /categories/{id} archives a non-default category.
5. PATCH /categories/{id} 400 when trying to archive a default category.
6. PATCH /categories/{id} restores an archived category.
7. GET /categories?include_archived=true includes archived rows.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ── 1. GET seeds defaults ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_categories_seeds_defaults(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/categories")
            assert resp.status_code == 200, resp.text
            cats = resp.json()
            assert len(cats) > 0
            names = [c["name"] for c in cats]
            # At least one default category should exist (e.g. "Alimentación")
            assert any(c["is_default"] for c in cats)
    finally:
        _clear()


# ── 2. POST creates a new category ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/categories",
                json={"name": "Suscripciones", "kind": "expense", "color": "#4A6741"},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["name"] == "Suscripciones"
            assert body["kind"] == "expense"
            assert body["is_default"] is False
            assert body["archived"] is False
    finally:
        _clear()


# ── 3. POST 409 on duplicate active name ──────────────────────────────────────


@pytest.mark.asyncio
async def test_create_category_duplicate_name(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/api/v1/categories",
                json={"name": "Duplicada", "kind": "expense", "color": "#4A6741"},
            )
            resp = await ac.post(
                "/api/v1/categories",
                json={"name": "Duplicada", "kind": "expense", "color": "#4A6741"},
            )
            assert resp.status_code == 409, resp.text
    finally:
        _clear()


# ── 4. PATCH archives a non-default category ──────────────────────────────────


@pytest.mark.asyncio
async def test_archive_non_default_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create = await ac.post(
                "/api/v1/categories",
                json={"name": "Para archivar", "kind": "expense", "color": "#8B3A2A"},
            )
            cat_id = create.json()["id"]

            resp = await ac.patch(
                f"/api/v1/categories/{cat_id}", json={"archived": True}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["archived"] is True
    finally:
        _clear()


# ── 5. PATCH 400 on archiving a default category ──────────────────────────────


@pytest.mark.asyncio
async def test_archive_default_category_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Seed defaults first
            list_resp = await ac.get("/api/v1/categories")
            defaults = [c for c in list_resp.json() if c["is_default"]]
            assert defaults, "No defaults seeded"

            resp = await ac.patch(
                f"/api/v1/categories/{defaults[0]['id']}",
                json={"archived": True},
            )
            assert resp.status_code == 400, resp.text
    finally:
        _clear()


# ── 6. PATCH restores an archived category ────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_archived_category(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create = await ac.post(
                "/api/v1/categories",
                json={"name": "Para restaurar", "kind": "income", "color": "#3D5C35"},
            )
            cat_id = create.json()["id"]
            await ac.patch(f"/api/v1/categories/{cat_id}", json={"archived": True})

            resp = await ac.patch(
                f"/api/v1/categories/{cat_id}", json={"archived": False}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["archived"] is False
    finally:
        _clear()


# ── 7. GET include_archived returns archived rows ─────────────────────────────


@pytest.mark.asyncio
async def test_list_categories_include_archived(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create = await ac.post(
                "/api/v1/categories",
                json={"name": "Cat archivada", "kind": "expense", "color": "#4A6741"},
            )
            cat_id = create.json()["id"]
            await ac.patch(f"/api/v1/categories/{cat_id}", json={"archived": True})

            # Without flag
            resp = await ac.get("/api/v1/categories")
            names = [c["name"] for c in resp.json()]
            assert "Cat archivada" not in names

            # With flag
            resp2 = await ac.get(
                "/api/v1/categories", params={"include_archived": True}
            )
            names2 = [c["name"] for c in resp2.json()]
            assert "Cat archivada" in names2
    finally:
        _clear()
