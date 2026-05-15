"""Phase 6e B12 — bot ↔ SPA deep-link contracts.

Covers the `edit_session` magic-link path on the auth service plus the
bot's `mint_edit_session_url` swallow-on-fail helper:

- generate_link with `purpose='edit_session'` and a valid `target_path`
  emits a URL of shape `${SPA}/?token=...&path=/foo` and persists the
  target_path on the row.
- generate_link rejects malformed `target_path` (protocol-relative,
  absolute URL, missing leading slash).
- A consumed link is single-use: a second `validate_and_consume` returns
  None.
- `bot.deep_link.mint_edit_session_url` swallows errors and returns
  `None` when the target_path is invalid, so the bot reply still ships.
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select as sa_select

from api.models.magic_link_token import MagicLinkToken
from api.services.auth.magic_link import (
    generate_link,
    validate_and_consume,
)
from bot.deep_link import mint_edit_session_url


@pytest.mark.asyncio
async def test_edit_session_link_url_shape_and_target_path_persisted(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(
        session,
        user_id=user_id,
        purpose="edit_session",
        target_path="/debts/abc-123",
    )

    parsed = urlparse(link.url)
    params = parse_qs(parsed.query)
    assert params.get("token") == [link.raw_token]
    assert params.get("path") == ["/debts/abc-123"]

    row = (
        await session.execute(
            sa_select(MagicLinkToken).where(MagicLinkToken.id == link.record_id)
        )
    ).scalar_one()
    assert row.purpose == "edit_session"
    assert row.target_path == "/debts/abc-123"


@pytest.mark.asyncio
async def test_edit_session_link_rejects_malformed_target_path(db_with_user):
    session, user_id = db_with_user
    for bad in ("foo/bar", "//evil.example.com", "https://evil.example/foo"):
        with pytest.raises(ValueError):
            await generate_link(
                session,
                user_id=user_id,
                purpose="edit_session",
                target_path=bad,
            )


@pytest.mark.asyncio
async def test_edit_session_link_is_single_use(db_with_user):
    session, user_id = db_with_user
    link = await generate_link(
        session,
        user_id=user_id,
        purpose="edit_session",
        target_path="/memoria",
    )
    first = await validate_and_consume(session, link.raw_token)
    assert first is not None
    assert first.user_id == user_id

    second = await validate_and_consume(session, link.raw_token)
    assert second is None


@pytest.mark.asyncio
async def test_mint_edit_session_url_returns_url_on_success(db_with_user):
    session, user_id = db_with_user
    url = await mint_edit_session_url(
        session, user_id=user_id, target_path="/transactions?highlight=abc"
    )
    assert url is not None
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert "token" in params
    assert params.get("path") == ["/transactions?highlight=abc"]


@pytest.mark.asyncio
async def test_mint_edit_session_url_returns_none_on_invalid_path(db_with_user):
    session, user_id = db_with_user
    url = await mint_edit_session_url(
        session, user_id=user_id, target_path="not-a-relative-path"
    )
    assert url is None


@pytest.mark.asyncio
async def test_mint_edit_session_url_returns_none_for_missing_user(db_with_user):
    session, _user_id = db_with_user
    # Unknown user_id: the row insert will fail on the FK, but the helper
    # is expected to swallow and return None.
    url = await mint_edit_session_url(
        session, user_id=uuid.uuid4(), target_path="/debts"
    )
    assert url is None
