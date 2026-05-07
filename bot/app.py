"""aiogram Bot + Dispatcher factory and lifecycle helpers.

Separated from handlers so the webhook route and the polling task can both
get at the shared singleton without circular imports. The LLMClient lives
here too — there's only one per process and it owns the Anthropic SDK
client, which in turn owns an httpx connection pool.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from api.config import settings
from api.services.llm_extractor import AnthropicLLMClient, LLMClient


log = logging.getLogger("bot.app")


class _BotSingleton:
    bot: Optional[Bot] = None
    dp: Optional[Dispatcher] = None
    llm_client: Optional[LLMClient] = None
    polling_task: Optional[asyncio.Task] = None


_state = _BotSingleton()


def get_bot() -> Bot:
    if _state.bot is None:
        raise RuntimeError("Telegram bot not initialized; call start_bot() first.")
    return _state.bot


def get_dispatcher() -> Dispatcher:
    if _state.dp is None:
        raise RuntimeError("Telegram dispatcher not initialized.")
    return _state.dp


def get_llm_client() -> LLMClient:
    """The extractor's LLM client. Always an AnthropicLLMClient in prod;
    tests swap it via `set_llm_client`."""
    if _state.llm_client is None:
        _state.llm_client = AnthropicLLMClient(api_key=settings.anthropic_api_key)
    return _state.llm_client


def set_llm_client(client: LLMClient) -> None:
    """Test hook. Never call this at runtime."""
    _state.llm_client = client


async def start_bot() -> None:
    """Initialize the Bot and Dispatcher, attach handlers, and launch
    whichever mode is configured. Idempotent — second call is a no-op."""
    if _state.bot is not None:
        return

    # Phase 6b warning: `env` SecretStore loses refresh tokens on restart.
    # Loud INFO at every boot so the dev knows what to expect — was a
    # debugging trap during the Block B smoke.
    if settings.secret_store_backend.lower() == "env":
        log.warning(
            "SECRET_STORE_BACKEND=env: Gmail refresh tokens are stored in "
            "process memory only. They will be lost on uvicorn restart and "
            "users will have to /conectar_gmail again. For dev persistence "
            "set SECRET_STORE_BACKEND=file."
        )

    mode = settings.telegram_mode.lower()
    if mode == "disabled":
        log.info("TELEGRAM_MODE=disabled — bot will not start.")
        return

    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_MODE is set but TELEGRAM_BOT_TOKEN is missing. "
            "Refusing to start."
        )

    _state.bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    _state.dp = Dispatcher()

    # Import here to avoid a circular import via pipeline → app at module load.
    from . import handlers

    handlers.register(_state.dp)

    # Phase 6b: pubsub bridge between OAuth callback and Telegram chat.
    # Idempotent and harmless when no Gmail flow is active.
    from . import gmail_listener

    await gmail_listener.start()

    if mode == "polling":
        # If a webhook was set previously (e.g. testing in webhook mode,
        # or a stale prod registration on the same token), Telegram will
        # reject every getUpdates call with TelegramConflictError. Delete
        # the webhook before polling starts. drop_pending_updates=true
        # discards anything Telegram queued for the webhook so we don't
        # immediately replay stale updates from a previous session.
        try:
            await _state.bot.delete_webhook(drop_pending_updates=True)
        except Exception:  # pragma: no cover — best-effort
            log.exception(
                "delete_webhook before polling failed; if you see "
                "TelegramConflictError, run "
                "curl https://api.telegram.org/bot$TOKEN/deleteWebhook"
            )
        _state.polling_task = asyncio.create_task(_run_polling())
        log.info("Telegram bot started in polling mode.")
    elif mode == "webhook":
        if not settings.telegram_webhook_url:
            raise RuntimeError(
                "TELEGRAM_MODE=webhook requires TELEGRAM_WEBHOOK_URL."
            )
        if not settings.telegram_webhook_secret:
            raise RuntimeError(
                "TELEGRAM_MODE=webhook requires TELEGRAM_WEBHOOK_SECRET."
            )
        await _state.bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            drop_pending_updates=True,
        )
        log.info("Telegram webhook registered at %s.", settings.telegram_webhook_url)
    else:
        raise RuntimeError(f"Unknown TELEGRAM_MODE: {mode!r}")


async def stop_bot() -> None:
    # Stop the gmail listener first — it doesn't need the Bot object,
    # but stopping it before deleting the webhook avoids a window where
    # a callback could fire and find no chat to respond to.
    try:
        from . import gmail_listener

        await gmail_listener.stop()
    except Exception:  # pragma: no cover
        log.exception("gmail_listener.stop() raised")

    if _state.polling_task is not None:
        _state.polling_task.cancel()
        try:
            await _state.polling_task
        except (asyncio.CancelledError, Exception):
            pass
        _state.polling_task = None
    if _state.bot is not None:
        try:
            if settings.telegram_mode.lower() == "webhook":
                await _state.bot.delete_webhook()
        except Exception:  # pragma: no cover - best effort shutdown
            pass
        await _state.bot.session.close()
        _state.bot = None
    _state.dp = None


async def _run_polling() -> None:
    assert _state.bot is not None and _state.dp is not None
    try:
        await _state.dp.start_polling(_state.bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - log & exit
        log.exception("Polling loop crashed.")
