import uuid

from pydantic import BaseModel, Field


class MagicLinkExchangeRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


class MagicLinkExchangeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
