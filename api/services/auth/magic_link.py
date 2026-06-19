"""Phase 6d B3 — magic-link generation, validation, and atomic consumption.

Token format: `<selector>.<verifier>` (Resolución 9.9).
- selector: ~22 chars, plain, indexed UNIQUE → O(1) lookup.
- verifier: ~43 chars, bcrypt'd in `magic_link_tokens.token_hash`.

`generate_link` is invoked by the bot directly (no public mint endpoint —
Resolución 9.3). `validate_and_consume` is invoked by the
`/api/v1/auth/magic-link/exchange` endpoint and runs the bcrypt check
plus an atomic `UPDATE ... WHERE used_at IS NULL` to prevent races.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models.magic_link_token import MagicLinkToken

# Generic error message shared by every failure path so we don't leak
# whether the token was unknown / expired / already used / cross-user.
GENERIC_REJECT = "Link inválido o expirado. Pedile uno nuevo al bot con /setup."


@dataclass
class GeneratedLink:
    # Phase 6f B16: the SPA `url` field was removed with the SPA. Callers
    # build their own URL from `raw_token` — the native deep link
    # (`bot.deep_link.mint_native_deep_link`) formats `ledgercr://exchange`.
    raw_token: str
    record_id: uuid.UUID


def _split(raw_token: str) -> tuple[str, str] | None:
    """Split a `<selector>.<verifier>` token. Returns None on malformed input."""
    if not raw_token or "." not in raw_token:
        return None
    selector, _, verifier = raw_token.partition(".")
    if not selector or not verifier:
        return None
    return selector, verifier


def _validate_target_path(target_path: str | None) -> str | None:
    if target_path is None:
        return None
    if (
        not target_path.startswith("/")
        or target_path.startswith("//")
        or "://" in target_path
    ):
        raise ValueError("target_path debe ser una ruta relativa.")
    return target_path


async def generate_link(
    session: AsyncSession,
    user_id: uuid.UUID,
    purpose: str = "onboarding",
    target_path: str | None = None,
) -> GeneratedLink:
    """Mint a single-use magic link. Persists the row in a transaction; the
    caller is responsible for committing if calling within an external
    session context.
    """
    selector = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(32)
    raw_token = f"{selector}.{verifier}"

    token_hash = bcrypt.hashpw(
        verifier.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.bcrypt_rounds),
    ).decode("utf-8")

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.magic_link_ttl_s
    )

    clean_target_path = _validate_target_path(target_path)
    record = MagicLinkToken(
        user_id=user_id,
        selector=selector,
        token_hash=token_hash,
        jti=uuid.uuid4(),
        purpose=purpose,
        target_path=clean_target_path,
        expires_at=expires_at,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    return GeneratedLink(raw_token=raw_token, record_id=record.id)


@dataclass
class ConsumedLink:
    user_id: uuid.UUID
    record_id: uuid.UUID
    jti: uuid.UUID


async def validate_and_consume(
    session: AsyncSession, raw_token: str
) -> Optional[ConsumedLink]:
    """Validate the raw token and atomically mark it consumed.

    Returns ConsumedLink on success, None on any failure (unknown selector,
    bcrypt mismatch, expired, already used, race-lost).
    """
    parts = _split(raw_token)
    if parts is None:
        return None
    selector, verifier = parts

    now = datetime.now(timezone.utc)

    # O(1) lookup by selector. Only fetch rows still in the consumption
    # window — the verifier check happens against this single candidate.
    result = await session.execute(
        select(MagicLinkToken).where(
            MagicLinkToken.selector == selector,
            MagicLinkToken.expires_at > now,
            MagicLinkToken.used_at.is_(None),
        )
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        return None

    # bcrypt.checkpw is constant-time per call (independent of mismatch
    # position), so timing leakage between mismatch / no-row is bounded
    # by selector existence — selectors are random 22-char tokens, so
    # an attacker can't enumerate users via timing.
    try:
        ok = bcrypt.checkpw(
            verifier.encode("utf-8"),
            candidate.token_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return None
    if not ok:
        return None

    # Atomic single-use enforcement. If two requests race, only one wins
    # the WHERE clause; the loser updates 0 rows.
    update_result = await session.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.id == candidate.id,
            MagicLinkToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    await session.commit()
    if update_result.rowcount != 1:
        return None

    return ConsumedLink(
        user_id=candidate.user_id,
        record_id=candidate.id,
        jti=candidate.jti,
    )
