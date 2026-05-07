"""Redis pub/sub channel between the OAuth callback and the bot handler.

When `GET /api/v1/gmail/oauth/callback` finishes (success or error), the
router publishes a JSON message to `gmail_callback:{user_id}`. The bot
handler (B5) listens on that channel for the user that just typed
`/conectar_gmail` and advances the onboarding state without polling.

We use pub/sub instead of a Redis list with a blocking pop because the
publisher fires once and the subscriber is always live during the
onboarding flow. Pub/sub is fire-and-forget: if the bot subscriber isn't
listening when the callback fires, the message is lost — and that's fine
because the next user message in the bot will refresh state from Redis
(`gmail_onboarding:{user_id}`) anyway. Pub/sub is the *fast* path; Redis
state is the *correct* path.
"""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis


_log = logging.getLogger("bot.gmail_pubsub")


def channel_for(user_id) -> str:
    return f"gmail_callback:{user_id}"


async def publish_callback(
    *, redis: Redis, user_id, status: str, detail: str | None = None
) -> int:
    """Notify subscribers (the bot) that the OAuth callback resolved.

    Returns the number of subscribers that received the message — 0 is
    not an error (see module docstring). status is one of
    {"success", "denied", "error"}; detail is a short string for logs.
    """
    payload = {"status": status}
    if detail:
        payload["detail"] = detail
    n = await redis.publish(channel_for(user_id), json.dumps(payload))
    _log.info(
        "gmail_callback published user=%s status=%s subscribers=%d",
        user_id,
        status,
        n,
    )
    return n
