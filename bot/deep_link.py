"""Phase 6e B12 — bot-side helpers for SPA deep linking.

The bot mints `purpose='edit_session'` magic links via
`api.services.auth.magic_link.generate_link` and attaches them as URL
buttons on its replies. Each link is single-use and TTL 30 min by
default (config: `magic_link_ttl_s`). The exchanged SPA session still
expires after 4h on its own (`session_cookie_ttl_s`).

Per Resolución 9.3, there is no public mint endpoint — only the bot
calls `generate_link` directly. This helper is a thin wrapper that
exists so bot callsites don't need to import the auth service tree.
"""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.services.auth.magic_link import generate_link

log = logging.getLogger("bot.deep_link")


async def mint_edit_session_url(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_path: str,
) -> str | None:
    """Mint a single-use `edit_session` magic-link URL pointing at the SPA.

    Returns the URL string on success, or `None` if anything fails
    (validation, DB error). Callers should treat a `None` return as "drop
    the button" — never as "crash the bot reply." We've deliberately
    chosen a swallow-and-log model here because deep linking is a
    convenience, not a correctness path.
    """
    try:
        link = await generate_link(
            db,
            user_id=user_id,
            purpose="edit_session",
            target_path=target_path,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "mint_edit_session_url failed user=%s path=%s err=%s",
            user_id,
            target_path,
            type(exc).__name__,
        )
        return None
    return link.url


async def mint_native_deep_link(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_path: str | None = None,
) -> str | None:
    """Mint a single-use native deep link `<scheme>://exchange?token=...`.

    Phase 6f B15. Same `<selector>.<verifier>` token + single-use/TTL-30m
    machinery as `mint_edit_session_url`, but formatted for the native app's
    custom URL scheme (`settings.native_app_scheme`, default `ledgercr`)
    instead of the SPA https URL. Tapping the link opens the app, where the
    silent `useMagicLinkListener` (Phase 6f B3) exchanges the token for a
    session JWT.

    `target_path` is an optional SPA-style relative path carried through for
    the future native path→screen router; the app signs in regardless.

    Returns the URL string on success, or `None` on any failure — callers
    treat `None` as "drop the link," never as "crash the reply."
    """
    try:
        # Reuse the `edit_session` purpose ("open an authenticated client
        # session") — the same one the SPA deep link uses. The exchange path
        # ignores purpose, and the `ck_magic_link_tokens_purpose` CHECK only
        # allows {onboarding, edit_session}, so a dedicated label would need a
        # migration for no behavioral gain.
        link = await generate_link(
            db,
            user_id=user_id,
            purpose="edit_session",
            target_path=target_path,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "mint_native_deep_link failed user=%s path=%s err=%s",
            user_id,
            target_path,
            type(exc).__name__,
        )
        return None

    query = {"token": link.raw_token}
    if target_path is not None:
        query["path"] = target_path
    return f"{settings.native_app_scheme}://exchange?{urlencode(query)}"
