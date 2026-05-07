"""Endpoint tests for /api/v1/gmail/oauth/*.

We override `current_user` and the DB session for `/oauth/start` and
`/status`. The callback path requires a real DB row (insert into
gmail_credentials), so those tests use the `db_with_user` fixture from
conftest.py — they need Postgres running. Tests that need the DB are
marked `requires_db` and skipped automatically when Postgres is down.
"""
from __future__ import annotations

import socket
import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.config import settings
from api.dependencies import current_user
from api.main import app
from api.models.gmail_credential import GmailCredential
from api.services import secrets as secrets_mod
from api.services.gmail import oauth as oauth_svc


# ── stub Redis ────────────────────────────────────────────────────────────────


class _StubRedis:
    """Subset of redis.asyncio.Redis the OAuth flow uses.

    Implements set / delete / publish — enough for build_auth_url,
    _validate_state_and_consume_nonce, and publish_callback.
    """

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key, value, ex=None):
        exp = (time.time() + ex) if ex else None
        self.store[key] = (value, exp)
        return True

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                removed += 1
        return removed

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 0


_stub_redis_singleton = _StubRedis()


def _db_reachable() -> bool:
    """Quick TCP probe of the configured Postgres URL host:port.

    Used to skip integration tests when the DB isn't up; matches the
    project convention (conftest.py docstring says DB tests fail loudly
    when Postgres is offline, but for a single B4 test file we'd rather
    skip than red-flag the suite).
    """
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = url.hostname or "localhost"
        port = url.port or 5432
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable; integration test"
)


class _StubUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.telegram_user_id = 555
        self.timezone = "America/Costa_Rica"
        self.currency = "CRC"
        self.status = "active"


@pytest.fixture(autouse=True)
def _set_oauth_secrets(monkeypatch):
    monkeypatch.setattr(
        settings, "gmail_oauth_state_secret", "test-secret-xx-aaaaaaaaaaa"
    )
    monkeypatch.setattr(settings, "gmail_client_id", "cid")
    monkeypatch.setattr(settings, "gmail_client_secret", "csecret")
    monkeypatch.setattr(
        settings,
        "gmail_redirect_uri",
        "http://localhost:8000/api/v1/gmail/oauth/callback",
    )
    monkeypatch.setattr(settings, "gmail_oauth_state_ttl_s", 600)
    monkeypatch.setattr(settings, "secret_store_backend", "env")
    monkeypatch.setattr(settings, "dev_secret_prefix", "DEV_SECRET_")
    secrets_mod.reset_store()

    # Override redis everywhere the router and helpers reach for it.
    from api.routers import gmail as gmail_router_module
    import api.redis_client as redis_module
    import bot.gmail_pubsub as pubsub_module
    import api.services.gmail.oauth as oauth_module

    stub = _StubRedis()
    monkeypatch.setattr(gmail_router_module, "get_redis", lambda: stub)
    monkeypatch.setattr(redis_module, "get_redis", lambda: stub)
    # The pubsub helper takes redis as a param so no patch needed there.
    yield
    secrets_mod.reset_store()


@pytest.fixture
def stub_user():
    return _StubUser()


@pytest.fixture
def client_with_user(stub_user):
    app.dependency_overrides[current_user] = lambda: stub_user
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def client_no_auth():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── /oauth/start ──────────────────────────────────────────────────────────────


async def test_oauth_start_returns_auth_url_and_ttl(client_with_user, stub_user):
    async with client_with_user as ac:
        resp = await ac.post("/api/v1/gmail/oauth/start")
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_url" in body
    assert body["expires_in_seconds"] == settings.gmail_oauth_state_ttl_s

    parsed = urlparse(body["auth_url"])
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["cid"]
    assert qs["scope"] == [oauth_svc.GMAIL_READONLY_SCOPE]


async def test_oauth_start_requires_auth(client_no_auth):
    async with client_no_auth as ac:
        resp = await ac.post("/api/v1/gmail/oauth/start")
    assert resp.status_code == 401


async def test_oauth_start_503_when_secret_missing(monkeypatch, client_with_user):
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "")
    async with client_with_user as ac:
        resp = await ac.post("/api/v1/gmail/oauth/start")
    assert resp.status_code == 503


# ── /oauth/callback ──────────────────────────────────────────────────────────


async def test_callback_redirects_to_error_when_user_denies(client_no_auth):
    async with client_no_auth as ac:
        resp = await ac.get(
            "/api/v1/gmail/oauth/callback",
            params={
                "error": "access_denied",
                "error_description": "User denied",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/static/gmail-error.html" in resp.headers["location"]
    assert "reason=denied" in resp.headers["location"]


async def test_callback_rejects_missing_params(client_no_auth):
    async with client_no_auth as ac:
        resp = await ac.get(
            "/api/v1/gmail/oauth/callback", follow_redirects=False
        )
    assert resp.status_code == 303
    assert "reason=invalid_request" in resp.headers["location"]


async def test_callback_rejects_bad_state(client_no_auth):
    async with client_no_auth as ac:
        resp = await ac.get(
            "/api/v1/gmail/oauth/callback",
            params={"code": "x", "state": "not-a-jwt"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "reason=invalid_state" in resp.headers["location"]


# ── full success path (requires Postgres) ─────────────────────────────────────
# This one exercises the whole DB write path: it needs a real user_id
# the FK can resolve. We piggyback on the conftest db_with_user fixture
# AND mock the Google token exchange via monkeypatching exchange_code.


@requires_db
async def test_callback_success_persists_credential_and_writes_secret(
    db_with_user, monkeypatch, client_no_auth
):
    session, user_id = db_with_user

    captured: dict = {}

    async def fake_exchange(*, code, state, redis, http=None):
        captured["code"] = code
        return oauth_svc.OAuthExchangeResult(
            user_id=user_id,
            refresh_token="rt-fake",
            access_token="at-fake",
            expires_in=3600,
            granted_scopes=[oauth_svc.GMAIL_READONLY_SCOPE],
        )

    # Patch where the router imports exchange_code from.
    from api.routers import gmail as gmail_router_module
    monkeypatch.setattr(
        gmail_router_module.oauth_svc, "exchange_code", fake_exchange
    )

    async with client_no_auth as ac:
        resp = await ac.get(
            "/api/v1/gmail/oauth/callback",
            params={"code": "auth-code", "state": "anything"},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/static/gmail-connected.html"

    # gmail_credentials row exists for our test user.
    found = await session.execute(
        select(GmailCredential).where(GmailCredential.user_id == user_id)
    )
    cred = found.scalar_one()
    assert cred.kv_secret_name == secrets_mod.kv_name_for_user(user_id)
    assert oauth_svc.GMAIL_READONLY_SCOPE in (cred.scopes or [])
    assert cred.revoked_at is None

    # Refresh token landed in the env store under the expected key.
    store = secrets_mod.get_secret_store()
    assert await store.get(cred.kv_secret_name) == "rt-fake"


@requires_db
async def test_status_reports_disconnected_when_no_row(
    client_with_user, db_with_user
):
    """Use db_with_user so the route runs against the same per-test engine
    instead of the cached global one — needed when this test runs after
    test_callback_success which closes its own loop."""
    from api.database import get_db

    session, _ = db_with_user

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with client_with_user as ac:
            resp = await ac.get("/api/v1/gmail/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "connected": False,
            "granted_at": None,
            "activated_at": None,
            "revoked_at": None,
            "last_refresh_at": None,
            "scopes": [],
        }
    finally:
        app.dependency_overrides.pop(get_db, None)
