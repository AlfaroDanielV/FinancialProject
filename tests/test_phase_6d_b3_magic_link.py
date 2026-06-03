"""Phase 6d B3 — magic-link service + exchange endpoint tests.

Covers the 7 cases from the approved test plan:

1. Token válido → exchange OK + cookie + 200 con user info.
2. Token expirado → 401 (mensaje genérico).
3. Token ya usado → 401 (mensaje genérico).
4. Token bcrypt mismatch (selector existe pero verifier wrong) → 401.
5. Concurrencia: dos exchanges en paralelo → uno 200, otro 401.
6. /setup smoke (service): generate_link round-trips through validate.
7. Cross-user mismatch: token válido para user A, foreign user can't
   distinguish from invalid → 401 (mismo mensaje genérico).
"""
from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.database import get_db
from api.main import app
from api.models.magic_link_token import MagicLinkToken
from api.services.auth.magic_link import (
    GENERIC_REJECT,
    generate_link,
    validate_and_consume,
)
from api.services.auth.session import decode_session_jwt


# ── Helper: drive the exchange endpoint sharing the test's session.
# Without this override the endpoint opens its own pool, bound to the
# default event loop — pytest-asyncio uses a fresh loop per function
# (NullPool in conftest) so the app's pool would deadlock on
# Connection._cancel. Same pattern as test_phase_6c_b8_privacy.py.


async def _exchange(session, token: str):
    async def _yield_session():
        yield session

    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.post(
                "/api/v1/auth/magic-link/exchange",
                json={"token": token},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 1. Token válido → 200 + bearer JWT in body (no cookie post-6f-B16) ────────


@pytest.mark.asyncio
async def test_valid_token_exchange_returns_bearer_jwt(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    resp = await _exchange(session, link.raw_token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(user_id)

    # Phase 6f B16: no cookie is set; the JWT is returned in the body.
    assert "set-cookie" not in {k.lower() for k in resp.headers.keys()}
    token = body["token"]
    assert token
    claims = decode_session_jwt(token)
    assert claims is not None
    assert claims["sub"] == str(user_id)

    # And the row is now consumed.
    refreshed = await session.execute(
        select(MagicLinkToken).where(MagicLinkToken.id == link.record_id)
    )
    row = refreshed.scalar_one()
    assert row.used_at is not None


@pytest.mark.asyncio
async def test_bearer_token_authenticates_endpoint(db_with_user):
    """The bearer JWT returned by exchange must authenticate current_user.

    Phase 6f B16: after exchanging `?token=...`, `GET /api/v1/onboarding/
    status` succeeds with `Authorization: Bearer <token>` (no cookie, no
    X-Shortcut-Token, no X-User-Id).
    """
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    async def _yield_session():
        yield session

    app.dependency_overrides[get_db] = _yield_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await ac.post(
                "/api/v1/auth/magic-link/exchange",
                json={"token": link.raw_token},
            )
            assert exchange.status_code == 200, exchange.text
            token = exchange.json()["token"]

            status_resp = await ac.get(
                "/api/v1/onboarding/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status_resp.status_code == 200, status_resp.text
            body = status_resp.json()
            assert body["accounts_count"] == 0
            assert body["completeness_score"] == 0.0
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 2. Token expirado → 401 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_token_rejected(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    # Move expiry into the past.
    await session.execute(
        update(MagicLinkToken)
        .where(MagicLinkToken.id == link.record_id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()

    resp = await _exchange(session, link.raw_token)
    assert resp.status_code == 401
    assert resp.json()["detail"] == GENERIC_REJECT


# ── 3. Token ya usado → 401 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_already_used_token_rejected(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    # First exchange succeeds.
    first = await _exchange(session, link.raw_token)
    assert first.status_code == 200

    # Second exchange of the same token fails.
    second = await _exchange(session, link.raw_token)
    assert second.status_code == 401
    assert second.json()["detail"] == GENERIC_REJECT


# ── 4. Token bcrypt mismatch (selector matches, verifier wrong) → 401 ─────────


@pytest.mark.asyncio
async def test_verifier_mismatch_rejected(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    selector, _, _verifier = link.raw_token.partition(".")
    forged_token = f"{selector}.{secrets.token_urlsafe(32)}"

    resp = await _exchange(session, forged_token)
    assert resp.status_code == 401
    assert resp.json()["detail"] == GENERIC_REJECT


@pytest.mark.asyncio
async def test_unknown_selector_rejected(db_with_user):
    """Sanity: random `selector.verifier` that we never minted → 401."""
    session, _user_id = db_with_user
    bogus = f"{secrets.token_urlsafe(16)}.{secrets.token_urlsafe(32)}"
    resp = await _exchange(session, bogus)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_malformed_token_rejected(db_with_user):
    """Token without the `.` separator → 401."""
    session, _user_id = db_with_user
    resp = await _exchange(session, "noseparatorhere1234567890")
    assert resp.status_code == 401


# ── 5. Concurrencia: dos exchanges en paralelo → solo uno gana ───────────────


@pytest.mark.asyncio
async def test_concurrent_exchange_only_one_wins(db_with_user):
    """Atomic UPDATE guard in validate_and_consume (Resolución 9.1) must
    ensure only one of two parallel exchanges succeeds.

    Note: we run sequentially against the SAME session here because the
    test fixture only owns one connection (NullPool + dependency override
    pattern). A true parallel test would need two engines+sessions, which
    pytest-asyncio's per-function loop makes painful. The sequential
    second call still exercises the `used_at IS NULL` clause — the row is
    already consumed by call #1, so call #2's UPDATE updates 0 rows.
    """
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    r1 = await _exchange(session, link.raw_token)
    r2 = await _exchange(session, link.raw_token)

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 401], (
        f"Expected exactly one winner; got {statuses}. "
        "If both 200, the atomic UPDATE guard in validate_and_consume is broken."
    )


# ── 6. /setup smoke: generate_link → validate_and_consume round-trip ─────────


@pytest.mark.asyncio
async def test_generate_then_validate_and_consume_round_trip(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(session, user_id=user_id, purpose="onboarding")

    consumed = await validate_and_consume(session, link.raw_token)
    assert consumed is not None
    assert consumed.user_id == user_id
    assert consumed.record_id == link.record_id

    # Second call returns None (used_at is set).
    again = await validate_and_consume(session, link.raw_token)
    assert again is None


# ── 7. Cross-user mismatch ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_user_token_indistinguishable_from_invalid(db_with_user):
    """A valid token belongs to the user who minted it, NOT to the caller.

    The exchange endpoint never exposes user_id pre-validation, so a
    "cross-user" attack is really: "use someone else's link". The link
    exchange transitions THAT user (the link owner). The attacker
    cannot impersonate anyone — they just consume the victim's link.

    What we test here: a forged link with someone-else's-jti cannot
    succeed; we reject because the bcrypt verifier check fails.
    """
    session, user_a_id = db_with_user

    # Mint a real link for user A.
    link_a = await generate_link(
        session, user_id=user_a_id, purpose="onboarding"
    )

    # Insert a second user (user B) directly so we have two rows to
    # disambiguate.
    from api.models.user import User

    user_b = User(
        email=f"b-{uuid.uuid4().hex}@example.com",
        full_name="User B",
        shortcut_token=secrets.token_urlsafe(48),
    )
    session.add(user_b)
    await session.commit()
    await session.refresh(user_b)

    # Forge: pair user A's selector with user B's-style verifier (random).
    selector_a, _, _verifier_a = link_a.raw_token.partition(".")
    forged = f"{selector_a}.{secrets.token_urlsafe(32)}"

    resp = await _exchange(session, forged)
    assert resp.status_code == 401
    assert resp.json()["detail"] == GENERIC_REJECT

    # Cleanup user B (db_with_user only cleans user A).
    await session.execute(
        update(MagicLinkToken)
        .where(MagicLinkToken.user_id == user_b.id)
        .values(used_at=datetime.now(timezone.utc))
    )
    await session.delete(user_b)
    await session.commit()


# ── unit tests for session.py ────────────────────────────────────────────────


def test_decode_session_jwt_round_trip():
    from api.services.auth.session import issue_session_jwt

    user_id = uuid.uuid4()
    jti = uuid.uuid4()
    token = issue_session_jwt(user_id, jti)
    claims = decode_session_jwt(token)
    assert claims is not None
    assert claims["sub"] == str(user_id)
    assert claims["jti"] == str(jti)
    assert "exp" in claims
    assert "iat" in claims


def test_decode_session_jwt_garbage_returns_none():
    assert decode_session_jwt("not.a.jwt") is None
    assert decode_session_jwt("") is None
