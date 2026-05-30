"""Phase 6f B15 — native deep-link minting + expo_push_token schema.

`mint_native_deep_link` mints the same single-use `<selector>.<verifier>`
magic-link token the SPA path uses, but formats it as
`<scheme>://exchange?token=...` for the native app's custom URL scheme.
Tapping it opens the app, where the silent `useMagicLinkListener` (B3)
exchanges the token via `POST /api/v1/auth/magic-link/exchange`.

Covers:
1. Mint returns a `ledgercr://exchange?...` URL whose token exchanges to a JWT.
2. `target_path` is carried through as a `path=` query param.
3. Single-use — exchanging the minted token twice → second is rejected (401).
4. `users.expo_push_token` (migration 0021) persists on the model.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import settings
from api.database import get_db
from api.main import app
from api.models.user import User
from bot.deep_link import mint_native_deep_link


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _token_from(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


# ── 1. Minted token exchanges to a JWT ────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_native_deep_link_token_exchanges_to_jwt(db_with_user):
    session, user_id = db_with_user
    url = await mint_native_deep_link(session, user_id=user_id)
    assert url is not None
    assert url.startswith(f"{settings.native_app_scheme}://exchange?")
    token = _token_from(url)
    assert "." in token  # selector.verifier

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/auth/magic-link/exchange", json={"token": token}
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["user_id"] == str(user_id)
            assert body["token"]
    finally:
        _clear_db_override()


# ── 2. target_path is carried through ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_native_deep_link_carries_target_path(db_with_user):
    session, user_id = db_with_user
    url = await mint_native_deep_link(
        session, user_id=user_id, target_path="/transactions"
    )
    assert url is not None
    q = parse_qs(urlsplit(url).query)
    assert q["path"][0] == "/transactions"
    assert "token" in q


# ── 3. Single-use ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_native_deep_link_token_is_single_use(db_with_user):
    session, user_id = db_with_user
    url = await mint_native_deep_link(session, user_id=user_id)
    assert url is not None
    token = _token_from(url)

    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            first = await ac.post(
                "/api/v1/auth/magic-link/exchange", json={"token": token}
            )
            assert first.status_code == 200, first.text
            second = await ac.post(
                "/api/v1/auth/magic-link/exchange", json={"token": token}
            )
            assert second.status_code == 401
    finally:
        _clear_db_override()


# ── 4. expo_push_token column (migration 0021) ────────────────────────────────


@pytest.mark.asyncio
async def test_expo_push_token_column_persists(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    assert user is not None
    assert user.expo_push_token is None  # default

    user.expo_push_token = "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
    await session.commit()
    await session.refresh(user)
    assert user.expo_push_token == "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
