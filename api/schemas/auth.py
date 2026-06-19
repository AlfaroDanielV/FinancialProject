import uuid

from pydantic import BaseModel, Field


class MagicLinkExchangeRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


class DeviceCodeExchangeRequest(BaseModel):
    """Body for `POST /api/v1/auth/device-code/exchange`.

    The code is the 6-character alphanumeric string the Telegram bot
    sent the user when they typed `/login`. Alphabet is uppercase
    A–Z + 2–9 minus ambiguous chars (0, O, 1, I, L). We accept any
    1–32 character input here and let the service-layer
    `normalize_code` + Redis lookup decide validity — this lets us
    return a generic 401 on bad codes without leaking the alphabet
    or length via Pydantic 422s.
    """

    code: str = Field(..., min_length=1, max_length=32)


class MagicLinkExchangeResponse(BaseModel):
    """Response shape for the magic-link / device-code exchange endpoints.

    Native clients (Expo) read `token` + `expires_at` from the body and
    send `Authorization: Bearer <token>` on subsequent calls; `user_id` /
    `email` / `full_name` identify the signed-in user.

    Phase 6f B16: the SPA `fa_session` cookie was removed; the bearer
    token in this body is now the only session credential issued per
    exchange (HS256, signed with `magic_link_session_secret`).
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    token: str = Field(
        ...,
        description=(
            "Session JWT (HS256). Native clients persist this in secure "
            "storage and send it as `Authorization: Bearer <token>`."
        ),
    )
    expires_at: int = Field(
        ...,
        description=(
            "Unix timestamp (seconds since epoch UTC) when the token "
            "expires."
        ),
    )
