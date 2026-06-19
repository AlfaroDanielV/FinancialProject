"""Phase 6d B3 — bot side of the magic-link flow.

`/setup` mints a fresh single-use magic link via
`api.services.auth.magic_link.generate_link` (no public mint endpoint —
Resolución 9.3). Each call mints a NEW link; old links are still
consumable until they expire or get used once.

Phase 6f B16: the SPA was retired, so `/setup` now sends a native deep
link (`ledgercr://exchange?token=...`) as tappable message text instead
of an https inline button (custom schemes can't be inline URL buttons).
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from api.database import AsyncSessionLocal

from .onboarding_welcome import build_setup_reply
from .user_resolver import user_by_telegram_id

router = Router(name="phase6d_onboarding")
log = logging.getLogger("bot.onboarding")


_PAIR_PROMPT = (
    "Primero pareá tu cuenta. Mandá /start con tu código de pareo."
)

@router.message(Command("setup"))
async def on_setup(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        user = await user_by_telegram_id(
            telegram_user_id=message.from_user.id, db=db
        )
        if user is None:
            await message.answer(_PAIR_PROMPT)
            return
        reply = await build_setup_reply(user=user, db=db)

    await message.answer(reply.text)
