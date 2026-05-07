"""Exchange and refresh paths against an httpx MockTransport.

We mock the network because the unit under test is "what do we send to
Google and what do we do with the response", not Google's actual API.
Integration is covered later in the manual approval gate after B4.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from api.config import settings
from api.services.gmail import oauth
from .test_gmail_oauth_state import StubRedis  # reuse


@pytest.fixture(autouse=True)
def _set_secrets(monkeypatch):
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "test-secret-aaaaaaa")
    monkeypatch.setattr(settings, "gmail_client_id", "cid")
    monkeypatch.setattr(settings, "gmail_client_secret", "csecret")
    monkeypatch.setattr(
        settings,
        "gmail_redirect_uri",
        "http://localhost:8000/api/v1/gmail/oauth/callback",
    )
    monkeypatch.setattr(settings, "gmail_oauth_state_ttl_s", 600)


def _mock_token_endpoint(response_json: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == oauth.GMAIL_TOKEN_URL
        # Body is x-www-form-urlencoded
        body = dict(
            pair.split("=") for pair in request.content.decode().split("&")
        )
        assert body["client_id"] == "cid"
        assert body["client_secret"] == "csecret"
        return httpx.Response(status_code, json=response_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_code_happy_path():
    redis = StubRedis()
    user_id = uuid.uuid4()
    from urllib.parse import parse_qs, urlparse

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)
    state = parse_qs(urlparse(url).query)["state"][0]

    http = _mock_token_endpoint(
        {
            "access_token": "ya29.fake",
            "expires_in": 3599,
            "refresh_token": "1//refresh-fake",
            "scope": oauth.GMAIL_READONLY_SCOPE,
            "token_type": "Bearer",
        }
    )
    result = await oauth.exchange_code(
        code="auth-code-fake", state=state, redis=redis, http=http
    )

    assert result.user_id == user_id
    assert result.refresh_token == "1//refresh-fake"
    assert result.access_token == "ya29.fake"
    assert result.expires_in == 3599
    assert oauth.GMAIL_READONLY_SCOPE in result.granted_scopes


async def test_exchange_code_missing_refresh_token_is_fatal():
    """If Google omits refresh_token, we cannot run the daily worker.
    Fail loudly so the user knows to retry."""
    redis = StubRedis()
    user_id = uuid.uuid4()
    from urllib.parse import parse_qs, urlparse

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)
    state = parse_qs(urlparse(url).query)["state"][0]

    http = _mock_token_endpoint(
        {
            "access_token": "ya29.fake",
            "expires_in": 3599,
            "scope": oauth.GMAIL_READONLY_SCOPE,
            # no refresh_token
        }
    )
    with pytest.raises(oauth.OAuthExchangeError, match="missing refresh_token"):
        await oauth.exchange_code(
            code="x", state=state, redis=redis, http=http
        )


async def test_exchange_code_propagates_google_error():
    redis = StubRedis()
    user_id = uuid.uuid4()
    from urllib.parse import parse_qs, urlparse

    url = await oauth.build_auth_url(user_id=user_id, redis=redis)
    state = parse_qs(urlparse(url).query)["state"][0]

    http = _mock_token_endpoint(
        {"error": "invalid_grant", "error_description": "Bad code"}, status_code=400
    )
    with pytest.raises(oauth.OAuthExchangeError) as exc:
        await oauth.exchange_code(
            code="bad", state=state, redis=redis, http=http
        )
    assert exc.value.code == "invalid_grant"


async def test_refresh_access_token_happy_path():
    http = _mock_token_endpoint(
        {"access_token": "new-access", "expires_in": 3500, "token_type": "Bearer"}
    )
    tok = await oauth.refresh_access_token("rt-fake", http=http)
    assert tok.token == "new-access"
    assert tok.expires_in == 3500


async def test_refresh_access_token_invalid_grant_surfaces_code():
    http = _mock_token_endpoint(
        {"error": "invalid_grant"}, status_code=400
    )
    with pytest.raises(oauth.OAuthExchangeError) as exc:
        await oauth.refresh_access_token("revoked-rt", http=http)
    assert exc.value.code == "invalid_grant"
