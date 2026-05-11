"""Phase 6d B3 — session JWT signing for the cookie set post-exchange.

JWT HS256, exp 4h, jti = magic_link.jti (lets us revoke a session by
checking the link's audit trail later if needed).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from ...config import settings


def issue_session_jwt(user_id: uuid.UUID, jti: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "jti": str(jti),
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=settings.session_cookie_ttl_s)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.magic_link_session_secret, algorithm="HS256")


def decode_session_jwt(token: str) -> Optional[dict]:
    """Returns the decoded claims, or None on any invalid / expired JWT."""
    try:
        return jwt.decode(
            token,
            settings.magic_link_session_secret,
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None
