"""Phase 6a — bloque 9.3: typing indicator refresh loop.

Verifies that `bot.handlers.typing_action` keeps Telegram's `typing`
chat-action alive while a long-running dispatch executes (CR-slang
queries can take 10–20s with 4 tool iterations + tools). The indicator
expires at 5s server-side, so the loop must refresh every 4s.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.handlers import TYPING_REFRESH_INTERVAL_S, typing_action


class _FakeBot:
    """Minimal Bot stub: counts send_chat_action calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.calls.append((chat_id, action))


@pytest.mark.asyncio
async def test_typing_action_refreshes_during_long_dispatch():
    """Simulate an 8s dispatch — TYPING_REFRESH_INTERVAL_S=4 means
    we expect the initial fire + at least one refresh = ≥2 calls.

    The exact count depends on event-loop timing; we assert ≥2 to keep
    the test robust against jitter.
    """
    bot = _FakeBot()
    chat_id = 12345

    async with typing_action(bot, chat_id):
        # Stand in for a slow dispatch.
        await asyncio.sleep(2 * TYPING_REFRESH_INTERVAL_S)

    assert len(bot.calls) >= 2, f"expected ≥2 send_chat_action, got {len(bot.calls)}"
    # All calls must be on the right chat with action=typing.
    for cid, action in bot.calls:
        assert cid == chat_id
        assert action == "typing"


@pytest.mark.asyncio
async def test_typing_action_fires_immediately_for_short_dispatch():
    """Even when the wrapped block returns instantly, the indicator
    fires at least once (the user sees feedback for the round-trip)."""
    bot = _FakeBot()

    async with typing_action(bot, 99):
        # No sleep — return immediately. We still want one call.
        # Yield once so the background task gets a chance to run.
        await asyncio.sleep(0.05)

    assert len(bot.calls) >= 1


@pytest.mark.asyncio
async def test_typing_action_swallows_send_errors():
    """Network blip on send_chat_action must not propagate to the
    caller — typing is best-effort UX, not correctness."""

    class _AngryBot:
        def __init__(self) -> None:
            self.calls = 0

        async def send_chat_action(self, **kwargs) -> None:
            self.calls += 1
            raise RuntimeError("telegram API blew up")

    bot = _AngryBot()
    # The context manager must not raise.
    async with typing_action(bot, 1):
        await asyncio.sleep(0.05)
    assert bot.calls >= 1


@pytest.mark.asyncio
async def test_typing_action_propagates_inner_exceptions():
    """If the wrapped block raises, the context manager re-raises after
    cancelling the typing task. We must not silently eat the error."""
    bot = _FakeBot()

    with pytest.raises(ValueError, match="boom"):
        async with typing_action(bot, 1):
            raise ValueError("boom")
