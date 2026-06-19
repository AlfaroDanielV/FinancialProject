"""Phase 6f B2 — native in-app chat endpoint.

`POST /api/v1/chat/message` is the entry point the Expo app uses to
talk to the same bot pipeline the Telegram handler drives. The endpoint
is intentionally a thin wrapper: it resolves the caller via
`current_user` (bearer JWT in production, cookie in the SPA-cutover
window, `X-Shortcut-Token` for the iPhone Shortcut path), then hands
control to `bot/pipeline.py::process_message()` and serializes the
returned `BotReply` to JSON.

Side effects (Redis state writes, DB commits, pending-confirmation
rows, insight enqueue, undo bookkeeping) all happen inside the
pipeline — the router never duplicates them. The Telegram bot and the
native chat surface share one source of truth for conversation state.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import current_user
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.chat import (
    ChatButton,
    ChatImageResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatOpenScreen,
    ChatUrlButton,
)

from app.queries.history import clear_history
from bot.account_creation import clear_account_creation
from bot.app import get_llm_client
from bot.clarification import clear_clarification
from bot.pending import clear_pending, load_pending
from bot.pending_db import resolve_from_pending
from bot.pipeline import process_message
from bot import messages_es


log = logging.getLogger("api.routers.chat")

_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB pre-base64

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _build_response(reply) -> ChatMessageResponse:
    open_screen = None
    if reply.open_screen is not None:
        open_screen = ChatOpenScreen(
            screen=reply.open_screen.screen,
            prefill=reply.open_screen.prefill,
        )
    return ChatMessageResponse(
        reply_text=reply.text,
        buttons=[
            ChatButton(label=b.label, callback_data=b.callback_data)
            for b in reply.buttons
        ],
        url_buttons=[
            ChatUrlButton(label=u.label, url=u.url)
            for u in reply.url_buttons
        ],
        open_screen=open_screen,
    )


def _chat_error_response() -> ChatMessageResponse:
    """Last-resort guard for the native chat endpoints.

    `process_message` is expected to handle its own failures and return a
    `BotReply` with friendly Spanish copy. But the native chat endpoint is the
    ONLY surface that turns an uncaught pipeline exception into a user-visible
    HTTP 500 (the app renders it as a generic "Hubo un error"); Telegram tolerates
    the same throw. So we wrap process_message and, on anything unexpected, return
    this handled response instead of letting the 500 escape.
    """
    return ChatMessageResponse(reply_text=messages_es.CHAT_UNEXPECTED_ERROR)


@router.post("/message", response_model=ChatMessageResponse)
async def post_chat_message(
    payload: ChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> ChatMessageResponse:
    redis = get_redis()
    try:
        reply = await process_message(
            user=user,
            text=payload.text,
            db=db,
            redis=redis,
            llm_client=get_llm_client(),
            llm_model=settings.llm_extraction_model,
        )
    except HTTPException:
        raise
    except Exception:
        log.exception("chat_message_unhandled user_id=%s", user.id)
        return _chat_error_response()
    return _build_response(reply)


@router.post("/reset")
async def reset_chat(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Start a new conversation — clear all durable conversational state for the
    caller: pending write, clarification, the account-creation flow, the
    memory-edit flow, and the LLM query history. Mirrors the bot's `/cancel`
    (write/flow state) + `/clear` (query history). The visible message list is
    client-local; the native app clears it and calls this to reset the server
    side, so a stuck flow (e.g. a stale account prompt) can't leak across.
    """
    redis = get_redis()
    existing = await load_pending(user_id=user.id, redis=redis)
    if existing is not None:
        # Close the Phase 5d audit row before dropping the Redis key.
        await resolve_from_pending(
            session=db, pending=existing, resolution="cancelled"
        )
        await db.commit()
    await clear_pending(user_id=user.id, redis=redis)
    await clear_clarification(user_id=user.id, redis=redis)
    await clear_account_creation(user_id=user.id, redis=redis)
    # Imported lazily, matching the bot's /cancel handler (avoids a heavy import
    # at module load for a rarely-hit path).
    from bot.memory_handlers import clear_memory_edit_state

    await clear_memory_edit_state(user_id=user.id, redis=redis)
    await clear_history(user.id, redis=redis)
    return {"reset": True}


@router.post("/image", response_model=ChatImageResponse)
async def post_chat_image(
    file: UploadFile = File(..., description="Receipt photo (JPEG, PNG, WebP, or GIF)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> ChatImageResponse:
    """Phase 6f B6 — receipt photo upload.

    Accepts a multipart image, runs Haiku vision extraction (Sonnet retry
    on low confidence), and routes the result through the deterministic
    write dispatcher. Same `BotReply` shape as `/chat/message`.
    """
    media_type = file.content_type or ""
    if media_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type '{media_type}'. "
                   f"Allowed: {sorted(_ALLOWED_MIME_TYPES)}",
        )

    image_bytes = await file.read()
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds {_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.",
        )

    redis = get_redis()
    try:
        reply = await process_message(
            user=user,
            text="",
            db=db,
            redis=redis,
            llm_client=get_llm_client(),
            llm_model=settings.llm_extraction_model,
            image_bytes=image_bytes,
            image_media_type=media_type,
            vision_model=settings.llm_query_model,
        )
    except HTTPException:
        raise
    except Exception:
        log.exception("chat_image_unhandled user_id=%s", user.id)
        return _chat_error_response()
    return _build_response(reply)
