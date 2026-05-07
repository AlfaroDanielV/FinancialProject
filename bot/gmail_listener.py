"""Background task that bridges the OAuth callback → Telegram chat.

The /api/v1/gmail/oauth/callback endpoint publishes JSON to
`gmail_callback:{user_id}` after success/failure. We psubscribe to the
pattern so a single connection serves all users.

On `success`:
    transition state awaiting_oauth → awaiting_sample,
    send GMAIL_CALLBACK_SUCCESS to the chat that started /conectar_gmail.

On `denied` / `error`:
    clear onboarding state, send the appropriate Spanish copy.

The listener is a best-effort UX helper. Correctness is owned by the
state machine: if the listener misses a message (Redis blip, restart),
the next user message in the bot still finds `awaiting_oauth` in Redis
and the bot can prompt the user to /conectar_gmail again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal
from api.redis_client import get_redis
from api.models.gmail_credential import GmailCredential
from sqlalchemy import select

from . import gmail_onboarding
from . import messages_es
from .app import get_bot


log = logging.getLogger("bot.gmail_listener")


_PATTERN = "gmail_callback:*"


# ── handler ──────────────────────────────────────────────────────────────────


async def _handle_message(channel: str, payload: str) -> None:
    """Process one pubsub message. channel = "gmail_callback:{user_id}"."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        log.warning("gmail_callback malformed payload: %r", payload)
        return

    status = data.get("status")
    if status not in {"success", "denied", "error"}:
        log.warning("gmail_callback unknown status=%s", status)
        return

    try:
        user_id_raw = channel.split(":", 1)[1]
        user_id = uuid.UUID(user_id_raw)
    except (ValueError, IndexError):
        log.warning("gmail_callback bad channel: %s", channel)
        return

    redis = get_redis()
    state = await gmail_onboarding.get(user_id, redis)
    if state is None:
        # No active onboarding for this user. The callback fired but the
        # user already cleared state (e.g. /desconectar_gmail mid-flow).
        # Nothing to push; drop silently.
        log.info("gmail_callback no onboarding state for user=%s", user_id)
        return

    bot = get_bot()
    chat_id = state.telegram_chat_id

    if status == "success":
        try:
            await gmail_onboarding.transition(
                user_id=user_id, to="selecting_banks", redis=redis
            )
        except Exception:
            log.exception("transition to selecting_banks failed")
        # Importing here (not at module top) avoids the circular reference
        # where gmail_handlers → app → handlers → gmail_handlers.
        try:
            from .gmail_handlers import send_bank_selection_prompt

            await send_bank_selection_prompt(
                bot=bot, chat_id=chat_id, user_id=user_id, redis=redis
            )
        except Exception:
            log.exception("send_bank_selection_prompt failed")
        return

    # denied / error path: clear state, tell the user.
    text = (
        messages_es.GMAIL_CALLBACK_DENIED
        if status == "denied"
        else messages_es.GMAIL_CALLBACK_ERROR
    )
    await gmail_onboarding.clear(user_id, redis)
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        log.exception("bot.send_message failed in callback (status=%s)", status)


# ── listener loop ────────────────────────────────────────────────────────────


_task: Optional[asyncio.Task] = None


async def _run_loop(redis: Redis) -> None:
    pubsub = redis.pubsub()
    await pubsub.psubscribe(_PATTERN)
    log.info("gmail_listener subscribed to pattern=%s", _PATTERN)
    try:
        async for raw in pubsub.listen():
            if raw is None:
                continue
            kind = raw.get("type")
            if kind != "pmessage":
                # subscribe / psubscribe / punsubscribe acks. Ignore.
                continue
            channel = raw.get("channel")
            data = raw.get("data")
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                await _handle_message(channel, data)
            except Exception:
                log.exception(
                    "gmail_listener handler crashed for channel=%s", channel
                )
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.punsubscribe(_PATTERN)
            await pubsub.aclose()
        except Exception:  # pragma: no cover
            pass


async def start() -> None:
    """Spawn the listener as a background task. Idempotent."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_run_loop(get_redis()))
    log.info("gmail_listener task started")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
