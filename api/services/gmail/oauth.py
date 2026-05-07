"""Google OAuth Authorization Code flow for Gmail readonly access.

Three pure entry points:

    build_auth_url(user_id, redis)        → str
    exchange_code(code, state, redis)     → OAuthExchangeResult
    refresh_access_token(refresh_token)   → AccessToken

The `state` parameter is a HS256 JWT signed with GMAIL_OAUTH_STATE_SECRET.
Payload `{user_id, nonce, exp}`. The nonce is a one-time random string
that's stored in Redis at issue time and deleted on first use — that
defeats both CSRF and replay even if an attacker captures a valid state
mid-flight.

Why we don't use google-auth-oauthlib: the Authorization Code flow is
literally one redirect + one POST. Pulling in the SDK adds dependencies
and hides what's happening. httpx + signed state is straightforward and
testable without mocks of the SDK.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from redis.asyncio import Redis

from ...config import settings


GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_STATE_ALG = "HS256"

# Redis key for the one-time nonce. Lifetime mirrors the JWT exp.
def _nonce_key(nonce: str) -> str:
    return f"gmail_oauth_nonce:{nonce}"


# ── exceptions ────────────────────────────────────────────────────────────────


class OAuthStateError(Exception):
    """Invalid, expired, replayed, or tampered state JWT."""


class OAuthExchangeError(Exception):
    """Google rejected the code/refresh exchange."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ── result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OAuthExchangeResult:
    user_id: uuid.UUID
    refresh_token: str
    access_token: str
    expires_in: int
    granted_scopes: list[str]


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int


# ── state JWT ─────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_secret() -> str:
    secret = settings.gmail_oauth_state_secret
    if not secret:
        raise OAuthStateError(
            "GMAIL_OAUTH_STATE_SECRET is not configured. Set it in .env "
            "before initiating OAuth."
        )
    return secret


def _encode_state(*, user_id: uuid.UUID, nonce: str, ttl_s: int) -> str:
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "nonce": nonce,
        "exp": int((_now() + timedelta(seconds=ttl_s)).timestamp()),
        "iat": int(_now().timestamp()),
    }
    return jwt.encode(payload, _require_secret(), algorithm=_STATE_ALG)


def _decode_state(state: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            state,
            _require_secret(),
            algorithms=[_STATE_ALG],
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise OAuthStateError("state expired") from e
    except jwt.InvalidTokenError as e:
        raise OAuthStateError(f"state invalid: {e}") from e


# ── auth URL builder ──────────────────────────────────────────────────────────


async def build_auth_url(*, user_id: uuid.UUID, redis: Redis) -> str:
    """Build the Google consent URL for `user_id`.

    Stores a one-time nonce in Redis (TTL = state TTL). The callback
    deletes it on first use; replays therefore fail with OAuthStateError.

    `prompt=consent` forces Google to re-issue a refresh token even if the
    user has previously consented — necessary because Google only returns
    `refresh_token` on the FIRST consent unless prompt=consent is set.
    Without it, a user who reconnects after /desconectar_gmail would
    receive only an access_token and our flow would silently break.
    """
    if not settings.gmail_client_id or not settings.gmail_redirect_uri:
        raise OAuthStateError(
            "Gmail OAuth is not configured (missing client_id / redirect_uri)"
        )

    ttl = settings.gmail_oauth_state_ttl_s
    nonce = secrets.token_urlsafe(16)
    await redis.set(_nonce_key(nonce), str(user_id), ex=ttl)

    state = _encode_state(user_id=user_id, nonce=nonce, ttl_s=ttl)
    params = {
        "response_type": "code",
        "client_id": settings.gmail_client_id,
        "redirect_uri": settings.gmail_redirect_uri,
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GMAIL_AUTH_URL}?{urlencode(params)}"


# ── code exchange ─────────────────────────────────────────────────────────────


async def _validate_state_and_consume_nonce(
    state: str, redis: Redis
) -> uuid.UUID:
    payload = _decode_state(state)
    nonce = payload.get("nonce")
    user_id_raw = payload.get("user_id")
    if not nonce or not user_id_raw:
        raise OAuthStateError("state payload missing fields")

    # One-time nonce: delete returns 1 only if it existed. 0 means already
    # consumed (replay) or never issued (forged signature won't reach here
    # because _decode_state already verified the HMAC).
    deleted = await redis.delete(_nonce_key(nonce))
    if deleted == 0:
        raise OAuthStateError("state nonce already used")

    try:
        return uuid.UUID(user_id_raw)
    except ValueError as e:
        raise OAuthStateError("state user_id malformed") from e


async def exchange_code(
    *, code: str, state: str, redis: Redis, http: httpx.AsyncClient | None = None
) -> OAuthExchangeResult:
    """Validate state, then trade `code` for tokens.

    Google's response is documented at
    https://developers.google.com/identity/protocols/oauth2/web-server#exchange-authorization-code.

    A successful response contains `access_token`, `expires_in`, `scope`,
    `token_type=Bearer`, and (on first consent / when prompt=consent)
    `refresh_token`. We require refresh_token — the bot is useless without
    it. Missing → raise OAuthExchangeError.
    """
    user_id = await _validate_state_and_consume_nonce(state, redis)

    body = {
        "code": code,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "redirect_uri": settings.gmail_redirect_uri,
        "grant_type": "authorization_code",
    }

    close_after = http is None
    client = http or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(
            GMAIL_TOKEN_URL,
            data=body,
            headers={"Accept": "application/json"},
        )
    finally:
        if close_after:
            await client.aclose()

    if resp.status_code != 200:
        try:
            err = resp.json()
        except ValueError:
            err = {"raw": resp.text}
        raise OAuthExchangeError(
            f"Google token exchange failed ({resp.status_code}): {err}",
            code=err.get("error") if isinstance(err, dict) else None,
        )

    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise OAuthExchangeError(
            "Google response missing refresh_token. This usually means "
            "prompt=consent was not honored — check the auth URL builder."
        )

    return OAuthExchangeResult(
        user_id=user_id,
        refresh_token=refresh_token,
        access_token=data["access_token"],
        expires_in=int(data.get("expires_in", 3600)),
        granted_scopes=str(data.get("scope", "")).split() or [GMAIL_READONLY_SCOPE],
    )


# ── access token refresh ──────────────────────────────────────────────────────


async def refresh_access_token(
    refresh_token: str, *, http: httpx.AsyncClient | None = None
) -> AccessToken:
    """Trade a refresh_token for a fresh access_token.

    The scanner calls this on every run because access tokens are 1h-lived
    and we never persist them. If the refresh_token has been revoked
    (user revoked from myaccount.google.com or Daniel removed them as a
    test user), Google returns 400 with `error=invalid_grant`. The caller
    treats that as "needs reconnect" and updates gmail_credentials.
    """
    body = {
        "refresh_token": refresh_token,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "grant_type": "refresh_token",
    }

    close_after = http is None
    client = http or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(
            GMAIL_TOKEN_URL,
            data=body,
            headers={"Accept": "application/json"},
        )
    finally:
        if close_after:
            await client.aclose()

    if resp.status_code != 200:
        try:
            err = resp.json()
        except ValueError:
            err = {"raw": resp.text}
        raise OAuthExchangeError(
            f"Google refresh failed ({resp.status_code}): {err}",
            code=err.get("error") if isinstance(err, dict) else None,
        )

    data = resp.json()
    return AccessToken(
        token=data["access_token"],
        expires_in=int(data.get("expires_in", 3600)),
    )
