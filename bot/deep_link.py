"""Bot-side helper for native deep linking.

The bot mints `purpose='edit_session'` magic links via
`api.services.auth.magic_link.generate_link` and formats them as
`<scheme>://exchange?token=...` links. Each link is single-use and TTL
30 min by default (config: `magic_link_ttl_s`); the exchanged session JWT
expires after 4h (`session_ttl_s`).

Per Resolución 9.3, there is no public mint endpoint — only the bot
calls `generate_link` directly. This helper is a thin wrapper that
exists so bot callsites don't need to import the auth service tree.

Phase 6f B16: the SPA was retired, so `mint_edit_session_url` (which
returned an https SPA URL) was removed. `mint_native_deep_link` is the
only deep-link minter now.
"""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.services.auth.magic_link import generate_link

log = logging.getLogger("bot.deep_link")


async def mint_native_deep_link(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_path: str | None = None,
    purpose: str = "edit_session",
) -> str | None:
    """Mint a single-use native deep link `<scheme>://exchange?token=...`.

    Phase 6f B15. Single-use `<selector>.<verifier>` token (TTL 30m)
    formatted for the native app's custom URL scheme
    (`settings.native_app_scheme`, default `ledgercr`). Tapping the link
    opens the app, where the silent `useMagicLinkListener` (Phase 6f B3)
    exchanges the token for a session JWT.

    `target_path` is an optional relative path carried through for the
    future native path→screen router; the app signs in regardless.
    `purpose` is one of the `ck_magic_link_tokens_purpose` values
    (`onboarding` | `edit_session`) — only for audit, the exchange path
    ignores it. Onboarding callers pass `onboarding`.

    Returns the URL string on success, or `None` on any failure — callers
    treat `None` as "drop the link," never as "crash the reply."
    """
    try:
        link = await generate_link(
            db,
            user_id=user_id,
            purpose=purpose,
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
