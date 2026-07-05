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

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
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
from app.queries.session import AsyncSessionLocal
from app.queries.stream_events import Delta, StreamEvent, ToolBatch
from bot.account_creation import clear_account_creation
from bot.advisory import (
    end_advisory_session,
    is_advisory_session_active,
    start_advisory_session,
)
from bot.app import get_llm_client
from bot.clarification import clear_clarification
from bot.pending import clear_pending, load_pending
from bot.pending_db import resolve_from_pending
from bot.pipeline import process_message
from bot import messages_es


log = logging.getLogger("api.routers.chat")

_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB pre-base64

# P10.S streaming (SSE) tunables.
_HEARTBEAT_S = 10.0  # ': ka' comment cadence while the queue is idle (keeps the
#                      ingress connection non-idle during the tool phase)
_TURN_DEADLINE_S = 170.0  # whole-turn hard ceiling inside the runner (< the
#                           client's ~210s abort cap; > the per-call 90s stream)
# Strong refs to in-flight runner tasks. A runner is NEVER cancelled on client
# disconnect (a write turn must finish exactly once), so it can outlive the
# response generator — hold a ref here so asyncio doesn't GC it mid-flight.
_RUNNERS: set[asyncio.Task] = set()

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
        error_class=getattr(reply, "error_class", None),
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
    return ChatMessageResponse(
        reply_text=messages_es.CHAT_UNEXPECTED_ERROR, error_class="system"
    )


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
        # P10 B0.5 (R1): serialization happens INSIDE the guard — a BotReply
        # that fails to serialize must degrade to the handled 200 body, not
        # escape as a 500 the client can only show as a generic banner.
        return _build_response(reply)
    except HTTPException:
        raise
    except Exception:
        log.exception("chat_message_unhandled user_id=%s", user.id)
        return _chat_error_response()


def _chat_transient_response() -> ChatMessageResponse:
    """Terminal frame for the streaming whole-turn deadline — retryable, so the
    client shows 'transient' copy (not the generic 'system' banner)."""
    return ChatMessageResponse(
        reply_text=messages_es.CHAT_UNEXPECTED_ERROR, error_class="transient"
    )


def _sse(event: str, data: str) -> str:
    """One SSE frame: an `event:`/`data:` pair terminated by a blank line."""
    return f"event: {event}\ndata: {data}\n\n"


@router.post("/message/stream")
async def post_chat_message_stream(
    payload: ChatMessageRequest,
    user: User = Depends(current_user),
) -> StreamingResponse:
    """P10.S — token-streaming variant of POST /chat/message (SSE).

    Streams the QUERY path's LLM text as `event: delta` frames, a neutral
    `event: status` while tools run, and exactly ONE terminal `event: final`
    carrying the full ChatMessageResponse (buttons / open_screen / error_class).
    Deterministic turns (writes, confirmations, commands) emit no deltas — just
    the terminal `final`. Telegram + the classic /chat/message are untouched.

    Contract (hardened): the pipeline runs in a runner task that owns its OWN DB
    session (the request-scoped Depends(get_db) session is torn down before this
    body iterates) and is NEVER cancelled on client disconnect (a write turn
    must commit exactly once). Behind `chat_streaming_enabled` — 404 when off so
    the client falls back to the blocking endpoint.
    """
    if not settings.chat_streaming_enabled:
        # Kill switch / staged rollout. A 404 (capability missing) is the ONLY
        # signal the client silently falls back on — distinct from a 5xx or a
        # network error, so a disabled flag never risks a double-executed write.
        raise HTTPException(status_code=404, detail="chat streaming disabled")

    user_id = user.id  # captured now; the runner re-fetches in its own session
    text = payload.text
    redis = get_redis()
    queue: asyncio.Queue = asyncio.Queue()  # unbounded → put_nowait never raises
    final_future: asyncio.Future = asyncio.get_running_loop().create_future()
    sentinel = object()

    async def on_event(ev: StreamEvent) -> None:
        # Best-effort: an emit must NEVER raise back into the LLM loop.
        try:
            queue.put_nowait(ev)
        except Exception:  # pragma: no cover - unbounded queue can't fill
            log.exception("chat_stream_emit_failed user_id=%s", user_id)

    async def runner() -> None:
        final: ChatMessageResponse | None = None
        try:
            # Own session (blocker #1): Depends(get_db) is closed before the
            # StreamingResponse body iterates. Re-fetch the user so it's bound
            # to THIS session, mirroring the non-stream endpoint's contract.
            async with AsyncSessionLocal() as db:
                user_row = await db.get(User, user_id)
                if user_row is None:
                    final = _chat_error_response()
                else:
                    async with asyncio.timeout(_TURN_DEADLINE_S):
                        reply = await process_message(
                            user=user_row,
                            text=text,
                            db=db,
                            redis=redis,
                            llm_client=get_llm_client(),
                            llm_model=settings.llm_extraction_model,
                            on_event=on_event,
                        )
                    final = _build_response(reply)
        except (asyncio.TimeoutError, TimeoutError):
            log.warning("chat_stream_turn_timeout user_id=%s", user_id)
            final = _chat_transient_response()
        except Exception:
            log.exception("chat_stream_unhandled user_id=%s", user_id)
            final = _chat_error_response()
        finally:
            if not final_future.done():
                final_future.set_result(final or _chat_error_response())
            # Wake the generator immediately (don't wait a heartbeat tick).
            queue.put_nowait(sentinel)

    def _runner_done(task: asyncio.Task) -> None:
        _RUNNERS.discard(task)
        # Retrieve any exception so asyncio doesn't log 'never retrieved'. The
        # runner catches Exception internally, so this is defensive only.
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:  # pragma: no cover
                log.error("chat_stream_runner_crashed user_id=%s: %r", user_id, exc)

    async def event_stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        deltas = 0
        first_token = False
        # Fast first byte + a 'started' marker. Any bytes reaching the client
        # mean the server received the request and the turn is running — the
        # client must NOT auto-retry (would double-execute a write).
        yield ": connected\n\n"
        yield _sse("started", "{}")
        log.info("chat_stream_start user_id=%s", user_id)
        task = asyncio.create_task(runner())
        _RUNNERS.add(task)
        task.add_done_callback(_runner_done)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": ka\n\n"  # heartbeat comment; resets the client stall timer
                    continue
                if ev is sentinel:
                    break
                if isinstance(ev, Delta):
                    if not first_token:
                        first_token = True
                        log.info(
                            "chat_stream_first_token user_id=%s ttfb_ms=%d",
                            user_id,
                            int((time.perf_counter() - started) * 1000),
                        )
                    deltas += 1
                    yield _sse("delta", json.dumps({"text": ev.text}))
                elif isinstance(ev, ToolBatch):
                    log.info(
                        "chat_stream_tool_phase user_id=%s tools=%s",
                        user_id,
                        ",".join(ev.tool_names),
                    )
                    yield _sse(
                        "status",
                        json.dumps({"label": messages_es.STREAM_STATUS_TOOLS}),
                    )
            final = (
                final_future.result()
                if final_future.done()
                else _chat_error_response()
            )
            yield _sse("final", final.model_dump_json())
            log.info(
                "chat_stream_final user_id=%s duration_ms=%d deltas=%d error_class=%s",
                user_id,
                int((time.perf_counter() - started) * 1000),
                deltas,
                getattr(final, "error_class", None),
            )
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected. Do NOT cancel the runner (blocker #2): a write
            # turn must finish exactly once. It runs to completion under its own
            # 170s deadline, closes its own session, and de-registers via the
            # done-callback.
            log.info("chat_stream_client_disconnect user_id=%s", user_id)
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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
    try:
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
        # Imported lazily, matching the bot's /cancel handler (avoids a heavy
        # import at module load for a rarely-hit path).
        from bot.memory_handlers import clear_memory_edit_state

        await clear_memory_edit_state(user_id=user.id, redis=redis)
        await clear_history(user.id, redis=redis)
        # P10 B2: a fresh conversation also leaves the advisory session.
        await end_advisory_session(user_id=user.id, redis=redis)
    except HTTPException:
        raise
    except Exception:
        # P10 B0.5: same no-500 contract as /message — a partial reset is
        # reported honestly so the client can retry instead of showing the
        # generic network banner.
        log.exception("chat_reset_unhandled user_id=%s", user.id)
        return {"reset": False}
    return {"reset": True}


@router.get("/advisory-session")
async def get_advisory_session(
    user: User = Depends(current_user),
) -> dict:
    """P10 B2 — session state for the native 'Modo asesor' header control."""
    redis = get_redis()
    active = await is_advisory_session_active(user_id=user.id, redis=redis)
    return {"active": active, "enabled": settings.advisory_persona_enabled}


@router.post("/advisory-session")
async def set_advisory_session(
    payload: dict,
    user: User = Depends(current_user),
) -> dict:
    """P10 B2 — start/end the advisory continuity session from the native app.

    Body: {"active": bool}. Mirrors /asesor · /normal in the chat. When the
    feature flag is off the session never starts (the control shows the
    unavailable copy client-side via `enabled`).
    """
    active = bool(payload.get("active"))
    redis = get_redis()
    if active:
        if not settings.advisory_persona_enabled:
            return {"active": False, "enabled": False}
        await start_advisory_session(user_id=user.id, redis=redis)
    else:
        await end_advisory_session(user_id=user.id, redis=redis)
    return {"active": active, "enabled": settings.advisory_persona_enabled}


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
        # P10 B0.5 (R1): serialize inside the guard (see post_chat_message).
        return _build_response(reply)
    except HTTPException:
        raise
    except Exception:
        log.exception("chat_image_unhandled user_id=%s", user.id)
        return _chat_error_response()
