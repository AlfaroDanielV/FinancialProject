import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
# Lightweight RFC-5321-ish email check; no MX validation. Avoids the
# email-validator dependency for now.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(v: str) -> str:
    v = v.strip().lower()
    if not _EMAIL.match(v) or len(v) > 320:
        raise ValueError("email is not a valid address")
    return v


def _validate_phone(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if not _E164.match(v):
        raise ValueError("phone_number must be E.164 (e.g. +50688887777)")
    return v


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=255)
    phone_number: Optional[str] = None
    country: str = Field(default="CR", min_length=2, max_length=2)
    timezone: str = Field(default="America/Costa_Rica", max_length=50)
    currency: str = Field(default="CRC", max_length=10)
    locale: str = Field(default="es-CR", max_length=10)

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone(v)

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return v.upper()

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _validate_email(v)


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    phone_number: Optional[str] = None
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    timezone: Optional[str] = Field(default=None, max_length=50)
    currency: Optional[str] = Field(default=None, max_length=10)
    locale: Optional[str] = Field(default=None, max_length=10)

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone(v)

    @field_validator("country")
    @classmethod
    def _country(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone_number: Optional[str]
    country: str
    timezone: str
    currency: str
    locale: str
    telegram_user_id: Optional[int]
    whatsapp_phone: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserRegisterResponse(UserResponse):
    """Returned exactly once at /register and /rotate-shortcut-token.

    The shortcut_token is never echoed elsewhere; clients must persist it.
    """

    shortcut_token: str


class ShortcutTokenResponse(BaseModel):
    shortcut_token: str
