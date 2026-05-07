"""Unit tests for the OAuth state JWT used in the Gmail flow.

These tests cover the four invariants:
    1. A freshly-built state encodes the user_id and nonce we passed in.
    2. The nonce is registered in Redis with a TTL bounded by the JWT exp.
    3. Tampering, expiration, and forged signatures are all rejected.
    4. The nonce is one-time: once consumed by exchange_code, replays fail.

We use an in-process redis stub instead of fakeredis to avoid the dep.
The shape of `set` / `delete` we exercise is tiny.
"""
from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

from api.config import settings
from api.services.gmail import oauth


# ── stub redis ────────────────────────────────────────────────────────────────


class StubRedis:
    """Minimal subset of redis.asyncio.Redis that the OAuth helpers use."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.calls: list[tuple[str, ...]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        expiry = (time.time() + ex) if ex else None
        self.store[key] = (value, expiry)
        self.calls.append(("set", key, value, str(ex) if ex else "None"))
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        now = time.time()
        for k in keys:
            entry = self.store.get(k)
            if entry is None:
                continue
            _, exp = entry
            if exp is not None and exp < now:
                # Treat as already expired; don't count toward removed.
                self.store.pop(k, None)
                continue
            self.store.pop(k, None)
            removed += 1
        self.calls.append(("delete",) + keys)
        return removed


@pytest.fixture(autouse=True)
def _set_oauth_secret(monkeypatch):
    """Ensure the secret is set for every test in this module.

    The shipped default is ''. Tests inject a fixed secret so JWT signing
    is deterministic per-run.
    """
    monkeypatch.setattr(
        settings, "gmail_oauth_state_secret", "test-secret-32-bytes-min-aaaaaaaaa"
    )
    monkeypatch.setattr(settings, "gmail_client_id", "test-client-id")
    monkeypatch.setattr(
        settings,
        "gmail_redirect_uri",
        "http://localhost:8000/api/v1/gmail/oauth/callback",
    )
    monkeypatch.setattr(settings, "gmail_oauth_state_ttl_s", 600)
    yield


# ── build_auth_url ────────────────────────────────────────────────────────────


async def test_build_auth_url_encodes_user_id_in_state():
    redis = StubRedis()
    user_id = uuid.uuid4()

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["test-client-id"]
    assert qs["scope"] == [oauth.GMAIL_READONLY_SCOPE]
    assert qs["access_type"] == ["offline"]
    # prompt=consent is required so Google re-issues a refresh_token on
    # subsequent connects after /desconectar_gmail.
    assert qs["prompt"] == ["consent"]

    state = qs["state"][0]
    payload = jwt.decode(
        state,
        settings.gmail_oauth_state_secret,
        algorithms=["HS256"],
    )
    assert payload["user_id"] == str(user_id)
    assert "nonce" in payload and len(payload["nonce"]) > 10


async def test_build_auth_url_registers_nonce_in_redis_with_ttl():
    redis = StubRedis()
    user_id = uuid.uuid4()

    await oauth.build_auth_url(user_id=user_id, redis=redis)

    keys = [k for k in redis.store if k.startswith("gmail_oauth_nonce:")]
    assert len(keys) == 1
    _, exp = redis.store[keys[0]]
    # Should expire within (now, now + ttl + slack).
    assert exp is not None
    assert time.time() + 590 <= exp <= time.time() + 610


async def test_build_auth_url_fails_when_secret_missing(monkeypatch):
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "")
    redis = StubRedis()

    with pytest.raises(oauth.OAuthStateError):
        await oauth.build_auth_url(user_id=uuid.uuid4(), redis=redis)


# ── state validation (CSRF, replay, tampering, expiration) ───────────────────


async def test_validate_state_consumes_nonce_one_time():
    redis = StubRedis()
    user_id = uuid.uuid4()

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)
    state = parse_qs(urlparse(url).query)["state"][0]

    resolved = await oauth._validate_state_and_consume_nonce(state, redis)
    assert resolved == user_id
    # Replay: the nonce was deleted on first use.
    with pytest.raises(oauth.OAuthStateError, match="already used"):
        await oauth._validate_state_and_consume_nonce(state, redis)


async def test_validate_state_rejects_tampered_payload():
    redis = StubRedis()
    user_id = uuid.uuid4()

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)
    state = parse_qs(urlparse(url).query)["state"][0]

    # Flip the last char of the signature → HMAC mismatch.
    tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
    with pytest.raises(oauth.OAuthStateError, match="invalid"):
        await oauth._validate_state_and_consume_nonce(tampered, redis)


async def test_validate_state_rejects_wrong_secret():
    redis = StubRedis()
    user_id = uuid.uuid4()
    # Build a state with a different secret; verifying with our secret
    # must fail even though the structure is valid.
    forged = jwt.encode(
        {
            "user_id": str(user_id),
            "nonce": "x" * 16,
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        },
        "different-secret",
        algorithm="HS256",
    )
    with pytest.raises(oauth.OAuthStateError, match="invalid"):
        await oauth._validate_state_and_consume_nonce(forged, redis)


async def test_validate_state_rejects_expired():
    redis = StubRedis()
    expired = jwt.encode(
        {
            "user_id": str(uuid.uuid4()),
            "nonce": "x" * 16,
            "exp": int(time.time()) - 10,
            "iat": int(time.time()) - 700,
        },
        settings.gmail_oauth_state_secret,
        algorithm="HS256",
    )
    with pytest.raises(oauth.OAuthStateError, match="expired"):
        await oauth._validate_state_and_consume_nonce(expired, redis)


async def test_validate_state_rejects_unknown_nonce_even_with_valid_jwt():
    """Defense in depth: a JWT we signed but whose nonce was never written
    to Redis should still be rejected, because consume returns 0."""
    redis = StubRedis()
    forged = jwt.encode(
        {
            "user_id": str(uuid.uuid4()),
            "nonce": "never-issued",
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        },
        settings.gmail_oauth_state_secret,
        algorithm="HS256",
    )
    with pytest.raises(oauth.OAuthStateError, match="already used"):
        await oauth._validate_state_and_consume_nonce(forged, redis)


async def test_validate_state_rejects_malformed_user_id():
    redis = StubRedis()
    nonce = "n" * 16
    await redis.set(oauth._nonce_key(nonce), "irrelevant", ex=600)
    state = jwt.encode(
        {
            "user_id": "not-a-uuid",
            "nonce": nonce,
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        },
        settings.gmail_oauth_state_secret,
        algorithm="HS256",
    )
    with pytest.raises(oauth.OAuthStateError, match="malformed"):
        await oauth._validate_state_and_consume_nonce(state, redis)
