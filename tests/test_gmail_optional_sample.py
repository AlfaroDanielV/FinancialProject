"""Tests for the /agregar_muestra optional-sample flow (Block D.2).

Targets the helper logic + persistence; we don't drive aiogram. The
handlers are thin glue around `_persist_optional_sample` and the Redis
state key — those are what we cover here.
"""
from __future__ import annotations

import socket
import time
import uuid
from urllib.parse import urlparse

import pytest

from api.config import settings
from api.models.bank_notification_sample import BankNotificationSample
from bot import gmail_handlers
from bot.redis_keys import (
    GMAIL_OPTIONAL_SAMPLE_TTL_S,
    gmail_optional_sample_key,
)


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


# ── stub redis (separate from the bot/listener stub since we share API) ────


class StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}

    async def set(self, key, value, ex=None, **kwargs):
        exp = (time.time() + ex) if ex else None
        self.store[key] = (value, exp)
        return True

    async def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        return entry[0]

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return len(keys)


# ── stub sample analyzer client ────────────────────────────────────────────


class StubAnalyzer:
    """Mimics the SampleAnalyzerClient protocol for tests."""

    def __init__(self, *, raw="ocr-text", bank=None, sender=None, conf=0.8):
        self.raw_to_return = raw
        self.bank = bank
        self.sender = sender
        self.conf = conf

    async def extract_text_from_image(self, image_bytes, *, mime_type="image/jpeg"):
        return self.raw_to_return

    async def analyze_text(self, raw_text):
        from api.services.gmail.sample_analyzer import SampleAnalysis

        return SampleAnalysis(
            raw_text=raw_text,
            sender_email=self.sender,
            bank_name=self.bank,
            format_signature={"k": "v"},
            confidence=self.conf,
        )


# ── _persist_optional_sample ───────────────────────────────────────────────


async def test_persist_optional_sample_text_writes_row(db_with_user, monkeypatch):
    db, user_id = db_with_user
    monkeypatch.setattr(
        gmail_handlers,
        "get_sample_analyzer",
        lambda: StubAnalyzer(bank="BAC", sender="x@bac.cr", conf=0.91),
    )
    bank, sender = await gmail_handlers._persist_optional_sample(
        user_id=user_id,
        raw_text="payload del correo",
        source="text",
        db=db,
    )
    assert bank == "BAC"
    assert sender == "x@bac.cr"

    from sqlalchemy import select

    rows = (
        await db.execute(
            select(BankNotificationSample).where(
                BankNotificationSample.user_id == user_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.detected_bank == "BAC"
    assert row.detected_sender == "x@bac.cr"
    assert row.source == "text"
    assert row.raw_text == "payload del correo"


async def test_persist_optional_sample_low_confidence_still_saved(
    db_with_user, monkeypatch
):
    """Optional samples save regardless of confidence — they're for
    extractor calibration, not for activation."""
    db, user_id = db_with_user
    monkeypatch.setattr(
        gmail_handlers,
        "get_sample_analyzer",
        lambda: StubAnalyzer(bank=None, sender=None, conf=0.2),
    )
    bank, sender = await gmail_handlers._persist_optional_sample(
        user_id=user_id, raw_text="weird email body", source="text", db=db
    )
    assert bank is None
    assert sender is None

    from sqlalchemy import select

    row = (
        await db.execute(
            select(BankNotificationSample).where(
                BankNotificationSample.user_id == user_id
            )
        )
    ).scalar_one()
    assert row.detected_bank is None
    assert float(row.confidence or 0) < 0.6


# ── _is_awaiting_optional_sample filter (cheap) ────────────────────────────


async def test_filter_returns_false_without_redis_key(monkeypatch):
    """A normal text message should NOT match the filter unless the
    user ran /agregar_muestra recently. After the telegram_user_id
    refactor, this is a pure Redis check — no DB needed."""
    from aiogram.types import Chat, Message
    from aiogram.types import User as TgUser

    redis = StubRedis()
    monkeypatch.setattr(gmail_handlers, "get_redis", lambda: redis)

    msg = Message(
        message_id=1,
        date=__import__("datetime").datetime.now(),
        chat=Chat(id=99_001, type="private"),
        from_user=TgUser(id=99_001, is_bot=False, first_name="Test"),
        text="some text",
    )
    out = await gmail_handlers._is_awaiting_optional_sample(msg)
    assert out is False


async def test_filter_returns_true_when_state_set(monkeypatch):
    from aiogram.types import Chat, Message
    from aiogram.types import User as TgUser

    redis = StubRedis()
    # Key is indexed by telegram_user_id (NOT user_id) per the refactor.
    await redis.set(gmail_optional_sample_key(99_002), "1", ex=600)
    monkeypatch.setattr(gmail_handlers, "get_redis", lambda: redis)

    msg = Message(
        message_id=1,
        date=__import__("datetime").datetime.now(),
        chat=Chat(id=99_002, type="private"),
        from_user=TgUser(id=99_002, is_bot=False, first_name="Test"),
        text="anything",
    )
    out = await gmail_handlers._is_awaiting_optional_sample(msg)
    assert out is True


# ── Redis key contract (matches what redis_keys exports) ───────────────────


def test_redis_key_format():
    """Indexed by telegram_user_id (BIGINT) post-refactor, not user.id."""
    assert gmail_optional_sample_key(99_001) == "gmail_optional_sample:tg:99001"


def test_ttl_is_ten_minutes():
    assert GMAIL_OPTIONAL_SAMPLE_TTL_S == 600
