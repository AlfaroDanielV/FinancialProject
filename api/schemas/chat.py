"""Phase 6f B2 — schemas for the native in-app chat endpoint.

The chat endpoint is a thin HTTP wrapper around `bot/pipeline.py::process_message()`.
Request shape: just the user's text. Response shape: a serialized `BotReply`.

The mobile app renders `reply_text` as a message bubble and, if `buttons`
or `url_buttons` are present, surfaces them as native action sheets / nav
buttons. Button shape mirrors the Telegram inline-keyboard contract so
the underlying Redis state machine (`telegram:pending:{user_id}` etc.)
works without any per-channel branching.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatButton(BaseModel):
    """A confirm/cancel/edit button. `callback_data` echoes the same
    short-id encoding the Telegram path uses (`pending:{short_id}:yes`).
    The native app posts the same text back through `/chat/message` when
    the user taps it (the pipeline treats button taps as text replies
    of "Sí" / "No" / "Editar", so the encoded short-id is informational
    only — the pipeline matches against the latest pending action)."""

    label: str
    callback_data: str


class ChatUrlButton(BaseModel):
    """External https URL button — opens in `expo-web-browser` on native.

    The SPA edit-session deep links that originally populated this (Phase
    6e B12) were removed when the SPA was retired at 6f B16; the field
    stays as an extension point for future https deep links."""

    label: str
    url: str


class ChatOpenScreen(BaseModel):
    """Phase 6f debt slice — a directive for the native client to open a
    screen pre-filled with `prefill`. Used by the chat→form handoff for debt
    creation: the chat does light extraction, then the app opens
    `DebtCreateScreen` with the extracted fields so the user completes the
    amortization params and submits to `POST /debts`. The SPA / Telegram
    ignore this field — it is native-only."""

    screen: str
    prefill: dict[str, Any] = Field(default_factory=dict)


class ChatMessageRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description=(
            "User-supplied chat text. Same upper bound as Telegram (4096 chars)."
        ),
    )


class ChatMessageResponse(BaseModel):
    """Serialized `BotReply` from the bot pipeline.

    `reply_text` is plain Spanish text or HTML-tagged text (the same the
    bot would send via `ParseMode.HTML`). Native clients should render
    it as plain text — the HTML tagging is Telegram-specific styling
    that doesn't carry semantics the mobile UI needs. A later block may
    introduce a format-neutral intermediate if rich text matters.
    """

    reply_text: str
    buttons: list[ChatButton] = Field(default_factory=list)
    url_buttons: list[ChatUrlButton] = Field(default_factory=list)
    open_screen: Optional[ChatOpenScreen] = None


# ChatImageResponse is intentionally the same shape as ChatMessageResponse —
# the pipeline returns a BotReply regardless of whether the input was text
# or an image. Keeping it a separate class lets us evolve the schema
# independently if the image path needs extra fields later.
ChatImageResponse = ChatMessageResponse
