from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .redis_client import close_redis
from .routers import (
    accounts,
    agent,
    bill_occurrences,
    budgets,
    calendar,
    custom_events,
    debts,
    goals,
    jobs,
    notification_rules,
    notifications,
    nudges,
    queries,
    recurring_bills,
    reports,
    telegram,
    transactions,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_dev else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(budgets.router)
app.include_router(debts.router)
app.include_router(reports.router)
app.include_router(goals.router)
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


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
