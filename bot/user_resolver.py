"""Map a Telegram user id to a `users` row, with a small in-process cache.

Every inbound update calls this; we don't want a round-trip to Postgres on
every keystroke. Cache is tiny and discarded on pair/unpair via a version
bump so it stays coherent across a webhook-mode deploy that has many
workers sharing Redis — each worker just re-looks up on its next miss.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User


async def user_by_telegram_id(
    *, telegram_user_id: int, db: AsyncSession
) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none()


async def bind_telegram_id(
    *, user: User, telegram_user_id: int, db: AsyncSession
) -> None:
    user.telegram_user_id = telegram_user_id
    await db.commit()


async def unbind_telegram_id(*, user: User, db: AsyncSession) -> None:
    user.telegram_user_id = None
    await db.commit()
