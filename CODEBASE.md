# Finance Assistant — Codebase Map & How It Works

> **Last updated:** 2026-06-15 · **Migration head:** `0034` · **Active phase:** ~P7/P8
> **Authority:** This file is a navigation guide. The canonical, always-current
> operational reference is **`CLAUDE.md`** (rules, schema, phase history). When the
> two disagree, `CLAUDE.md` wins. Strategy/decisions live in the Obsidian vault at
> `~/Finance_project/30_Projects/Finance-Agent/`.

A personal-finance agent for **Costa Rica**. You capture transactions by chatting
(Telegram or a native iOS app), photographing receipts, forwarding nothing (Gmail
is read automatically), or tapping an iPhone Shortcut. An LLM **reads** your
messages; **deterministic Python decides and writes**. On top of accurate data it
answers questions, budgets in "sobres" (envelopes), and pushes back on
unaffordable goals — with math, never vibes.

Codename `ledger_cr`. Repo path has an intentional typo (`Fiancial_agent`) — never corrected.

---

## 1. The one idea that explains everything

> **The LLM extracts; rules decide. The LLM never writes financial data.**

Every capture is two passes:

1. **Extract** — a cheap model (Claude Haiku) turns free text / a photo / a PDF
   into a structured `ExtractionResult` (amount, merchant, intent, and a
   `dispatcher` tag: `write | query | control`).
2. **Decide & write** — plain Python validates that result and performs the
   mutation. No LLM is in the write path, ever.

Reads are different: the **query** path lets a stronger model (Claude Sonnet) call
read-only tools and narrate the answer — but those tools cannot mutate anything.

This split is physically two dispatchers:

| Dispatcher | Lives in | Job | LLM? |
|---|---|---|---|
| **Write** (deterministic) | `api/services/telegram_dispatcher.py` + `bot/` | Capture & mutate: transactions, transfers, goals, bills, debts | No — Python decides |
| **Read-only query** | `app/queries/dispatcher.py` + `app/queries/tools/` | Answer questions over your data | Yes — Sonnet + tools, can't write |

Pure financial math (CR salary, aguinaldo, revolving-credit projections) lives in
`app/domain/` — no LLM, no DB, no network.

---

## 2. Client surfaces (four ways data gets in)

All four hit the **same FastAPI backend** (`api/`). The in-app chat and the
Telegram bot funnel through the **same** `bot/pipeline.py::process_message()`.

| Surface | How it arrives | Auth | Entry point |
|---|---|---|---|
| **Native iOS app** (Expo) | REST + in-app chat | `Authorization: Bearer <jwt>` | `mobile/` → routers in `api/routers/` |
| **Telegram bot** (aiogram) | polling (dev) / webhook (prod) | Telegram user id | `bot/handlers.py` → `bot/pipeline.py` |
| **iPhone Shortcut** | POST webhook | `X-Shortcut-Token` header | `POST /transactions/shortcut` |
| **Gmail** (bank emails) | daily background scan | per-user OAuth refresh token | `workers/gmail_daily.py` → `api/services/gmail/` |

The Telegram bot is started **inside the API process** by the FastAPI lifespan
(`api/main.py`), only when `TELEGRAM_MODE != disabled`. The native app is a
separate Expo project that talks to the API over HTTP.

---

## 3. How a request flows (trace these to learn the code)

**A. Capture from chat** ("gasté ₡5000 en el súper")
```
POST /chat/message              api/routers/chat.py
  → process_message()           bot/pipeline.py        (channel-agnostic core)
  → extract_finance_intent()    api/services/llm_extractor/   (Haiku → ExtractionResult)
  → dispatch()                  api/services/telegram_dispatcher.py  (returns a Decision)
  → ProposeAction → save        bot/pending.py + bot/pending_db.py   (Redis + audit row)
  → user taps "Sí"
  → commit_pending()            bot/commit.py          (writes the Transaction row)
```

**B. Ask a question** ("¿cuánto gasté esta semana?")
```
POST /chat/message              api/routers/chat.py
  → process_message()           bot/pipeline.py        (extraction says dispatcher="query")
  → run_dispatch()              app/queries/dispatcher.py
  → Sonnet tool loop over       app/queries/tools/*.py (list_transactions, aggregate_transactions, …)
  → deterministic text answer   (the LLM narrates tool results; it never invents numbers)
```

**C. Gmail ingestion** (automatic)
```
workers/gmail_daily.py
  → scanner / reconciler        api/services/gmail/   (read inbox, parse bank emails)
  → insert SHADOW transactions  (not counted until approved — a 7-day review window)
  → user approves               bot /aprobar_shadow  OR  native GmailReviewScreen
  → shared logic                api/services/gmail/shadow_review.py
```

**D. Structured screens** (native app, no LLM)
```
mobile/src/screens/*.tsx  →  mobile/src/api/*.ts (axios, bearer)  →  api/routers/*  →  api/services/*  →  DB
```

---

## 4. Repository map

```
FinancialProject/
├── api/              # FastAPI backend (the server everything talks to)
├── bot/              # Telegram/native chat layer (aiogram v3) — drives process_message()
├── app/              # LLM query dispatcher + pure rules engines
│   ├── queries/      #   read-only Sonnet dispatcher + its tools + prompts
│   └── domain/       #   pure math: payroll (CR salary/aguinaldo), credit (revolving)
├── workers/          # Standalone cron jobs (Gmail daily, nightly insights)
├── mobile/           # Native iOS app (Expo SDK 54, React Native, TypeScript)
├── migrations/       # Hand-written Alembic migrations (0001 → 0034)
├── tests/            # pytest suite (~150 files; backend + bot seams)
├── scripts/          # Per-phase smoke scripts + one-off backfills
├── docs/             # Per-phase decision records, LOCAL_DEV.md, deployment, etc.
├── infra/azure/      # Azure Container Apps job manifests (YAML)
├── prompts/          # Insight extractor/editor prompt text (Phase 6c memory)
├── CLAUDE.md         # ← authoritative operational reference (read this)
├── LEARNING_GUIDE.md # self-study path from "reads Python" → "maintains this"
├── docker-compose.yml / Dockerfile* / DOCKER.md
└── CODEBASE.md       # this file
```
> **Legacy / empty** (historical scaffolding, safe to ignore): `agent/`, `jobs/`,
> `parsers/`. The web SPA (`web/`) was deleted at Phase 6f B16 (2026-06-01); the
> native app is now the only structured UI.

### `api/` — the backend
```
api/
├── main.py            # App init, mounts ~35 routers, lifespan starts the bot, /health
├── config.py          # Pydantic Settings (reads .env) — all config lands here
├── database.py        # Async SQLAlchemy engine + get_db session dependency
├── dependencies.py    # current_user: X-Shortcut-Token → bearer JWT → dev X-User-Id shim
├── redis_client.py    # Redis connection (durable bot state lives in Redis)
├── routers/           # One file per resource — the HTTP surface (see table below)
├── services/          # Business logic (the real work) — see breakdown below
├── models/            # ~37 SQLAlchemy ORM models (one per table)
├── schemas/           # Pydantic request/response models (validation at every boundary)
├── middleware/        # sensitive_redaction.py (scrubs memory payloads from logs)
├── data/              # CR reference data: bank_directory_cr.py, categories_cr.py
└── static/            # OAuth callback success pages
```

**`api/routers/`** (HTTP endpoints, all under `/api/v1`): `transactions`,
`accounts`, `transfers`, `debts`, `goals`, `envelopes`, `recurring_bills`,
`bill_occurrences`, `recurring_incomes`, `custom_events`, `categories`,
`budgets`, `dashboard`, `calendar`, `notifications`, `notification_rules`,
`nudges`, `jobs` (batch triggers), `queries` + `chat` (the two chat entry
points), `gmail`, `auth` (magic-link + device-code), `onboarding`, `users`,
`telegram`, `payroll`, `consents`, `insights` / `privacy_insights` /
`admin_insights`, `agent`, `reports`.

**`api/services/`** (where logic lives — routers stay thin):

| Folder/file | What it does |
|---|---|
| `llm_extractor/` | The Haiku extractor: `prompt.py`, `schema.py`, `runner.py`, `client.py`, `vision.py` (receipt photos), `document.py` (loan/card PDFs). Output = `ExtractionResult`. |
| `telegram_dispatcher.py` | The **write dispatcher** — turns an `ExtractionResult` into a `Decision` (ProposeAction / AskClarification / …). Channel-agnostic despite the name. |
| `finance/` | `affordability.py` (pushback engine), `cashflow.py` (unified monthly surplus — the one source of truth), `incomes.py`. |
| `envelopes.py`, `envelope_sharing.py` | "Sobres" budgeting: live spend, nested sub-sobres, shared via code. |
| `gmail/` | OAuth (`oauth.py`), `scanner.py`, `reconciler.py`, bank-email parsing, `shadow_review.py`, `whitelist.py`, `discovery.py`. |
| `insights/` | Phase 6c memory — two writers: `computed.py` (SQL aggregates) + `extractor.py` (Haiku); `lifecycle.py`, `persister.py`, `memory_view.py`. |
| `nudges/` | Phase 5d engagement: `orchestrator.py`, `policy.py` (anti-spam rules), `evaluators/`, `phrasing.py`, `delivery.py`, `feed.py`. |
| `dashboard/` | `summary.py` (home figures), `materialized.py` (MV refresh). |
| `auth/` | `magic_link.py`, `device_code.py`, `session.py` (JWT codec). |
| `dedup/` | `duplicate_detector.py` (deterministic likely-duplicate detection). |
| `dispatch/lazy_detection.py` | Match an account "hint" to an existing account after extraction. |
| `extraction/email_extractor.py` | Bank-email field extraction. |
| flat files | `accounts.py`, `transactions.py`, `transfers.py`, `goals.py`, `categories.py`, `recurrence.py` (bill occurrences), `amortization.py` (French/Ley 7472), `fx.py` (₡/$ — fixed ₡500 placeholder), `income_frequency.py`, `credit_cards.py`, `snapshots.py`, `advice_trace.py`, `consents.py`, `budget.py` (token budget), `users.py`, `secrets.py`. |

### `bot/` — the chat layer (Telegram + native)
```
pipeline.py          # process_message(): the ROUTER — extract → write/query/control. Start here.
handlers.py          # aiogram handlers (on_text, on_callback, …) — Telegram glue
commit.py            # commit_pending(): the deterministic write of a confirmed proposal
pending.py / pending_db.py   # pending proposals (Redis state + audit rows)
clarification.py     # multi-turn "¿de qué cuenta?" state + merge_reply
account_creation.py  # conversational "crear cuenta …" mini-flow (Redis)
registration.py      # P8 Telegram cold-start sign-up (unknown user → account)
undo.py              # /undo
nudges_send.py / memory_handlers.py / gmail_handlers.py / onboarding_*.py
delivery_send.py     # sanitize → split → sequential Telegram send
redis_keys.py        # ← centralized Redis key conventions (telegram:* prefix kept historically)
messages_es.py       # Spanish (voseo, CR) user-facing strings
app.py               # bot startup/shutdown (start_bot/stop_bot), wired into api/main lifespan
```

### `app/` — query dispatcher + pure rules
```
queries/
  dispatcher.py      # run_dispatch(): the read-only Sonnet tool loop (cannot mutate)
  tools/             # the read-only tools the LLM may call (list/aggregate transactions,
                     #   accounts, debts, envelopes, affordability, goals, salary, …)
  prompts/system.py  # the query system prompt (cache-anchored on compare_periods)
  history.py         # per-user conversation window (Redis)
domain/              # PURE rules (no LLM/DB/network) — the deterministic core
  payroll/           # cr_salary.py (gross→net), cr_cycles.py (aguinaldo/salario escolar), rates.py
  credit/revolving.py# revolving-credit projections + never-payoff detection
```

### `mobile/` — native iOS app (Expo)
```
App.tsx / index.ts / app.json    # entry + Expo config (pinned to SDK 54)
src/
  api/*.ts          # one axios helper per resource (client.ts adds the bearer token)
  screens/*.tsx     # ~27 screens (Dashboard, Chat, Accounts, Transactions, Debts, Goals, …)
  navigation/       # 5-tab nav: Inicio · Chat · Cuentas · Movimientos · Más (+ per-tab stacks)
  components/        # shared UI (pickers, edit modals, EnvelopePicker, charts, …)
  lib/              # auth, amortization, format, incomeFrequency, queryClient, observability
  theme.ts          # design tokens (neutral "Rams" palette; color = meaning only)
```
The in-app chat (`screens/Chat.tsx`) posts to `POST /chat/message` and renders the
**same** replies the Telegram bot produces, plus `open_screen` handoffs that route
to native forms (debt/card creation, envelope/account assignment).

---

## 5. "Where do I find…?"

| I want to… | Look in |
|---|---|
| Add/Change an HTTP endpoint | `api/routers/<resource>.py` (+ schema in `api/schemas/`) |
| Change business logic | `api/services/…` (routers should stay thin) |
| Change a DB table | new file in `migrations/versions/` **and** the model in `api/models/` |
| Change how chat routes a message | `bot/pipeline.py::process_message` |
| Change what the extractor pulls out | `api/services/llm_extractor/prompt.py` + `schema.py` |
| Add a read-only "the agent can answer X" tool | `app/queries/tools/` (register before `compare_periods`) |
| Change the query agent's persona/rules | `app/queries/prompts/system.py` |
| Change deterministic financial math | `app/domain/` (payroll, credit) or `api/services/finance/` |
| Change a native screen / form | `mobile/src/screens/` (+ `mobile/src/api/` for the call) |
| Find a Redis key | `bot/redis_keys.py` |
| Change Spanish copy | `bot/messages_es.py` (bot) / inline in the query layer |
| Understand a past decision | `docs/phase-*-decisions.md` or the vault `05_Decisions/` |

---

## 6. Data model & migrations

- **All migrations are hand-written** (no autogenerate) in `migrations/versions/`,
  numbered `0001` … `0034` (current head `0034`). Each schema change = one file.
- Models mirror tables 1:1 in `api/models/`. UUID PKs, `TIMESTAMPTZ`,
  `NUMERIC(12,2)` amounts (negative = expense, positive = income),
  `transaction_date` is day-level `DATE`.
- **Core tables:** `users`, `accounts`, `transactions`, `transfers`,
  `categories`/`user_categories`, `budgets`, `goals` (+ `goal_contributions`),
  `debts` (+ `debt_payments`), `recurring_bills` (+ `bill_occurrences`),
  `recurring_incomes`, `custom_events`, `notification_*`.
- **Feature tables:** `envelopes` (+ `envelope_members`), `credit_card_terms`,
  `user_insights` (memory), `user_nudges`, `llm_extractions` /
  `llm_query_dispatches` (telemetry), `gmail_*` (ingestion), `advice_events` /
  `*_snapshots` / `user_consents` (data foundation), `magic_link_tokens`.

For the authoritative, annotated schema (every column + the why), see the
**Database Schema** section of `CLAUDE.md`.

---

## 7. Run it locally

The canonical, step-by-step cookbook (including the iPhone + Expo flow) is
**`docs/LOCAL_DEV.md`**. Short version:

**Backend**
```bash
docker compose up -d db redis          # Postgres 16 (host port 5433) + Redis 7 (6379)
uv run alembic upgrade head            # apply migrations (never auto-run at container boot)
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
# health: GET http://localhost:8000/health   (Swagger /docs in dev only)
```
The Telegram bot starts with the API when `TELEGRAM_MODE=polling` and a token is set.

**Native app** (separate terminal)
```bash
cd mobile
# set EXPO_PUBLIC_API_BASE_URL=http://<your-LAN-IP>:8000 in mobile/.env.local
npm install && npm start            # scan the QR with Expo Go on the iPhone
# then in Telegram: /login → paste the 6-char code into the app's Login screen
```

**Tests**
```bash
uv run pytest -p no:cacheprovider -q tests/<file>.py     # focused
bash scripts/test_phase_7b.sh                            # a gate (mobile tsc + byte-locked regression)
cd mobile && npx tsc --noEmit                            # mobile typecheck (no native CI yet)
```

---

## 8. Conventions & gotchas (the short list)

- **LLM never writes financial data.** No LLM inside the write dispatcher — ever.
- **Spanish (voseo, CR)** for user-facing text; English for code, logs, docs.
- **Redis is the source of truth** for durable bot state; keys in `bot/redis_keys.py`
  keep the historical `telegram:` prefix even for native-app state.
- **`current_user` resolves** `X-Shortcut-Token` → bearer JWT → dev `X-User-Id`
  shim (last one is dev-only; don't build on it). `status='suspended'` → 403.
- **The query path must never surface a raw 500** — it maps failures to Spanish
  copy; the chat endpoint has a top-level guard too.
- **`uvicorn --reload` does NOT pick up a `git merge`** (it watches inodes; git
  swaps files) — restart the server after merging backend changes.
- **Prompt cache breakpoint limit is 4**; `compare_periods` stays the last
  registered query tool as the cache anchor — don't reorder.
- **`committed_outflows` (cashflow) is byte-locked** by a regression test — any
  cashflow-adjacent change must keep it identical.

The full rule set + every closed-phase "hard rule to preserve" lives in
`CLAUDE.md`; operational lessons that already cost time are in the vault
`08_Code-Context/AGENT_CONTEXT.md`.

---

## 9. Further reading

| Doc | For |
|---|---|
| **`CLAUDE.md`** | Authoritative rules, full schema, phase-by-phase history |
| `LEARNING_GUIDE.md` | A guided self-study path through the project |
| `docs/LOCAL_DEV.md` | Running everything locally (incl. iPhone + Expo) |
| `docs/phase-*-decisions.md` | Why a given feature was built the way it was |
| `docs/AZURE_DEPLOYMENT.md` / `DOCKER.md` | Deploy + container details |
| Vault `~/Finance_project/30_Projects/Finance-Agent/` | Strategy, roadmap, decision notes |

> Heads-up: `docs/CODEBASE_GUIDE.md` covers similar ground but currently trails the
> schema (it predates several migrations). Trust `CLAUDE.md` + this file for the
> current head.
