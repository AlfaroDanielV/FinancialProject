"""Telegram send adapter for nudges.

The delivery worker in api/services/nudges/delivery.py doesn't know about
aiogram — it calls `send_fn(NudgeMessage) -> bool`. This module provides
the production implementation: turn a NudgeMessage into a Telegram send
with an inline keyboard.

callback_data convention: `nudge:<nudge_id>:<verb>`. The aiogram callback
handler (bot/pipeline.py in bloque 8) parses that and routes to the
dismiss/act endpoints.
"""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from api.services.nudges.delivery import NudgeMessage


log = logging.getLogger("bot.nudges_send")


def _callback_data(nudge_id, verb: str) -> str:
    return f"nudge:{nudge_id}:{verb}"


def _kb(message: NudgeMessage) -> InlineKeyboardMarkup | None:
    if not message.buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=b.label, callback_data=_callback_data(message.nudge_id, b.verb)
                )
            ]
            for b in message.buttons
        ]
    )


async def telegram_send_fn(message: NudgeMessage) -> bool:
    """Send a nudge via the aiogram Bot singleton. Returns True on success,
    False on any Telegram error (logged)."""
    from .app import get_bot  # local import avoids circular at module load

    try:
        bot = get_bot()
    except RuntimeError as e:
        # Bot not initialized (TELEGRAM_MODE=disabled). Treat as failure so
        # delivery counts it and keeps the nudge pending for next run.
        log.warning("telegram_send_fn: bot not initialized (%s)", e)
        return False

    try:
        await bot.send_message(
            chat_id=message.chat_id,
            text=message.text,
            reply_markup=_kb(message),
        )
        return True
    except TelegramAPIError as e:
        log.warning(
            "telegram_send_fn: API error chat=%s nudge=%s err=%s",
            message.chat_id,
            message.nudge_id,
            e,
        )
        return False
