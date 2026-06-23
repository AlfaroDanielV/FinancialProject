"""Tests for the send-only bot initializer used by the daily Gmail worker.

The cron worker (`workers/gmail_daily.py`) runs in its own process with no
FastAPI lifespan, so without an explicit init the notifier's `get_bot()` raises
`RuntimeError` and every Telegram message is dropped. `start_bot_send_only()`
populates the Bot singleton for outbound-only use. These tests pin its contract:
idempotent, graceful no-op without a token, no webhook/polling, clean teardown.

No DB or network — pure singleton lifecycle.
"""
from __future__ import annotations

import pytest

from api.config import settings
from bot import app as bot_app


# A format-valid (but fake) Telegram token: aiogram validates `int:str`.
_FAKE_TOKEN = "123456:AAHfake_test_token_for_send_only-001"


@pytest.fixture(autouse=True)
async def _clean_bot_state():
    """Ensure each test starts and ends with an empty Bot singleton so a real
    Bot session from one test can't leak into another."""
    await bot_app.stop_bot()
    yield
    await bot_app.stop_bot()


@pytest.fixture
def _token_configured(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", _FAKE_TOKEN)
    # webhook is the production worker mode — the send-only path must NOT
    # register a webhook regardless.
    monkeypatch.setattr(settings, "telegram_mode", "webhook")


async def test_send_only_populates_singleton(_token_configured):
    # Before init, get_bot() raises.
    with pytest.raises(RuntimeError):
        bot_app.get_bot()

    await bot_app.start_bot_send_only()

    bot = bot_app.get_bot()
    assert bot is not None
    # Send-only: no dispatcher, no polling task, no webhook registration.
    assert bot_app._state.dp is None
    assert bot_app._state.polling_task is None


async def test_send_only_is_idempotent(_token_configured):
    await bot_app.start_bot_send_only()
    first = bot_app.get_bot()
    await bot_app.start_bot_send_only()
    assert bot_app.get_bot() is first  # second call is a no-op


async def test_stop_bot_clears_singleton(_token_configured):
    await bot_app.start_bot_send_only()
    await bot_app.stop_bot()
    with pytest.raises(RuntimeError):
        bot_app.get_bot()


async def test_no_token_is_graceful_noop(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_mode", "webhook")

    # Must not raise — the scan has to run even when notifications can't.
    await bot_app.start_bot_send_only()

    with pytest.raises(RuntimeError):
        bot_app.get_bot()


async def test_disabled_mode_is_graceful_noop(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", _FAKE_TOKEN)
    monkeypatch.setattr(settings, "telegram_mode", "disabled")

    await bot_app.start_bot_send_only()

    with pytest.raises(RuntimeError):
        bot_app.get_bot()
