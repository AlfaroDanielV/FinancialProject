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
    """Response shape for `POST /api/v1/auth/magic-link/exchange`.

    The SPA reads `user_id` / `email` / `full_name` and relies on the
    `Set-Cookie: fa_session=…` side effect for auth.

    Phase 6f B2: native clients additionally read `token` + `expires_at`
    from the body and send `Authorization: Bearer <token>` on subsequent
    calls (they cannot share the HttpOnly cookie with their JS-side
    axios). The token is the same HS256 JWT the cookie carries, signed
    with the same `magic_link_session_secret`; there is exactly one
    session credential issued per exchange.
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    token: str = Field(
        ...,
        description=(
            "Session JWT (HS256). Same value as the fa_session cookie; "
            "native clients persist this in secure storage and send it as "
            "`Authorization: Bearer <token>`."
        ),
    )
    expires_at: int = Field(
        ...,
        description=(
            "Unix timestamp (seconds since epoch UTC) when the token "
            "expires."
        ),
    )
