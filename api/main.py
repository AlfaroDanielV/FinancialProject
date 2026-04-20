from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
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
    recurring_bills,
    reports,
    transactions,
    users,
)

app = FastAPI(
    title="Finance Assistant API",
    description="Personal finance backend — Costa Rica MVP",
    version="0.1.0",
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
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
app.include_router(calendar.router)
app.include_router(jobs.router)
app.include_router(agent.router)


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
