# Finance Assistant — Codebase Overview

> **Last updated:** 2026-04-13  
> **Current state:** Phase 1 complete. Phases 2–5 in progress or planned.

---

## What This Project Is

A personal finance management backend for a **single-user MVP** built for Costa Rica. The system lets you track transactions, set budgets and savings goals, and will eventually support AI-powered financial insights via a WhatsApp chatbot.

Transactions can be entered manually through the API, via an **iPhone Shortcut webhook**, or (in future phases) via email parsing or WhatsApp.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Web Framework | FastAPI 0.115 |
| ASGI Server | Uvicorn |
| Database | PostgreSQL 16 (async via AsyncPG) |
| ORM / Migrations | SQLAlchemy 2.0 (async) + Alembic |
| Cache | Redis 7 |
| Settings | Pydantic Settings + `.env` |
| HTTP Client | httpx |
| AI | Anthropic API (planned Phase 5+) |
| Package Manager | uv (Astral) |
| Container | Docker + docker-compose |

---

## Project Structure

```
FinancialProject/
├── api/                        # Core FastAPI application
│   ├── main.py                 # App init, router registration, lifespan
│   ├── config.py               # Pydantic Settings (reads .env)
│   ├── database.py             # SQLAlchemy async engine + session factory
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── base.py             # DeclarativeBase
│   │   ├── user.py
│   │   ├── transaction.py
│   │   ├── account.py
│   │   ├── budget.py
│   │   ├── goal.py
│   │   ├── event.py
│   │   └── weekly_report.py
│   ├── routers/                # Route handlers
│   │   ├── transactions.py     # ✅ IMPLEMENTED — CRUD + iPhone Shortcut webhook
│   │   ├── reports.py          # 🔲 Phase 3 stub
│   │   ├── goals.py            # 🔲 Phase 3 stub
│   │   ├── events.py           # 🔲 Phase 4 stub
│   │   └── agent.py            # 🔲 Phase 5 stub
│   ├── schemas/
│   │   └── transaction.py      # Pydantic request/response models
│   └── services/               # Business logic (empty, for future use)
├── migrations/                 # Alembic database migrations
│   └── versions/
│       └── 0001_initial_schema.py   # All 7 tables created here
├── agent/                      # AI agent modules (empty — Phase 5+)
├── jobs/                       # Background tasks (empty — future)
├── parsers/                    # Email/document parsers (empty — future)
├── scripts/
│   └── create_user.py          # One-time user seed script
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── alembic.ini
├── .env.example
└── CODEBASE.md                 # This document
```

---

## Database Schema

All tables use UUID primary keys and live in PostgreSQL.

### `users`
Single-user MVP. One record. Referenced by all other tables.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| name | String | |
| currency | String(10) | default: `CRC` |
| timezone | String(50) | default: `America/Costa_Rica` |
| created_at | Timestamp | |

### `transactions`
Core of the app. Negative amount = expense, positive = income.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| account_id | FK → accounts | nullable |
| amount | Numeric(12,2) | negative=expense, positive=income |
| currency | String(10) | default: `CRC` |
| merchant | String(255) | nullable |
| description | Text | nullable |
| category | String(100) | nullable |
| subcategory | String(100) | nullable |
| transaction_date | Date | |
| source | String(50) | `manual` \| `email_parse` \| `shortcut` \| `whatsapp` |
| source_ref | String(500) | deduplication key |
| parse_status | String(50) | `confirmed` \| `flagged` |
| is_duplicate | Boolean | |
| created_at | Timestamp | |

Indexed on `(user_id, transaction_date)` and `source_ref`.

### `accounts`
Bank accounts / credit cards.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| name | String | |
| account_type | String(50) | `checking` \| `savings` \| `credit` \| `investment` |
| is_active | Boolean | |
| created_at | Timestamp | |

### `budgets`
Monthly spending limits per category.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| category | String(100) | |
| monthly_limit | Numeric(12,2) | |
| is_active | Boolean | |

### `goals`
Savings goals with optional target dates.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| name | String(255) | |
| target_amount | Numeric(12,2) | |
| current_amount | Numeric(12,2) | default: 0 |
| target_date | Date | nullable |
| monthly_contribution | Numeric(12,2) | nullable |
| is_active | Boolean | |

### `events`
Recurring or one-time events with cost estimates and advance alerts.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| name | String(255) | |
| event_type | String(50) | `birthday` \| `anniversary` \| `maintenance` \| `trip` \| `subscription` |
| event_date | Date | |
| estimated_cost | Numeric(12,2) | nullable |
| is_recurring | Boolean | |
| recurrence_rule | Text | `yearly` \| `monthly` \| `quarterly` |
| alert_days_before | Integer[] | default: `[30, 14, 7]` |

### `weekly_reports`
Stored JSON snapshots of generated spending reports.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users | |
| report_date | Date | |
| report_data | JSONB | |

---

## API Endpoints

Base URL: `http://localhost:8000/api/v1`

### Transactions — `/api/v1/transactions` ✅

| Method | Path | Description |
|---|---|---|
| `POST` | `/shortcut` | iPhone Shortcut webhook. Requires `X-Shortcut-Token` header. |
| `POST` | `/` | Create a transaction manually. |
| `GET` | `/` | List transactions. Params: `limit`, `offset`. |
| `GET` | `/{id}` | Get one transaction. |
| `PATCH` | `/{id}/flag` | Mark a transaction for review (`parse_status = "flagged"`). |

**Shortcut body example:**
```json
{
  "amount": 4500,
  "merchant": "Mas x Menos",
  "category": "Groceries",
  "is_expense": true,
  "description": "Weekly groceries",
  "transaction_date": "2026-04-13"
}
```

### Health — `/health` ✅

Returns `{ "status": "ok", "environment": "development" }`.

### Reports, Goals, Events, Agent — Stubs (no logic yet)

Routers are registered but return placeholder responses. Implementation planned in Phases 3–5.

**Docs UI** (dev mode only):
- Swagger: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## Configuration

All config is in `api/config.py` using Pydantic Settings. Values are read from `.env`.

```env
DATABASE_URL=postgresql+asyncpg://finance:finance@localhost:5432/finance
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=sk-ant-...
ENVIRONMENT=development
SECRET_KEY=<32-char-random-string>
DEFAULT_USER_ID=<uuid>          # Set after running create_user.py
SHORTCUT_TOKEN=<random-token>   # Authenticates iPhone Shortcut requests
```

**Single-user auth model:** Every request is automatically scoped to `DEFAULT_USER_ID`. No login/session logic yet.

---

## Running Locally

### 1. Start services

```bash
docker-compose up -d
```

This starts:
- **PostgreSQL 16** on port `5433`
- **Redis 7** on port `6379`
- **FastAPI** on port `8000` (with `--reload`)

### 2. Create the user (first time only)

```bash
python scripts/create_user.py --name "Your Name"
```

Copy the returned UUID into `.env` as `DEFAULT_USER_ID`.

### 3. Apply migrations

Alembic runs automatically via the app lifespan, or manually:

```bash
alembic upgrade head
```

### 4. Test

- Open `http://localhost:8000/docs` and try the endpoints.
- Or send a Shortcut request with `X-Shortcut-Token: <SHORTCUT_TOKEN>`.

---

## Phase Roadmap

| Phase | Status | Description |
|---|---|---|
| **1** | ✅ Complete | Transaction CRUD, iPhone Shortcut webhook, database setup |
| **2** | Ready | Accounts CRUD (model + migration exist, router TBD) |
| **3** | Planned | Budgets, savings goals, weekly spending reports |
| **4** | Planned | Event alerts (subscriptions, birthdays, trips) |
| **5+** | Planned | AI agent — Anthropic API + WhatsApp/OpenClaw integration |

---

## Key Files Quick Reference

| File | Purpose |
|---|---|
| [api/main.py](api/main.py) | App init, middleware, router mounts |
| [api/config.py](api/config.py) | All settings / env vars |
| [api/database.py](api/database.py) | Async SQLAlchemy engine and session |
| [api/routers/transactions.py](api/routers/transactions.py) | Primary implemented router |
| [api/models/transaction.py](api/models/transaction.py) | Core data model |
| [migrations/versions/0001_initial_schema.py](migrations/versions/0001_initial_schema.py) | Full DB schema |
| [scripts/create_user.py](scripts/create_user.py) | Seed the single user |
| [docker-compose.yml](docker-compose.yml) | Local dev orchestration |
| [pyproject.toml](pyproject.toml) | Dependencies and project metadata |
