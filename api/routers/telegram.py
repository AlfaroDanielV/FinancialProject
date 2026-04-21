"""Telegram endpoints (Phase 5b).

Routes:
  POST /api/v1/users/me/telegram/pairing-code — auth via current_user
  POST /api/v1/users/me/telegram/unpair       — auth via current_user
  POST /api/v1/telegram/webhook               — validates Telegram's secret header
  POST /api/v1/telegram/_simulate             — dev-only pipeline driver
"""
from __future__ import annotations

import json
import logging

from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import AsyncSessionLocal, get_db
from ..dependencies import current_user
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.telegram import (
    PairingCodeResponse,
    SimulateRequest,
    SimulateResponse,
)

from bot import messages_es
from bot.app import get_llm_client, get_bot, get_dispatcher
from bot.pairing import (
    consume_pairing_code,
    issue_pairing_code,
    resolve_pairing_code,
)
from bot.pipeline import (
    BotReply,
    handle_pending_callback,
    process_message,
    process_mock_extraction,
)
from bot.redis_keys import PAIRING_TTL_S
from bot.user_resolver import (
    bind_telegram_id,
    unbind_telegram_id,
    user_by_telegram_id,
)


log = logging.getLogger("api.routers.telegram")


# Two routers: user-scoped pairing endpoints and the bot-scoped webhook.
users_tg_router = APIRouter(
    prefix="/api/v1/users/me/telegram", tags=["telegram"]
)
telegram_router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


# ── pairing code (user-scoped) ────────────────────────────────────────────────


@users_tg_router.post("/pairing-code", response_model=PairingCodeResponse)
async def create_pairing_code(
    user: User = Depends(current_user),
):
    redis = get_redis()
    code = await issue_pairing_code(user=user, redis=redis)
    return PairingCodeResponse(code=code, expires_in_seconds=PAIRING_TTL_S)


@users_tg_router.post("/unpair", status_code=204)
async def unpair(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.telegram_user_id is None:
        return
    await unbind_telegram_id(user=user, db=db)


# ── webhook (Telegram-scoped) ─────────────────────────────────────────────────


@telegram_router.post("/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Receive an Update from Telegram and feed it into aiogram.

    Secret token validation is per Telegram's docs: we registered a secret
    when calling setWebhook; every legitimate request echoes it back in
    this header. Mismatch → reject quietly (don't leak that we run a bot).
    """
    if settings.telegram_mode.lower() != "webhook":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="webhook mode disabled"
        )
    if (
        not settings.telegram_webhook_secret
        or x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        # Telegram will retry on non-2xx, but it will NOT retry indefinitely
        # against a 401/403 — which is what we want for spoofed calls.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="bad secret"
        )

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    bot = get_bot()
    dp = get_dispatcher()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return {"ok": True}


# ── _simulate (dev only) ──────────────────────────────────────────────────────


@telegram_router.post("/_simulate", response_model=SimulateResponse)
async def telegram_simulate(
    payload: SimulateRequest,
):
    """Drive `bot.pipeline` directly, without Telegram or aiogram.

    Rejected in non-development environments. The Phase 5b smoke script
    uses this to exercise the full flow (extractor → dispatcher → commit
    → Redis) from curl. NEVER expose this in production — it bypasses
    Telegram auth by accepting any `telegram_user_id` the caller supplies.
    """
    if not settings.is_dev:
        raise HTTPException(status_code=404, detail="not found")

    async with AsyncSessionLocal() as db:
        redis = get_redis()

        # Pairing flow — mirrors bot.handlers.on_start but for the simulator.
        if payload.pairing_code is not None:
            code = payload.pairing_code.strip().upper()
            candidate = await resolve_pairing_code(
                code=code, redis=redis, db=db
            )
            if candidate is None:
                return SimulateResponse(text=messages_es.PAIR_BAD_CODE)
            existing = await user_by_telegram_id(
                telegram_user_id=payload.telegram_user_id, db=db
            )
            if existing is not None and existing.id != candidate.id:
                return SimulateResponse(text=messages_es.PAIR_TG_ACCOUNT_TAKEN)
            if (
                candidate.telegram_user_id is not None
                and candidate.telegram_user_id != payload.telegram_user_id
            ):
                return SimulateResponse(text=messages_es.PAIR_USER_ALREADY_PAIRED)
            await bind_telegram_id(
                user=candidate,
                telegram_user_id=payload.telegram_user_id,
                db=db,
            )
            await consume_pairing_code(code=code, redis=redis)
            return SimulateResponse(
                text=messages_es.PAIR_SUCCESS.format(
                    name=payload.first_name or ""
                )
            )

        user = await user_by_telegram_id(
            telegram_user_id=payload.telegram_user_id, db=db
        )
        if user is None:
            return SimulateResponse(text=messages_es.PAIR_PROMPT)

        if payload.callback_data:
            reply: BotReply = await handle_pending_callback(
                user=user,
                callback_data=payload.callback_data,
                db=db,
                redis=redis,
            )
        elif payload.mock_extraction is not None:
            reply = await process_mock_extraction(
                user=user,
                raw_extraction=payload.mock_extraction,
                db=db,
                redis=redis,
            )
        else:
            reply = await process_message(
                user=user,
                text=payload.text,
                db=db,
                redis=redis,
                llm_client=get_llm_client(),
                llm_model=settings.llm_extraction_model,
            )

    return SimulateResponse(
        text=reply.text,
        buttons=[
            {"label": b.label, "callback_data": b.callback_data}
            for b in reply.buttons
        ],
    )
