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

from bot.app import get_llm_client
from bot.pipeline import process_message


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


@router.post("/message", response_model=ChatMessageResponse)
async def post_chat_message(
    payload: ChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> ChatMessageResponse:
    redis = get_redis()
    reply = await process_message(
        user=user,
        text=payload.text,
        db=db,
        redis=redis,
        llm_client=get_llm_client(),
        llm_model=settings.llm_extraction_model,
    )
    return _build_response(reply)


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
    return _build_response(reply)
