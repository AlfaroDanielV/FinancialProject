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

from sqlalchemy.ext.asyncio import AsyncSession

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
