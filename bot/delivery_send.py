"""Phase 6a — bloque 9: Telegram delivery wiring.

Two entry points, both built on `app.queries.delivery`:

- `render_chunks(text)` — pure: sanitize_telegram_html → split_for_telegram.
  Returns the list a Telegram client would send. Used by the
  `/api/v1/queries/test` endpoint so callers can inspect chunking
  without driving aiogram.

- `send_chunked(message, text, *, reply_markup=None)` — sends each chunk
  sequentially via `message.answer`. The optional inline keyboard is
  attached only to the last chunk (so taps land on the most recent
  visible message). Sends are awaited in order — never `gather` — so
  Telegram preserves message order.

Why is this its own module? The bot handler and the test endpoint
must NOT diverge on how a query response gets formatted before going
out. Sharing the helper guarantees a fix to one fixes the other.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, Message

from app.queries.delivery import sanitize_telegram_html, split_for_telegram

log = logging.getLogger("bot.delivery_send")


def render_chunks(text: str) -> list[str]:
    """Sanitize HTML, split for Telegram, return chunk list.

    Pure function — no I/O. Empty input yields a single empty chunk
    (mirrors `split_for_telegram` contract).
    """
    sanitized = sanitize_telegram_html(text)
    return split_for_telegram(sanitized)


async def send_chunked(
    message: Message,
    text: str,
    *,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """Send `text` as one or more Telegram messages.

    The message is sanitized for Telegram's HTML subset and split with
    the paragraph-aware splitter. Chunks are sent sequentially via
    `message.answer` so Telegram preserves order.

    `reply_markup` (when provided) attaches only to the LAST chunk —
    keeping inline keyboards near the most recent visible message.
    """
    chunks = render_chunks(text)
    if not chunks:
        return
    last_idx = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        kb = reply_markup if i == last_idx else None
        await message.answer(chunk, parse_mode=ParseMode.HTML, reply_markup=kb)
