"""User creation service (Phase 8 B1).

Extracted from `api/routers/users.py::register_user` so the Telegram
cold-start registration flow (`bot/registration.py`) creates users through
the SAME path as the REST endpoint — token minting, default notification
rule, and default categories stay in one place. The caller commits.
"""
from __future__ import annotations

import secrets
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.enums import NotificationScope
from api.models.notification_rule import NotificationRule
from api.models.user import User
from api.services.categories import ensure_default_categories

# 48 bytes → ~64 char URL-safe string. Spec asks for ≥32 bytes of entropy.
_TOKEN_BYTES = 48


def new_shortcut_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def create_user_with_defaults(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    phone_number: Optional[str] = None,
    country: str = "CR",
    timezone: str = "America/Costa_Rica",
    currency: str = "CRC",
    display_currency: Optional[str] = None,
    locale: str = "es-CR",
    telegram_user_id: Optional[int] = None,
) -> User:
    """Create a user + seed the global-default notification rule and the
    default categories. Flushes (so `user.id` is usable) but does NOT
    commit — the caller owns the transaction. `IntegrityError` (duplicate
    email) propagates for the caller to translate.
    """
    user = User(
        email=email.strip().lower(),
        full_name=full_name.strip(),
        phone_number=phone_number,
        country=country,
        timezone=timezone,
        currency=currency,
        display_currency=display_currency or currency[:3],
        locale=locale,
        shortcut_token=new_shortcut_token(),
        telegram_user_id=telegram_user_id,
    )
    db.add(user)
    await db.flush()
    db.add(
        NotificationRule(
            user_id=user.id,
            scope=NotificationScope.GLOBAL_DEFAULT.value,
            advance_days=[7, 3, 1, 0],
        )
    )
    await ensure_default_categories(db, user.id)
    return user
