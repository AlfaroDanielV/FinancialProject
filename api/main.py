import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .logging_config import setup_logging
from .redis_client import close_redis, get_redis
from .routers import (
    accounts,
    admin_insights,
    agent,
    auth,
    bill_occurrences,
    budgets,
    calendar,
    categories,
    chat,
    consents,
    custom_events,
    dashboard,
    debts,
    envelopes,
    gmail,
    goals,
    insights,
    jobs,
    notification_rules,
    notifications,
    nudges,
    onboarding,
    payroll,
    privacy_insights,
    queries,
    recurring_bills,
    recurring_incomes,
    reports,
    telegram,
    transactions,
    transfers,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    # Phase 6c B8: scrub user_insights payloads from log records before they
    # reach stdout. Installed AFTER setup_logging so the filter attaches to
    # the just-built handler.
    from .middleware.sensitive_redaction import install_on_root_logger

    install_on_root_logger()
    # Telegram bot is optional — only started when TELEGRAM_MODE != disabled.
    # This keeps CI, tests, and fresh dev envs runnable without a bot token.
    from bot.app import start_bot, stop_bot

    try:
        await start_bot()
    except Exception:
        # A bot failure must not break the REST API on boot — log and carry on.
        # In polling / webhook mode a misconfigured token is the likely
        # cause; the operator sees the error and re-deploys.
        import logging

        logging.getLogger("api.main").exception(
            "Telegram bot failed to start — continuing without it."
        )
    # Phase 5d proactive messaging: start the in-process nudge scheduler AFTER
    # the bot so telegram_send_fn can reach the aiogram Bot singleton. Gated on
    # settings.nudge_scheduler_enabled; a no-op when disabled.
    from .services.nudges.scheduler import (
        start_nudge_scheduler,
        stop_nudge_scheduler,
    )

    start_nudge_scheduler()
    yield
    await stop_nudge_scheduler()
    await stop_bot()
    await close_redis()


app = FastAPI(
    title="Finance Assistant API",
    description="Personal finance backend — Costa Rica MVP",
    version="0.1.0",
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
    lifespan=lifespan,
)


# Phase 6f B16: CORS middleware removed with the SPA. The native app
# (React Native / axios) and the iPhone Shortcut webhook are non-browser
# clients that don't enforce CORS. Re-add if a browser client returns.


# ── P10 B0.5 (R1/D13): chat-route validation nets ────────────────────────────
# A well-formed chat message must never surface as a non-2xx. The endpoint
# guard covers the pipeline + serialization; these handlers cover the two
# validation seams that fire OUTSIDE the handler body: request-body validation
# (RequestValidationError, pre-handler) and response-model validation
# (ResponseValidationError, post-return). Both degrade to the handled 200 chat
# body ONLY for /api/v1/chat/* — every other route keeps FastAPI defaults.

_CHAT_PATH_PREFIX = "/api/v1/chat"


def _is_chat_path(request: Request) -> bool:
    return request.url.path.startswith(_CHAT_PATH_PREFIX)


def _chat_error_json() -> JSONResponse:
    from bot import messages_es  # local: keep api.main light at import time

    return JSONResponse(
        status_code=200,
        content={
            "reply_text": messages_es.CHAT_UNEXPECTED_ERROR,
            "buttons": [],
            "url_buttons": [],
            "open_screen": None,
            "error_class": "system",
        },
    )


@app.exception_handler(RequestValidationError)
async def _request_validation_handler(request: Request, exc: RequestValidationError):
    if _is_chat_path(request):
        logging.getLogger("api.main").warning(
            "chat_request_validation_failed path=%s errors=%s",
            request.url.path,
            exc.errors()[:3],
        )
        return _chat_error_json()
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(ResponseValidationError)
async def _response_validation_handler(request: Request, exc: ResponseValidationError):
    if _is_chat_path(request):
        logging.getLogger("api.main").error(
            "chat_response_validation_failed path=%s", request.url.path
        )
        return _chat_error_json()
    raise exc


app.include_router(users.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(transfers.router)
app.include_router(budgets.router)
app.include_router(debts.router)
app.include_router(categories.router)
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(goals.router)
app.include_router(envelopes.router)
app.include_router(insights.router)
app.include_router(recurring_bills.router)
app.include_router(bill_occurrences.router)
app.include_router(custom_events.router)
app.include_router(notification_rules.router)
app.include_router(notifications.router)
app.include_router(nudges.router)
app.include_router(calendar.router)
app.include_router(jobs.router)
app.include_router(agent.router)
app.include_router(queries.router)
app.include_router(telegram.users_tg_router)
app.include_router(telegram.telegram_router)
app.include_router(gmail.router)
app.include_router(privacy_insights.router)
app.include_router(admin_insights.router)
app.include_router(consents.router)
app.include_router(recurring_incomes.router)
app.include_router(onboarding.router)
app.include_router(payroll.router)
app.include_router(auth.router)
app.include_router(chat.router)

# Static pages for the OAuth callback redirect targets. Kept separate
# from the router so adding a new HTML file doesn't require code changes.
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)):
    checks: dict[str, object] = {"db": False, "redis": False}

    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception as exc:
        checks["db_error"] = str(exc)

    try:
        await get_redis().ping()
        checks["redis"] = True
    except Exception as exc:
        checks["redis_error"] = str(exc)

    all_ok = checks["db"] is True and checks["redis"] is True
    return {"status": "ok" if all_ok else "degraded", **checks}
