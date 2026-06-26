# CLAUDE.md — Personal Finance Agent

## Project Brain (Obsidian vault)

Strategic context and architectural decisions for FinancialProject live in:
`~/Finance_project/30_Projects/Finance-Agent/`
 Before touching code, review the vault context and the engineering source notes I just integrated.                                                                                                                             
                                                                                                                                                                                                                                                 
    Read in this order:                                                                                                                                                                                                                          
                                                                                                                                                                                                                                                 
    1. 30_Projects/Finance-Agent/README.md                                                                                                                                                                                                       
    2. 30_Projects/Finance-Agent/00_Project-Brain.md                                                                                                                                                                                             
    3. 30_Projects/Finance-Agent/04_Architecture.md                                                                                                                                                                                              
    4. Relevant decision notes under:                                                                                                                                                                                                            
       30_Projects/Finance-Agent/05_Decisions/                                                                                                                                                                                                   
                                                                                                                                                                                                                                                 
    Then read these source notes:                                                                                                                                                                                                                
                                                                                                                                                                                                                                                 
    - 10_Sources/Books/The Pragmatic Programmer - Principios y Filosofia de Ingenieria.md                                                                                                                                                        
    - 10_Sources/Books/A Philosophy of Software Design.md                                                                                                                                                                                        
    - 10_Sources/Books/Designing Data-Intensive Applications - Principios de Ingenieria de Sistemas.md                                                                                                                                           
    - 10_Sources/Books/Clean Code - Principles of Software Craftsmanship for the Long-Term Vault.md                                                                                                                                              
                                                                                                                                                                                                                                      

**Always read before non-trivial work:**
- `08_Code-Context/AGENT_CONTEXT.md` — operational rules and stack constraints
- `00_Project-Brain.md` — current state and active phase
- `Roadmap.md` — phase sequence
- `05_Decisions/` — read decisions relevant to the current task
- `11_Phases/` — closed-phase retrospectives (5a/5b/5d/6a/6b/6c)

**Update the vault when:**
- A durable decision emerges → draft a new `05_Decisions/Decision - <Name>.md`
- A phase closes → add `11_Phases/Phase-Nx.md` summary and stub it here
- An architecture change happens → update `04_Architecture.md`
- Weekly → drop a `10_Weekly-Reviews/YYYY-MM-DD.md`

Draft notes follow the YAML frontmatter pattern of existing files. Never delete decision notes; mark `status: deprecated` instead.

---

## What This Project Is

A personal finance agent that automates transaction capture, parses bank emails, generates financial reports, and provides a conversational AI layer capable of giving financial advice — including pushing back on unrealistic goals. Built as a personal tool first, with a clear path to multi-tenancy if commercialized.

**The core thesis**: If the ledger is wrong, the agent is useless. Data accuracy is a prerequisite to any AI layer. Every architectural decision flows from this.

---

## Long-Term Vision

1. **Personal MVP** — A system I trust enough to check instead of my bank app
2. **Stabilization** — 4+ weeks of reliable daily use with accurate weekly reports
3. **Product** — Multi-tenant SaaS sold to individuals and couples via Telegram bot, API-backed LLM, chat-native UX

The product is **not** a dashboard-first app. It is a financial assistant
that lives in a messaging thread. In Phase 6f the primary surface becomes a
native iOS app (Expo) that combines an in-app chat (transaction capture +
queries + receipt photo upload) with structured read/edit screens for
accounts, transactions, debts, goals, etc. The Phase 6e SPA was retired at
Phase 6f B16 (`web/` deleted 2026-06-01). Telegram stays alive as a backup capture
surface.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| **Language** | Python 3.12 | |
| **Framework** | FastAPI | Async, with `lifespan` context manager |
| **Database** | PostgreSQL 16 | Via Docker Compose |
| **ORM** | SQLAlchemy 2.x (async) | Mapped columns, `AsyncSession` |
| **Migrations** | Alembic | Hand-written migrations, no autogenerate |
| **Cache/Queue** | Redis 7 | Source of truth for durable bot state |
| **Schemas** | Pydantic v2 | Request/response validation |
| **Messaging** | Telegram Bot API (aiogram v3) | NOT WhatsApp |
| **Email** | Gmail API | OAuth2, per-bank parsers |
| **LLM** | Anthropic API (Haiku for extraction, Sonnet for queries) | Not self-hosted |
| **SPA (retired)** | ~~Vite + React + Tailwind~~ | Phase 6d/6e Centro Financiero SPA — **retired at 6f B16 (`web/` deleted 2026-06-01)**; native app replaces it |
| **Native app** | Expo (managed) + React Native + TypeScript + React Navigation + TanStack Query + RHF + Zod | Phase 6f iOS-first; in-app chat reuses `bot/pipeline.py::process_message()` |
| **Transaction Capture** | iPhone Shortcuts → POST webhook | `X-Shortcut-Token` header |
| **Containerization** | Docker Compose (dev), Azure Container Apps (prod) | |
| **Settings** | Pydantic `BaseSettings` | Reads from `.env` |

### Why NOT these alternatives

- **WhatsApp / Baileys**: Unofficial API, Meta bans accounts. Telegram is official and zero ban risk.
- **Self-hosted LLMs**: GPU starts at ~$80/mo. API ~$5/mo at 10 users. Self-host only at 10k+ DAU.
- **LLM-generated pushback**: Financial pushback must be deterministic. LLM wraps results, never calculates.
- **Autogenerated Alembic migrations**: Hand-written gives full control.

---

## Project Structure

```
finance-agent/
├── api/
│   ├── main.py                 # FastAPI app, router registration, lifespan
│   ├── config.py               # Pydantic BaseSettings
│   ├── database.py             # Async engine, session factory, get_db
│   ├── dependencies.py         # current_user resolution
│   ├── routers/                # transactions, accounts, recurring_bills, bill_occurrences,
│   │                           # custom_events, notification_rules, notifications, calendar,
│   │                           # jobs, gmail/oauth, auth (magic link), onboarding, queries,
│   │                           # nudges, telegram, users, agent, categories, dashboard, transfers
│   ├── services/               # amortization, recurrence, dedup, email_parser,
│   │                           # nudges/*, insights/*, gmail/*, finance/*, auth/*,
│   │                           # budget, categories, transfers, dashboard/*
│   ├── models/__init__.py      # All SQLAlchemy ORM models
│   ├── schemas/                # Pydantic schemas per resource
│   ├── middleware/             # sensitive_redaction (Phase 6c B8)
│   └── data/                   # bank_directory_cr.py, categories_cr.py
├── bot/                        # aiogram v3 — Telegram bot
│   ├── pipeline.py             # Routing: extractor → dispatcher
│   ├── handlers.py             # on_text, on_callback, on_nudge_callback
│   ├── gmail_handlers.py       # /conectar_gmail flow
│   ├── onboarding_handlers.py  # Phase 6c discovery flow
│   ├── memory_handlers.py      # /memoria, /olvidar, /editar_memoria
│   ├── redis_keys.py           # Centralized key conventions
│   └── delivery_send.py        # sanitize → split → sequential Telegram send
├── app/queries/                # Read-only LLM dispatcher (Phase 6a)
│   ├── dispatcher.py
│   ├── tools/                  # get_user_context, compare_periods, etc.
│   └── history.py              # query_history Redis
├── app/domain/                 # Pure rules layers (no LLM/DB/network)
│   ├── payroll/                # CR gross→net salary calculator
│   └── credit/                 # Phase 7b revolving-credit projections
├── workers/                    # gmail_daily.py, insights_nightly.py, insights_lifecycle.py
│                               # (web/ Phase 6d/6e SPA — DELETED at 6f B16, 2026-06-01)
├── mobile/                     # Phase 6f native iOS app (Expo, React Native) — created at 6f B1
├── migrations/versions/        # Hand-written Alembic (0001 → 0030)
├── tests/                      # pytest suite
├── scripts/                    # Phase smoke scripts (phase5a/5b/6b/6c/etc.)
├── docs/phase-*/               # Per-phase operational docs (privacy, deployment, etc.)
├── docker-compose.yml
└── CLAUDE.md
```

---

## Database Schema (Core Tables)

All tables use UUID primary keys via `gen_random_uuid()`. Timestamps are `TIMESTAMPTZ`. Amounts are `NUMERIC(12,2)` — negative = expense, positive = income.

- **users** — `id`, `email` (UNIQUE), `full_name`, `phone_number`, `country` (CR), `timezone`, `currency` (CRC), `locale`, `shortcut_token` (UNIQUE, opaque), `telegram_user_id` (UNIQUE), `whatsapp_phone`, `status`, timestamps
- **accounts** — `id`, `user_id` FK, `name`, `account_type` (checking/savings/credit/investment), `is_active`
- **transactions** — `id`, `user_id` FK, `account_id` FK, `amount`, `currency`, `merchant`, `description`, `category`, `transaction_date`, `source` (manual/email/import/telegram/gmail/reconciled), `source_ref` (Gmail Message-ID), `gmail_message_id`, `parse_status`, `raw_data` JSONB, `tags` ARRAY, `is_recurring`, `status` (confirmed/shadow/pending_review)
- **budgets**, **goals**, **weekly_reports**, **debts**, **debt_payments** — see migrations 0001–0005
- **Phase 4 tables** — `recurring_bills`, `bill_occurrences`, `custom_events`, `notification_rules`, `notification_events` (see "Phase 4 tables" below)
- **Phase 5b tables** — `llm_extractions` (migration 0007)
- **Phase 5d tables** — `user_nudges`, `user_nudge_silences`, `pending_confirmations` (migration 0008)
- **Phase 6a tables** — `llm_query_dispatches` (migrations 0009, 0010)
- **Phase 6b tables** — `gmail_credentials`, `gmail_sender_whitelist`, `bank_notification_samples`, `gmail_messages_seen`, `gmail_ingestion_runs`, `gmail_discovery_runs` (migrations 0011, 0012, 0015)
- **Phase 6c tables** — `user_insights`, `user_insights_audit` (migrations 0013, 0014)
- **Phase 6d tables** — `magic_link_tokens`, `recurring_incomes`, `lazy_detection_events` (migration 0016)
- **Phase 6e tables/views** — `goal_contributions`, `transfers`, `user_categories`, `currency_rates`, `transactions.transfer_id`, `transactions.category_id`, `users.display_currency`, `accounts.archived`, `magic_link_tokens.target_path`, `mv_monthly_summary_by_user`, `mv_yearly_summary_by_user` (migration 0017); `transactions.archived` + partial index `ix_transactions_user_date_active` (migration 0018); `debts.archived` (migration 0019); `recurring_incomes.archived` (migration 0020)
- **Phase 6f/7/7a tables** — `users.expo_push_token` (0021), `envelopes` + `transactions.envelope_id` (0022), `nudge_type` CHECK widened (0023), `envelopes.parent_id`+`depth` (0024), `recurring_incomes.gross_monthly` (0025), `recurring_bills.envelope_id` + `debts.envelope_id` (0026), `notification_events.debt_id` (0027)
- **Phase 7b tables** — `debt_payments.transaction_id` + `bill_occurrences.transaction_id` FKs aligned to `ON DELETE SET NULL` (migration 0028); `credit_card_terms` 1:1 on the credit account — APRs as 0–1 fractions, mínimo = max(pct × saldo, piso), `credit_limit`, `statement_day`, `payment_due_day`, `envelope_id`; deliberately NO balance column (migration 0029)
- **Phase 7d columns** — `transactions.goal_id` (FK goals SET NULL — marks goal aportes/refunds, excluded from income/expense math like transfer legs), `goal_contributions.source_account_id` (FK accounts SET NULL) + `goal_contributions.refund_transaction_id` (FK transactions SET NULL — refund idempotency stamp) (migration 0030)
- **Phase 7e tables** — `advice_events`, `envelope_snapshots`, `cashflow_snapshots`, `user_consents` (migration 0031)
- **Phase 7g columns** — `recurring_incomes.hire_date` (DATE NULL — fecha de incorporación; prorates CR aguinaldo + salario escolar) (migration 0032)

### Phase 4 tables (recurring bills + calendar alerts)

All amounts in Phase 4 tables are `NUMERIC(14,2)`. Dates are calendar `DATE`; timestamps `TIMESTAMPTZ`.

- **recurring_bills** — template for a recurring charge. `id`, `name`, `provider`, `category` (Phase 6d CR category string from `CATEGORIES_CR`, with legacy Phase 4 bill-category strings still accepted), `amount_expected` (nullable if `is_variable_amount`), `currency`, `is_variable_amount`, `account_id` FK → accounts, `frequency` (weekly/biweekly/monthly/bimonthly/quarterly/semiannual/annual/custom), `day_of_month` (1–31, CHECK), `recurrence_rule` (iCal RRULE, required when frequency=custom), `start_date`, `end_date`, `lead_time_days`, `is_active`, `notes`, `linked_loan_id` FK → debts (nullable, `ON DELETE SET NULL`).
- **bill_occurrences** — materialized instance. `id`, `recurring_bill_id` FK cascade, `due_date`, `amount_expected`, `amount_paid`, `status` (pending/paid/partially_paid/skipped/overdue/cancelled), `paid_at`, `transaction_id` FK → transactions, `notes`. **UNIQUE(recurring_bill_id, due_date)** guarantees idempotent regeneration.
- **custom_events** — one-off or recurring calendar events not tied to a bill. `id`, `title`, `description`, `event_type` (tax_deadline/goal_milestone/income_expected/reminder/other), `event_date`, `is_all_day`, `event_time`, `amount`, `currency`, `recurrence_rule`, `is_active`.
- **notification_rules** — advance-notice configuration. `id`, `scope` (bill/event/category_default/global_default), targets mutually exclusive per scope (enforced by CHECK `ck_notification_rules_scope_target`), `advance_days` JSONB list (descending), `is_active`. Seed: one row with `scope=global_default` and `advance_days=[7,3,1,0]`.
- **notification_events** — generated alerts. `id`, `bill_occurrence_id` xor `custom_event_id` (CHECK `ck_notification_events_target`), `trigger_date`, `advance_days`, `channel` (in_app/telegram/email), `status` (pending/delivered/acknowledged/dismissed/failed), `delivered_at`, `acknowledged_at`, `payload_snapshot` JSONB (frozen at creation).

**Rule resolution order** for an occurrence or event: specific `scope=bill` / `scope=event` → `scope=category_default` matching `recurring_bill.category` → `scope=global_default`. First match wins.

**Idempotency contract**: `generate_occurrences`, `mark_overdue`, `compute_pending_notifications` are safe to run on any schedule. The first relies on `UNIQUE(recurring_bill_id, due_date)`; the third de-dups by `(target_kind, target_id, advance_days)`.

**Deduplication strategy**: `source_ref` holds the Gmail Message-ID. Before inserting an email-parsed transaction, check for existing rows with the same `source_ref` + `user_id`. Also fuzzy-match on `amount` + `merchant` + `transaction_date` within a ±1 day window for manual vs email duplicates.

---

## Implementation Roadmap

### Current Status: Phases 7/7a/7b/7c/7d/7e code-complete (operator on-device sign-off pending). Phase 7f (Capture & Forms UX) code-complete 2026-06-12; Phase 7g (Income Model Rework) code-complete 2026-06-13. Phase 6f B0–B16 implemented; SPA retired 2026-06-01; native-only daily use ongoing.

| Phase | Focus | Done When |
|---|---|---|
| **0** ✅ | Dev environment | `docker compose up` → `GET /health` returns 200 |
| **1** ✅ | DB + iPhone Shortcut | Can log a transaction and see it via API |
| **2** | Gmail sync + bank parsers + dedup | Subsumed by Phase 6b |
| **3** | Budgets, goals, weekly report | Pending |
| **4** ✅ | Recurring bills + calendar alerts | `POST /jobs/*` materialize/overdue/notify |
| **5a** ✅ | Users + multi-user foundation | See `11_Phases/Phase-5a-...` (auth model below) |
| **5b** ✅ | Telegram bot + LLM extraction | See `11_Phases/Phase-5b-Telegram-LLM.md` |
| **5c** ⏸ | WhatsApp Cloud API | Deferred until Meta approval |
| **5d** ✅ | Engagement nudges | See `11_Phases/Phase-5d-Engagement-Nudges.md` |
| **6a** ✅ | Conversational query layer | See `11_Phases/Phase-6a-Conversational-Query-Layer.md` |
| **6b** ✅ | Gmail ingestion + reconciliation | See `11_Phases/Phase-6b-Gmail-Ingestion.md` |
| **6c** ✅ | User memory + behavioral profiling | See `11_Phases/Phase-6c-User-Memory.md`. Awaiting production `INSIGHTS_DISPATCHER_ENABLED=true` flip. |
| **6d** ✅ | Onboarding & self-registration | Closed in B13; see "Phase 6d (closed)" below |
| **6e** ✅ | Centro Financiero SPA | Closed at B13; **retired at 6f B16 (`web/` deleted 2026-06-01)**. B14/B15 dropped; privacy export/delete absorbed into Phase 6f B14. |
| **6f** 🚧 | Native iOS app (Expo) | B0–B16 implemented. Replaces SPA (B16 retired `web/` + SWA workflow + cookie auth path on 2026-06-01, operator override of the 4-week-dogfood-first gate). In-app chat (reuses `bot/pipeline.py::process_message()`), receipt photo upload (B6), conversational creation (goals/income/bills/debt), device-code `/login` auth (B3). See `docs/phase-6f-decisions.md`. Remaining: operator daily native-only use. |
| **P7** 🚧 | Affordability / pushback engine + Unified Monthly Cashflow | Code-complete; operator on-device sign-off pending |
| **P7a** 🚧 | Nested envelopes + context-aware pushback | Code-complete; operator on-device sign-off pending |
| **P7b** 🚧 | Accounts CRUD + hard delete, transfers, credit-card clarity | Code-complete 2026-06-11; operator on-device sign-off pending |
| **P7c** 🚧 | UI 2.0: neutral theme + money clarity | Code-complete 2026-06-11; operator on-device sign-off pending |
| **P7d** 🚧 | Goal funding from accounts + goals full CRUD | Code-complete 2026-06-11; operator on-device sign-off pending |
| **P7e** 🚧 | Data foundation: advice trace, snapshots, consent ledger (migration 0031) | Code-complete 2026-06-11; operator on-device sign-off pending |
| **P7f** 🚧 | Capture & Forms UX: account buttons in chat, form polish, envelope indicator, debts in Gastos fijos | Code-complete 2026-06-12; operator on-device sign-off pending |
| **P7g** 🚧 | Income Model Rework: monthly→per-payment storage (quincenal = 2/month), CR aguinaldo + salario escolar on gross prorated by hire date (migration 0032) | Code-complete 2026-06-13; operator on-device sign-off pending |
| **P8** | Beta users | Onboard a second person via Telegram with accurate reports within a week |
| **P9** | SaaS hardening | Multi-tenant auth, billing, compliance, observability |

### Phase Gate Rule

**Do not advance to the next phase until the current phase's "done when" is met.** Skipping ahead creates compounding correctness problems — especially for the AI layer, which is downstream of reliable data pipelines.

---

## Phase 6d (closed) — Onboarding & self-registration

Self-onboarding is the explicit P8 gate. B1–B13 are closed. B13 closed by
operator approval on 2026-05-12 after local Telegram polling + HTTPS tunnel
testing verified the critical B12 path. The original production-dogfood
retrospective remains the historical B12 log and records that this was an
operator override rather than a fully filled production friction log.

- **B1** schema delta — `magic_link_tokens`, `recurring_incomes`, `lazy_detection_events` (migration 0016).
- **B2** onboarding backend — `api/routers/onboarding.py`, `api/routers/recurring_incomes.py`, schemas + `api/services/finance/`.
- **B3** opaque magic-link cookie auth — `<selector>.<verifier>` link format, bcrypt verifier hashes, single-use atomic consumption, 4h HttpOnly `fa_session` JWT cookie. Code in `api/routers/auth.py`, `api/services/auth/`. `current_user` resolves in order: `X-Shortcut-Token` → session cookie → dev `X-User-Id` shim.
- **B4** SPA scaffold in `web/` (Vite/React/Tailwind/Zod/react-hook-form). Vite dev proxy, credentialed axios client, Azure Static Web Apps workflow. Browser smoke pending until SWA is provisioned and `AZURE_STATIC_WEB_APPS_API_TOKEN` is set.
- **B5** `/accounts/new` and `/incomes/new` SPA CRUD + aliases `/onboarding/cuentas`, `/onboarding/ingresos`.
- **B6** `/debts/new` SPA debt creation + alias `/onboarding/deudas`. The form covers French-amortization parameters, client-side cuota preview, Ley 7472 prepago warning, and server schedule pagination via `GET /api/v1/debts/{id}/schedule`.
- **B7** `/bills/new` SPA recurring bills CRUD + alias `/onboarding/gastos`. Reuses existing Phase 4 `recurring_bills`; categories come from Phase 6d `CATEGORIES_CR`, and the backend keeps legacy bill-category strings compatible.
- **B8** lazy detection in the write dispatcher for unknown account/bank hints. The extractor prompt now explicitly fills `account_hint`; deterministic matching uses threshold `0.85`; unknown hints return a create/link prompt and every hint path writes `lazy_detection_events` telemetry.
- **B9** conversational account-creation mini-flow in Redis (`telegram:account_creation:{user_id}`, TTL 10 min). Supports manual `crear cuenta ...`, B8 lazy-triggered name prefill, `/cancel`, inline validation, and replay of the original transaction extraction after the account is created.
- **B10** `/start`, `/help`, and `/setup` onboarding-aware command UX. `/start` and `/help` branch on `GET /api/v1/onboarding/status`; empty/partial users get setup guidance, complete users get a short command reminder, and `/setup` always mints a fresh single-use magic link.
- **B11** E2E coverage for the full hybrid onboarding path and lazy-only path. Tests live in `tests/test_phase_6d_b11_e2e.py`; CI runner is `.github/workflows/phase-6d-e2e.yml`.
- **B12** local dogfood accepted by operator override — local polling + HTTPS
  tunnel exposed the magic-link SPA path, including the Telegram inline URL
  constraint against `localhost`. Historical notes live in
  `docs/phase-6d-retrospective.md`.
- **B13** docs freeze — `docs/phase-6d-decisions.md` is frozen,
  `docs/phase-6e-decisions.md` is stubbed, and `scripts/test_phase_6d.sh`
  is the focused Phase 6d verification entrypoint.

**Commands added/changed:** `/setup` always mints a new single-use setup link;
`/cancel` clears pending write state, clarification state, and the Redis account
creation flow; `/start` and `/help` are onboarding-aware.

**Endpoints added/extended:** `POST /api/v1/auth/magic-link/exchange`;
`GET /api/v1/onboarding/status`; `GET /api/v1/onboarding/categories`;
`GET/POST/PATCH/DELETE /api/v1/accounts`; `GET/POST/PATCH/DELETE
/api/v1/recurring-incomes`; `GET/POST/PATCH/DELETE /api/v1/debts` plus
`GET /api/v1/debts/{id}/schedule`; `GET/POST/PATCH/DELETE
/api/v1/recurring-bills`.

**Tables added/extended:** `magic_link_tokens`, `recurring_incomes`,
`lazy_detection_events`; `accounts` gained `currency` + `initial_balance`;
`debts` gained Phase 6d amortization fields; Phase 4 `recurring_bills` is reused
for fixed bills.

**SPA stack/location:** Vite + React 18 + TypeScript + Tailwind + Zod +
react-hook-form in `web/`. Dev URL is `http://localhost:5173`; local Telegram
button testing requires an HTTPS tunnel for `SPA_BASE_URL` because Telegram
rejects `localhost` inline keyboard URLs. Production SPA URL is configured via
`SPA_BASE_URL` in the runtime environment.

Verification: B11 focused tests `3 passed`; B8+B9+B10+B11+dispatcher/routing command regression `51 passed`; B2+B3+B6+B7+B8+B9+B10+B11 focused tests `59 passed`; `npm run lint` green; Python `py_compile` green; `git diff --check` green. B13 rerun: `scripts/test_phase_6d.sh` passed with `59 passed`, `npm run lint`, and `npm run build` on 2026-05-12. Playwright is locked over Cypress for future browser/mobile E2E harness; current B11 CI E2E is pytest ASGI + bot pipeline plus SPA transfer budget because the repo has no browser runner yet.

---

## Phase 6e (closed) — Centro Financiero SPA

Phase 6e expands the existing `web/` onboarding SPA into the full Centro
Financiero surface. Initial scope is read/edit workflows for existing accounts,
debts, recurring incomes, recurring bills, transactions, and insights. It must
preserve Telegram-first product direction: the web surface is for structured
review and editing, not a replacement for the bot.

Current block status: B1-B13 implemented (B7 + B10 sign-offs received 2026-05-15; B13 install + Lighthouse gate still pending); B14 privacy export/delete UI + E2E is next.

- **B1** decisions + architecture doc expanded in `docs/phase-6e-decisions.md`.
  Daniel approved the B1 recommendations by moving forward on 2026-05-12.
- **B2** backend foundation implemented in Alembic migration `0017` and routers
  under `api/routers/{goals,transfers,categories,dashboard}.py`. Existing
  `goals` was migrated in place. `transactions.category` remains for legacy
  flows while `transactions.category_id` points to `user_categories`.
- **B3** dashboard home route implemented at `web/src/routes/Dashboard.tsx`.
  `/` now renders the Centro Financiero dashboard with period selector,
  balance header, monthly metrics, daily cash-flow sparkline, category chart,
  upcoming payments, top active goals, top memory insights, partial-onboarding
  CTAs, and quick links. Recharts is code-split into
  `DashboardCharts` so the initial bundle remains under the 200KB gzip budget.
- **B4** accounts module full. Backend: per-account balance computation in
  `api/services/accounts.py::compute_account_balances`; `AccountResponse`
  carries `current_balance` and `month_start_balance`; `AccountUpdate` rejects
  immutable `currency`/`initial_balance`; `GET /api/v1/transactions` accepts
  `account_id`, `category_id`, `from_date`, `to_date`, `kind`
  (`all|income|expense|transfer`), `q`; `PATCH /api/v1/transactions/{id}` edits
  `amount`/`merchant`/`description`/`category[_id]`/`transaction_date` and
  hard-rejects shadow rows + transfer legs with 409. SPA: `/accounts` is now
  `AccountsIndex` (list + archived toggle + transfer modal); `/accounts/:id`
  is `AccountDetail` (balance header, inline edit, filtered tx list with edit
  modal, archive/restore); `/accounts/new` is the slimmed creation flow. Both
  new routes lazy-load. Shadow rows show "Pendiente" with no edit button —
  approval still flows through `/aprobar_shadow` in the bot.
- **B5** global transactions module. Backend: migration `0018` adds
  `transactions.archived BOOLEAN` plus partial index
  `ix_transactions_user_date_active`; `GET /api/v1/transactions` grew filters
  `account_ids[]`, `category_ids[]`, `currency`, `min_amount`, `max_amount`,
  `sort_by` (`date|amount|category`), `sort_dir`, `include_archived`,
  `cursor`; response carries `next_cursor` (date sort only); new
  `GET /api/v1/transactions/export` streams CSV with a 50,000-row hard cap
  (413 when exceeded); new `POST /api/v1/transactions/bulk/{archive,restore,
  categorize}` are all-or-nothing and 409 on missing/shadow/transfer rows;
  `PATCH` now rejects archived rows. Balance + dashboard queries
  exclude `archived=true` rows. SPA: `/transactions` is `TransactionsIndex`
  (filter bar, multi-account/category selects, sort presets, bulk-select with
  bulk-archive + bulk-categorize, CSV export); shared
  `web/src/components/transactions/TransactionEditModal.tsx` extracted from
  AccountDetail so both routes use one edit modal. Initial bundle stayed
  ~119.50KB gzip.
- **B6** recurring bills + calendar. Backend: new
  `POST /api/v1/recurring-bills/{id}/mark-paid` body
  `{amount_paid?, paid_at?, account_id?, category_id?, description?,
  idempotency_key}` — auto-resolves the next pending/overdue/partially_paid
  occurrence, creates a manual transaction, and links via the existing
  `recurrence.link_transaction_to_occurrence`. Redis-backed idempotency keyed
  on `bill_mark_paid:{user_id}:{bill_id}:{key}` (TTL 10 min); replays return
  `idempotent_replay=true` without duplicating the transaction. PATCH on
  `is_active` false→true now regenerates occurrences (resume after pause).
  Pause = `PATCH is_active=false` (occurrences stay); Archive = existing
  DELETE (cancels future pending). SPA: `/bills` is now `BillsIndex` (lazy,
  calendar via `react-day-picker` with category-coded dots + 60-day urgency
  list view); `BillActionsModal` handles mark-paid + pause/resume/archive
  from both views. Initial bundle stayed ~119.58KB gzip.
- **B7** debts + amortization + early-payoff calculator. Backend: migration
  `0019` adds `debts.archived BOOLEAN`; `PATCH /debts/{id}` is narrowed via
  `model_config={"extra": "forbid"}` to `name`, `payment_due_day`,
  `account_id`, `notes`, `minimum_payment`, `is_active`, `archived` (extras →
  422). The remaining financial fields (`original_amount`, `current_balance`,
  `interest_rate`, `term_months`, rate/insurance fields) stay immutable
  post-creation; **`minimum_payment` was made editable 2026-06** (the cuota
  changes via rate adjustments / renegotiation / typo-at-entry and drives the
  amortization + affordability math — the router validates it: positive, below
  the balance, covers the monthly interest). New
  `GET /debts/{id}/payoff-scenarios?lump_sum=&extra_monthly=` returns
  per-scenario savings + Ley 7472 penalty info; 422 when neither param.
  DELETE flips both `is_active` and `archived`; `debt_overview` filters
  archived. SPA: `/debts` is `DebtsIndex` (lazy, total/monthly/DTI metrics
  with color grade); `/debts/:id` is `DebtDetail` (lazy, header + three
  tabs: amortización table from existing `/schedule`, browser-side
  Calculadora cancelación, server-blessed Escenarios Ley 7472).
  `web/src/lib/amortization.ts` grew `earlyPayoffLumpSum`,
  `earlyPayoffExtraMonthly`, `calculatePrepaymentPenalty` mirroring the
  backend service. Initial bundle ~120.46KB gzip. Daniel statement-parity
  sign-off received 2026-05-15 — gate closed.
- **B8** recurring incomes module. Backend: migration `0020` adds
  `recurring_incomes.archived BOOLEAN`; `PATCH` narrowed via
  `extra="forbid"` to `name`/`amount`/`frequency`/`next_payment_date`/
  `notes`/`is_active`/`archived` (extras → 422, including `currency` and
  `base_salary_link_id`); `GET /recurring-incomes` query param renamed
  `include_inactive` → `include_archived`; `DELETE` flips both flags. New
  `POST /recurring-incomes/{salary_id}/derive-cycles` atomically creates
  both `aguinaldo` and `salario_escolar` from a single salary, idempotent
  on recall (returns existing rows with `created_*=false`), 400 when the
  target is not `income_type='salary'`. SPA: `/incomes` is `IncomesIndex`
  (lazy, CR-cycle nudge banner when active CRC salary exists but
  aguinaldo/salario_escolar missing, inline-edit per row with derived
  amounts read-only, Pausar/Reanudar/Archivar/Restaurar actions); the
  existing `IncomesNew.tsx` at `/incomes/new` was patched to drop
  `currency` from its inline PATCH body (otherwise the B8 schema
  tightening would 422 it). Initial bundle ~120.50KB gzip.
- **B9** goals module. No schema changes. Backend: new
  `GET /api/v1/goals/{id}/contributions` returns the full list sorted
  `occurred_at desc`; new `GET /api/v1/goals/{id}/forecast` computes
  months-to-target at the last 3 complete calendar months' average pace
  (`has_enough_data=false` when zero contributions in the window;
  already-achieved → `months_to_target=0`). SPA: `/goals` is
  `GoalsIndex` (lazy, status + currency filters); `/goals/:id` is
  `GoalDetail` (lazy, progress header, linked-account read-only saldo
  snapshot, contribution history, forecast banner, actions for
  pausar/reanudar/marcar cumplida/abandonar, AddContributionModal);
  `/goals/new` is `GoalsNew` (lazy, creation form with
  linked_account_id optional). Initial bundle ~120.78KB gzip.
- **B10** memoria SPA. No schema changes. Backend: new
  `GET /api/v1/users/me/insights` returns active rows grouped by
  `GROUP_ORDER`; new `PATCH /api/v1/users/me/insights/{id}` accepts a
  typed `content` payload validated through the `InsightContent`
  discriminated union, rejects computed types + content/type
  mismatches with 400, sets `source='user_override'` + `user_locked=true`
  + `confidence=1.00` and emits a `locked` audit; new
  `DELETE /api/v1/users/me/insights/{id}` and
  `DELETE /api/v1/users/me/insights/group/{group}` reuse the existing
  `delete_insights` service with distinct `deletion_reason` strings.
  SPA: `/memoria` is `MemoryIndex` (lazy) — collapsible per-group
  sections, per-row Edit modal that dispatches by `insight_type` to
  enum radios (risk_posture/decision_style/financial_literacy) or
  text (stated_preference/stated_goal/archetype), per-row + per-group
  delete with confirms, "Borrar toda mi memoria" two-step UX matching
  the bot's `/olvidar todo`, "Descargar mi memoria" triggers the
  export endpoint. The `ModulePlaceholder` helper was removed — every
  Centro Financiero route ships real content now. Memoria-parity
  sign-off vs the bot's `/memoria` received 2026-05-15.
- **B11** categories management SPA. No backend or schema changes —
  Phase 6e B2 already ships the CRUD with auto-seeding, transaction
  counts, the "default cannot be archived" 400 guard, and active-name
  uniqueness. SPA: `/categories` is `CategoriesIndex` (lazy) with an
  inline "Nueva categoría" form, per-row inline edit (name / kind /
  color via `<input type="color">` / icon text), per-row Archivar
  (disabled with tooltip on defaults) / Restaurar. Icon UX is a plain
  text input (no library) — logged as future polish. New SPA helper
  `web/src/api/categories.ts`; the older read-only
  `fetchUserCategories` in `web/src/api/accounts.ts` stays for other
  routes that just need the list. No bot extractor changes — LLM
  emits free-text `category_hint`, not an enum drawn from the API.
- **B12** bot ↔ SPA deep linking. No schema changes — Phase 6d B3
  already shipped `magic_link_tokens.target_path` (migration `0017`)
  and `generate_link(purpose='edit_session', target_path=...)`. New
  `bot/deep_link.py::mint_edit_session_url` wraps `generate_link` with
  swallow-on-fail semantics. `BotReply` grew a `url_buttons: list[
  UrlButton]` field; `bot/handlers.py::_kb` renders both callback and
  URL buttons. Two callsites now offer SPA deep links: post-commit
  ("Ver en Centro Financiero" → `/transactions?highlight={id}`) and
  `/memoria` end ("Editar en SPA" → `/memoria`). `NudgeButton` got an
  optional `url` field + `bot/nudges_send.py::_kb` renders it — the
  third callsite (high-DTI nudge → "Ver tus deudas") needs a new
  evaluator type before it can fire, logged as a Phase 5d-adjacent
  follow-up. SPA `web/src/lib/auth.tsx` honours `?path=` after the
  magic-link exchange via `isSafeRelativePath` validation +
  `navigate(safePath, { replace: true })`.
- **B13** PWA + mobile polish. SPA-only block. New devDeps:
  `vite-plugin-pwa`, `workbox-window`, `@types/node`, `cross-env`.
  `web/vite.config.ts` configures the manifest (name, theme/background
  colors, 192/512/maskable icons) and `generateSW` mode with the
  spec's three caching strategies: app shell (precached + revalidate),
  API responses (`NetworkFirst`, 4s timeout, 24h fallback), images
  (`CacheFirst`, 7d TTL). Icons live in `web/public/icons/`, generated
  by `scripts/generate_pwa_icons.py` (stdlib-only PNG synthesis;
  placeholder design, swap for real brand assets later).
  `web/index.html` got `viewport-fit=cover`, `theme-color`, Apple home-
  screen meta, and `apple-touch-icon`. New shell components under
  `web/src/components/shell/`: `OfflineBanner` (banner-only — does NOT
  block mutations), `InstallBanner` (gated to ≥ 3rd visit via
  `localStorage`; hidden on iOS Safari), `BottomNav` (mobile-only
  persistent nav: Inicio / Cuentas / Deudas / Movimientos / Más, with
  `pb-[env(safe-area-inset-bottom)]` for iPhone home-indicator
  clearance). `App.tsx` wires safe-area-inset padding for both top
  (notch) and bottom (nav + home indicator). Build script gained
  `cross-env NODE_OPTIONS=--experimental-global-webcrypto` so Node 18
  can run workbox-build (Node 20+ makes the flag a no-op). Initial
  bundle 122.82KB gzip (workbox-window runtime); SW + manifest emit
  at build time. **Install + Lighthouse PWA ≥ 80 sign-off is still
  pending — requires real iOS Safari + Android Chrome testing.**
  Pull-to-refresh, swipe-to-archive, and "block mutations when
  offline" deferred (decisions in §20.5).

**B2 endpoints:** `POST/GET/PATCH/DELETE /api/v1/goals[/{id}]`;
`POST /api/v1/goals/{id}/contributions`; legacy
`POST /api/v1/goals/{id}/contribute`; `POST/GET /api/v1/transfers`;
`POST/GET/PATCH /api/v1/categories[/{id}]`;
`GET /api/v1/dashboard/summary?period=month_current|month_prev|ytd`;
`GET /api/v1/dashboard/cash-flow?from=YYYY-MM&to=YYYY-MM`;
`GET /api/v1/dashboard/daily-cash-flow?from=YYYY-MM-DD&to=YYYY-MM-DD`;
`GET /api/v1/users/me/insights/summary`.

**B2 tables/columns:** `goal_contributions`, `transfers`, `user_categories`,
`currency_rates`, `transactions.transfer_id`, `transactions.category_id`,
`users.display_currency`, `accounts.archived`,
`magic_link_tokens.target_path`, and materialized views
`mv_monthly_summary_by_user`, `mv_yearly_summary_by_user`.

**B2 verification:** Alembic `0017 (head)` applied locally. Focused tests
`tests/test_phase_6e_b2_backend.py` passed (`4 passed`). Regression slice
`tests/test_phase_6d_b2_endpoints.py`, `tests/test_phase_6d_b3_magic_link.py`,
and `tests/test_phase_6d_b10_welcome.py` passed (`34 passed`). Syntax compile
passed with `PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache`.

**B3 verification:** `npm run lint` passed, `npm run build` passed
(`index` JS gzip ~119KB, `DashboardCharts` chunk gzip ~106KB), Python compile
passed, `tests/test_phase_6e_b2_backend.py` passed (`4 passed`), and the 6d
onboarding/auth regression slice passed (`34 passed`).

**B4 verification:** focused tests `tests/test_phase_6e_b4_accounts.py` plus
the 6e B2 + 6d regression slice passed (`43 passed`). `npm run lint` passed,
`npm run build` passed (`index` gzip ~119KB unchanged, new `AccountsIndex`
chunk gzip ~2.6KB, new `AccountDetail` chunk gzip ~4KB). Python compile passed.
`alembic current` returned `0017 (head)` — no schema change in B4.

**B5 verification:** focused tests `tests/test_phase_6e_b5_transactions.py`
plus the 6e B4 + B2 + 6d regression slice passed (`52 passed`).
`npm run lint` passed, `npm run build` passed (`index` gzip ~119.50KB stable,
new `TransactionsIndex` chunk gzip ~3.43KB, new shared
`TransactionEditModal` chunk gzip ~1.89KB, `AccountDetail` shrank to ~3.18KB
after modal extraction). Python compile passed. `alembic current` returned
`0018 (head)` after migration `0018_phase6e_b5_transactions_archived.py`.

**B6 verification:** focused tests `tests/test_phase_6e_b6_bills.py` plus the
6e B5 + B4 + B2 + 6d regression slice passed (`58 passed`). `npm run lint`
passed, `npm run build` passed (`index` gzip ~119.58KB stable, new
`BillsIndex` chunk gzip ~24.16KB including react-day-picker, BillsIndex CSS
gzip ~1.68KB). Python compile passed. `alembic current` still `0018 (head)` —
no schema change in B6. New SPA dep: `react-day-picker` (locked in decision
3.5).

**B7 verification:** focused tests `tests/test_phase_6e_b7_debts.py` plus the
6e B6 + B5 + B4 + B2 + 6d regression slice passed (`66 passed`). `npm run lint`
passed, `npm run build` passed (`index` gzip ~120.46KB still under budget,
new `DebtsIndex` chunk gzip ~1.96KB, new `DebtDetail` chunk gzip ~4.26KB).
Python compile passed. `alembic current` returned `0019 (head)` after
`0019_phase6e_b7_debts_archived.py`. Daniel statement-parity sign-off
received on 2026-05-15.

**B8 verification:** focused tests `tests/test_phase_6e_b8_incomes.py` plus the
6e B7 + B6 + B5 + B4 + B2 + 6d regression slice passed (`72 passed`).
`npm run lint` passed, `npm run build` passed (`index` gzip ~120.50KB stable,
new `IncomesIndex` chunk gzip ~3.60KB). Python compile passed. `alembic
current` returned `0020 (head)` after
`0020_phase6e_b8_recurring_incomes_archived.py`.

**B9 verification:** focused tests `tests/test_phase_6e_b9_goals.py` plus the
6e B8 + B7 + B6 + B5 + B4 + B2 + 6d B2 regression slice passed (`58 passed`).
`npm run lint` passed, `npm run build` passed (`index` gzip ~120.78KB still
under budget, new `GoalsIndex` chunk gzip ~1.53KB, new `GoalsNew` chunk gzip
~1.59KB, new `GoalDetail` chunk gzip ~2.96KB). `alembic current` still `0020
(head)` — no schema change in B9.

**B10 verification:** focused tests `tests/test_phase_6e_b10_insights.py` plus
the 6e B9 + B8 + B7 + B6 + B5 + B4 + B2 regression slice passed (`50 passed`).
`npm run lint` passed, `npm run build` passed (`index` gzip ~120.77KB stable,
new `MemoryIndex` chunk gzip ~3.99KB). `alembic current` still `0020 (head)` —
no schema change in B10. Memoria-parity sign-off vs the bot's `/memoria`
received 2026-05-15.

**B11 verification:** focused tests `tests/test_phase_6e_b11_categories.py`
plus the full 6e slice (B10 + B9 + B8 + B7 + B6 + B5 + B4 + B2) passed
(`53 passed`). `npm run lint` passed, `npm run build` passed (`index` gzip
~120.83KB stable, new `CategoriesIndex` chunk gzip ~2.77KB). `alembic current`
still `0020 (head)` — no backend changes in B11.

**B12 verification:** focused tests `tests/test_phase_6e_b12_deep_link.py`
plus the 6e B11 + B10 + B9 + B8 + B7 + B2 + 6d B2 slice passed (`54 passed`).
`npm run lint` passed, `npm run build` passed (`index` gzip ~120.89KB
stable; no new SPA chunks — the auth path change rolls into `index`).
Python compile passed. `alembic current` still `0020 (head)` — no schema
change in B12.

**B13 verification:** `npm run lint` passed; `npm run build` passed and
emitted `dist/sw.js`, `dist/workbox-*.js`, and `dist/manifest.webmanifest`
(workbox precache: 30 entries / 1016.97 KiB). Initial `index` JS gzip
~122.82KB (workbox-window runtime). Backend regression slice (B12 + B11 +
B10 + B9 + B8 + B7 + B2) passed (`39 passed`); no backend changes in B13.
`alembic current` still `0020 (head)`. Install + Lighthouse PWA ≥ 80
sign-off still pending — requires real iOS Safari + Android Chrome
testing.

**Phase 6e closure (2026-05-22):** Operator concluded the SPA UX is not
acceptable for daily use (bot ↔ web context-switching, magic-link round-trip
on mobile, web-feel vs native-feel). Phase 6e closes at B13. B14 (privacy
export/delete UI) and B15 (polish) are dropped on the SPA side — privacy
export/delete is absorbed into Phase 6f B14 on the native app. The SPA stays
deployed but frozen until Phase 6f reaches parity and retires `web/` at 6f
B16. Decision note: `~/Finance_project/30_Projects/Finance-Agent/05_Decisions/Decision - Native iOS App Replaces SPA.md`.

---

## Phase 6f (active) — Native iOS App (Expo)

Phase 6f replaces the frozen Phase 6e SPA with a React Native (Expo, managed
workflow) iOS app. Reaches parity with the SPA's read/edit surface and adds
two capabilities the SPA did not have: an in-app chat that reuses the
existing `bot/pipeline.py::process_message()` so the operator no longer
needs Telegram for daily capture or queries, and receipt photo upload routed
through Claude vision.

**Canonical source of truth:** `docs/phase-6f-decisions.md` (locked decisions,
architecture, block-by-block status). This section is a high-level summary.

**Stack:** Expo SDK 54 + Node 20 LTS, TypeScript, React Navigation 7,
TanStack Query 5, React Hook Form + Zod, Axios with bearer-token
interceptor, `expo-secure-store`, `expo-image-picker` (B6),
`expo-web-browser`, `expo-linking`. iOS-only. SDK version must match
App Store Expo Go release.

**Workspace:** new `mobile/` directory at repo root, sibling to `web/`,
`api/`, `bot/`. SPA `web/` remains in tree until Phase 6f B16.

**Backend additions (landed in B2 + B3):**
- `POST /api/v1/auth/magic-link/exchange` returns
  `{token, expires_at, user_id, email, full_name}` in the response body.
  (B2 originally also set the `fa_session` cookie for SPA back-compat;
  **B16 removed the cookie** when the SPA retired — bearer-only now.) — B2
- `api/dependencies.py::current_user` gains a `Authorization: Bearer <jwt>`
  resolution branch. Order — `X-Shortcut-Token` → bearer JWT → dev
  `X-User-Id` shim. (B2 had a cookie branch between bearer and shim;
  **B16 removed it** with the SPA.) — B2
- New router `api/routers/chat.py`: `POST /chat/message` (calls
  `bot/pipeline.py::process_message()` directly, returns serialized
  `BotReply`). — B2
- `POST /chat/reset` ("Nueva conversación" in the app) — clears all durable
  conversational state for the caller: pending write, clarification, the
  account-creation flow, the memory-edit flow, and the LLM query history
  (mirrors the bot's `/cancel` + `/clear`). The native Chat screen clears its
  local message list and calls this, so a stuck flow (e.g. a stale
  account-creation prompt) can't leak across conversations.
- `POST /chat/image` (multipart) routes through
  `api/services/llm_extractor/vision.py` (Haiku → Sonnet retry on
  confidence < 0.65). Same `ExtractionResult` the text path produces;
  write dispatcher runs identically. Image stored base64-inline in
  `llm_extractions.extraction["image_b64"]` (4MB cap pre-base64;
  Azure Blob is P8 work). `python-multipart` added as dep. — B6
- New service `api/services/auth/device_code.py` mints + atomically
  consumes 6-character alphanumeric Redis-backed codes (alphabet
  `[A-HJ-NP-Z2-9]`, TTL 5 min, `auth:device_code:{code}` key). Bot
  command `/login` (alias `/iniciar`) replies with the code. New
  endpoint `POST /api/v1/auth/device-code/exchange` returns the same
  shape as magic-link exchange but does NOT set a cookie (native-only).
  Suspended users return 401 (not 403) to avoid leaking account state.
  — B3
- `bot/pipeline.py::process_message()` is NOT modified. It is already
  channel-agnostic.

**Auth flow (native, B3 final shape):**
1. Operator runs `/login` in Telegram.
2. Bot replies with a 6-character code (`<code>` in monospace).
3. Operator types/pastes the code in the app's Login screen.
4. App auto-submits at 6 valid chars, calls
   `POST /auth/device-code/exchange`, gets a session JWT.
5. JWT stored in `expo-secure-store`. Subsequent calls send
   `Authorization: Bearer <jwt>`.
6. `useMagicLinkListener` stays mounted in `App.tsx` as a silent
   fallback so a future bot `ledgercr://exchange?token=...` deep link
   (B15) still auto-signs in.

**Distribution:** Expo dev build via Expo Go sideloaded onto operator's
iPhone. No TestFlight, no App Store, no Apple Developer Program
enrollment until P8.

**Nudges + shadow approvals:** Telegram-only delivery during Phase 6f.
`users.expo_push_token` column is added in B15 as schema-only prep for P8.

**Block plan (B0 → B16):**

- **B0 ✅ (2026-05-22):** decisions + scaffolding docs (this section,
  vault decision note, `docs/phase-6f-decisions.md`, vault
  `Phase-6f-Native-iOS-App.md`, Roadmap update). No code shipped.
- **B1 ✅ (2026-05-22, reved same day):** Expo workspace at `mobile/`,
  pinned to SDK 54 + Node 20. iPhone device test confirmed 2026-05-23.
- **B2 ✅ (2026-05-23):** backend bearer-token auth + `POST /chat/message`.
  10 focused tests + 27 regression tests green.
- **B3 ✅ (2026-05-26):** native auth shell + 5-tab nav. Original
  magic-link UI replaced mid-block by **device-code login** (`/login`
  → 6-char code → JWT). 8 focused tests green. Operator iPhone login
  confirmed.
- **B4 ✅ (2026-05-27):** Chat UI screen. `mobile/src/screens/Chat.tsx`
  — inverted-bottom FlatList, `KeyboardAvoidingView`, user/bot bubbles,
  confirm/cancel chips (tap posts label back through pipeline), URL chips
  (`expo-web-browser`), loading spinner, empty-state hint.
  `mobile/src/api/chat.ts` API helper. `AppNavigator` Chat tab wired to
  real screen. `npx tsc --noEmit` clean; B2+B3 backend tests 18 passed.
  Operator on-device query test confirmed.
- **B5 ✅ (2026-05-27):** Write transaction capture parity. Bug fix:
  `_text_is_confirmation` in `bot/pipeline.py` now strips emoji/punctuation
  via `_NON_WORD_RE` before matching, so button labels like `"Sí ✅"` and
  `"No ❌"` route correctly through the confirm/cancel path. Mobile: chips
  disable after one tap (`usedChips` Set in `ChatScreen`). 3 new E2E tests
  in `tests/test_phase_6f_b5_chat_write.py` (confirm-with-emoji, cancel-
  with-emoji, plain-sí regression). 21 backend tests passed, TypeScript
  clean. **Dogfood gate entered: operator commits to native-only capture
  for 7 days (until 2026-06-03) before B6 starts.**
- **B6 ✅ (2026-05-27):** Receipt photo upload. Backend: `api/services/llm_extractor/vision.py`
  (`extract_vision()`, Haiku-first, Sonnet retry on confidence < 0.65); `POST /chat/image`
  multipart endpoint (4MB cap, MIME whitelist jpeg/png/webp/gif); `process_message()` gains
  `image_bytes`/`image_media_type`/`vision_model` params with vision fast-path that skips
  text branches; `LLMClient` protocol widens `user_message` to `str | list[dict]` for
  content blocks; `FixtureLLMClient` handles unhashable list safely. `python-multipart` dep
  added. Mobile: `expo-image-picker` installed + permission strings in `app.json`;
  camera icon (📷) in input bar; thumbnail bubble for sent receipts; `postChatImage()`
  in `chat.ts`; shared `isPending` blocks both text and image mutations. 6 focused tests
  in `tests/test_phase_6f_b6_vision.py`; 27 backend tests passed; TypeScript clean.
- **B7 ✅ (2026-05-27):** Dashboard + full app retheme. New `mobile/src/theme.ts` design system:
  warm parchment palette (`#F5F0E8` bg, earth tones), Bauhaus approach (form follows function,
  details on demand via expandable sections, Feather icons from `@expo/vector-icons`, red used
  only for expense/overdue). Dashboard (`DashboardScreen`): period picker (este mes / mes ant. /
  año), total balance card, income/expense/net metrics row (Feather trending icons), expandable
  category breakdown (proportional bar built from flex View — no chart lib), expandable upcoming
  bills (from `/calendar/upcoming`). Login, Chat, PlaceholderScreen, AppNavigator all rethemed.
  Tab bar uses Feather icons (home, message-circle, credit-card, list, more-horizontal).
  `@expo/vector-icons` + `mobile/src/api/dashboard.ts` added. TypeScript clean, 27 backend tests passed.
- **B8 ✅ (2026-05-27):** Accounts module. `mobile/src/api/accounts.ts` (full API
  helper: AccountResponse, AccountCreate, AccountUpdate, TransactionListResponse,
  ACCOUNT_TYPE_LABELS, fetchAccounts, fetchAccount, createAccount, updateAccount,
  archiveAccount, fetchAccountTransactions). `mobile/src/navigation/AccountsNavigator.tsx`
  (stack: AccountsList → AccountDetail → AccountCreate modal). `AccountsScreen`:
  custom header, consolidated balance strip (sum active accounts), AccountCard with
  current balance + month diff (green/red), archived toggle, pull-to-refresh.
  `AccountDetailScreen`: balance hero, month diff, cursor-paginated transaction list
  via `useInfiniteQuery`, archive/restore with Alert confirm. `AccountCreateScreen`:
  name input, segmented type picker (4 types), CRC/USD currency toggle, initial
  balance, POST on submit. AppNavigator Cuentas tab wired to `AccountsNavigator`.
  TypeScript clean, 24 backend tests passed.
- **B9 ✅ (2026-05-27):** Transactions module. `mobile/src/api/transactions.ts`
  — canonical source for `TransactionResponse` + `TransactionListResponse` (moved
  from `accounts.ts` which now re-exports them); `TransactionFilters` typed object
  with `kind/accountId/includeArchived`; `DEFAULT_FILTERS` constant; `fetchTransactions`
  with sort locked to `date desc` (backend only emits `next_cursor` on date sort —
  switching sort would silently corrupt pagination); `archiveTransaction` /
  `restoreTransaction` via `POST /transactions/bulk/{archive,restore}`.
  `TransactionsNavigator`: stack TransactionsList → TransactionDetail.
  `TransactionsScreen`: kind pills (Todo/Ingresos/Egresos) always visible; account
  picker expands inline behind filter icon; `useInfiniteQuery` cursor-paginated
  FlatList; pull-to-refresh; shadow rows show "Pendiente" badge. `TransactionDetailScreen`:
  amount hero (colored by sign), full detail card (date, merchant, description,
  category, account name from cache, source), archive/restore bottom bar, shadow rows
  show read-only Telegram notice instead of archive button. CSV export and bulk-select
  deferred (browser-native patterns, not mobile-native). TypeScript clean, 24 backend
  tests passed.
- **B10 ✅ (2026-05-30):** Bills + calendar module. `mobile/src/api/bills.ts`
  (`RecurringBillResponse`, `BillOccurrenceResponse`, `MarkPaidPayload`,
  `FREQUENCY_LABELS`, `ACTIONABLE_STATUSES`, client-side `newIdempotencyKey()`;
  `fetchRecurringBills`, `fetchBillOccurrences`, `markBillPaid`, `pauseBill`/
  `resumeBill` via `PATCH is_active`, `archiveBill` via DELETE). `BillsScreen`
  (occurrence/urgency list) + `BillDetailScreen` (mark-paid with a client-minted
  idempotency key, pause/resume/archive). Reuses Phase 4 `/recurring-bills` +
  `/bill-occurrences` and Phase 6e B6 `POST /recurring-bills/{id}/mark-paid`.
- **B11 ✅ (2026-05-30):** Debts module. `mobile/src/api/debts.ts`
  (`DebtSummary`, `DebtResponse`, `DebtOverview`, `AmortizationSchedule`,
  `PayoffScenariosResponse`; `fetchDebts`, `fetchDebt`, `fetchDebtOverview`,
  `fetchDebtSchedule`, `fetchPayoffScenarios`, `updateDebt`, `archiveDebt`).
  `DebtsScreen` (overview metrics) + `DebtDetailScreen` (amortization table +
  payoff calculator + Ley 7472 scenarios). Reuses Phase 6e B7 `/debts/overview`,
  `/debts/{id}/schedule`, `/debts/{id}/payoff-scenarios`; PATCH whitelist
  (`extra="forbid"`) — `minimum_payment` added as editable 2026-06 (now 7
  fields). Mobile debt CRUD (create from the Deudas tab + `DebtEditModal`
  editing name/día de pago/cuota/notas + pause/resume + archive/restore)
  landed 2026-06.
- **B12 ✅ (2026-05-30):** Incomes module. `mobile/src/api/incomes.ts`
  (`RecurringIncomeResponse`, `IncomeType`, `DeriveCyclesResponse`;
  `fetchRecurringIncomes`, `updateRecurringIncome`, pause/resume/archive,
  `deriveIncomeCycles`). `IncomesScreen` with the CR-cycle (aguinaldo +
  salario_escolar) derive flow. Reuses Phase 6e B8 `/recurring-incomes` +
  `POST /recurring-incomes/{salary_id}/derive-cycles`.
- **B13 ✅ (2026-05-30):** Goals module. `mobile/src/api/goals.ts`
  (`GoalResponse`, `GoalContributionResponse`, `GoalForecastResponse`;
  `fetchGoals`, `fetchGoal`, `fetchGoalContributions`, `fetchGoalForecast`,
  `addGoalContribution`, pause/resume/markAchieved/abandon). `GoalsScreen`
  (status filter) + `GoalDetailScreen` (progress + forecast + contribution
  history). Reuses Phase 6e B9 `/goals/{id}/contributions` +
  `/goals/{id}/forecast`.
- **B14 ✅ (2026-05-30):** Categories + memoria (+ privacy). `categories.ts`
  (CRUD + archive/restore, `PRESET_COLORS`) → `CategoriesScreen`; `memoria.ts`
  (list, delete one/group/all, export) → `MemoryScreen`. The privacy
  export/delete-all absorbed from Phase 6e B14 is satisfied by the memoria
  export + "borrar todo" controls — there is **no** separate `PrivacySettings`
  screen. **Parity gaps vs the SPA, deferred:** native memoria is read + delete
  + export only — the per-type user-override edit (SPA's `PATCH
  /users/me/insights/{id}`) still flows through the bot's `/editar_memoria`;
  category `icon` is a free-text field (no picker library, matching the SPA).
  Reuses Phase 6e B10/B11 endpoints; no backend changes.

  All six B10–B14 modules are wired through
  `mobile/src/navigation/MasNavigator.tsx` (hub `MasHubScreen` → per-module
  native stacks) and reachable from the "Más" tab.

  **Verification (2026-05-30):** `bash scripts/test_phase_6f.sh` green — mobile
  `tsc --noEmit` clean; **72 backend tests pass** across B2/B3/B5/B6/B10–B14
  (`test_phase_6f_b10_bills.py`, `_b11_debts.py`, `_b12_incomes.py`,
  `_b13_goals.py`, `_b14_categories.py`, `_b14_memoria.py`). No schema changes —
  B10–B14 reuse the Phase 6e backend; `alembic current` still `0020 (head)`.
  Note: B10–B14 landed in a single bulk commit (`dcfc008`) without the per-block
  iPhone confirmation the earlier 6f blocks recorded — **operator on-device
  sign-off is still pending.** A `b14_memoria` test fixture (malformed
  `spending_pattern` content) was corrected to valid `SpendingPatternContent`
  during reconciliation.
- **B15** 🚧 (partial, 2026-05-30): polish + deep links + Sentry +
  `users.expo_push_token`. **Landed:**
  - **`users.expo_push_token`** — migration `0021`, nullable `String(255)` on
    `users`; model field added. Schema-only (no delivery worker; nudges stay
    Telegram-only). `alembic current` → `0021 (head)`.
  - **Bot `ledgercr://` deep link** — new `native_app_scheme` config
    (default `ledgercr`); `bot/deep_link.py::mint_native_deep_link` mints the
    same single-use `<selector>.<verifier>` magic-link token formatted as
    `ledgercr://exchange?token=...[&path=...]` (reuses `purpose='edit_session'`;
    swallow-on-fail → `None`). `/login` now appends the link below the 6-char
    code (`LOGIN_DEEP_LINK_SUFFIX`); tapping it feeds the already-mounted
    native `useMagicLinkListener` (B3). The 6-char code stays the primary path.
  - **Sentry scaffold** — `mobile/src/lib/observability.ts`
    (`initObservability`/`captureError`, env-gated on `EXPO_PUBLIC_SENTRY_DSN`)
    + `mobile/src/components/ErrorBoundary.tsx` wrapping the app.
    **Deliberately NOT importing `@sentry/react-native`** — the operator runs
    Expo Go, which can't load Sentry's native module; an eager import would
    red-screen the working app. Live Sentry lands with the EAS dev build at P8
    (decision §3.8). The scaffold is the one-file swap point.
  - Verification: `bash scripts/test_phase_6f.sh` green — mobile `tsc --noEmit`
    clean; 103 backend tests pass (Phase 6f B2/B3/B5/B6/B10–B15 + 6d
    regression). New tests: `tests/test_phase_6f_b15_deep_link.py` (mint→
    exchange→JWT, `target_path` passthrough, single-use 401, expo_push_token
    column).

  **Deferred (the rest of B15):** rewiring the bot's "Ver en Centro Financiero"
  buttons to native + a native path→screen router (gated on SPA coexistence
  until B16 + inline URL buttons require https so `ledgercr://` can't be a
  button — only tappable message text); universal links (`https://…/exchange`)
  pending operator DNS/hostname; live `@sentry/react-native` (EAS dev build,
  P8). **Operator on-device sign-off pending** for the `/login` deep-link
  tappability in Telegram iOS.
- **B16 ✅ (2026-06-01)**: SPA retirement. `web/` deleted; Azure Static Web
  Apps workflow + the Phase-6d-E2E workflow + `scripts/generate_pwa_icons.py`
  removed; `scripts/test_phase_6d.sh` trimmed of the SPA lint/build + B11 E2E.
  **Cookie auth path removed**: no `Set-Cookie` on exchange, no cookie branch
  in `current_user`, `session_cookie_*` settings dropped (`session_cookie_ttl_s`
  → `session_ttl_s`); both exchange endpoints are bearer-only. SPA deep links
  removed (`mint_edit_session_url` + the "Ver en Centro Financiero" / "Editar en
  SPA" buttons); `bot/deep_link.py` keeps only `mint_native_deep_link`.
  Onboarding `/setup` + `/start`/`/help` now send a native `ledgercr://` deep
  link as tappable message text (https inline buttons can't carry a custom
  scheme). `GeneratedLink.url` (SPA URL) dropped — callers build their own.
  CORS middleware + `spa_base_url`/`spa_cors_origins` removed (native + Shortcut
  are non-browser clients). The amortization parity test repointed to
  `mobile/src/lib/amortization.ts`. **Operator override** of the documented
  "4-week dogfood first" gate — the SPA was retired at the start of the
  native-only period rather than the end; native-only daily use is ongoing.

**B16 readiness — native↔SPA parity backlog (audit 2026-05-30):** before `web/`
can be deleted, the native app must cover what the SPA does. Audit of SPA routes
vs native screens/API:
- **Transactions edit** — ✅ **closed 2026-05-30.** Fixed a shipped bug:
  `archiveTransaction`/`restoreTransaction` posted `{ ids }` but the
  `TransactionBulkArchive` schema needs `transaction_ids` (was 422-ing — archive
  was broken in the native app since B9). Added `updateTransaction` +
  `mobile/src/components/TransactionEditModal.tsx` (amount magnitude w/ sign
  preserved, merchant, description, free-text category, `YYYY-MM-DD` date) wired
  into `TransactionDetailScreen` for confirmed/non-archived rows; backend 409s
  (shadow/transfer/archived) surface in the modal. tsc clean; on-device sign-off
  pending. No backend changes (PATCH `/transactions/{id}` already existed).
- **Create flows — RESOLVED: conversational, NOT ported forms.** Decision
  2026-05-30 (`05_Decisions/Decision - Conversational Creation Over Forms.md`):
  the native app's primary verb is conversation, so the SPA's `BillsNew`/
  `DebtsNew`/`IncomesNew`/`GoalsNew` forms are **not** ported. Instead, goals /
  recurring bills / recurring incomes are created in the in-app **chat** (extend
  the Phase 6d B9 conversational-account pattern; LLM proposes structured fields,
  deterministic code writes — "LLM extracts; rules decide" preserved). **Debt**
  is chat-initiated → a focused pre-filled native sheet for the amortization
  fields (honors the 6d "debt forms due to field complexity" decision). This is
  new write-dispatcher work, sequenced goals → income → bill → debt; structured
  "New" screens are not built. B16 parity = the user can *do* everything, not
  that every SPA form has a native twin.
  - **Goals — ✅ first slice implemented 2026-05-31.** New `Intent.CREATE_GOAL`
    + `goal_name`/`goal_target_amount`/`goal_target_date` fields on
    `ExtractionResult` (+ tool schema + prompt guidance/examples).
    `telegram_dispatcher._dispatch_create_goal` validates fields (clarifies for
    a missing amount/name), resolves the date hint server-side
    (`_resolve_goal_target_date`: ISO, `YYYY-MM`, "en N meses/años", "fin de
    año", Spanish month names), and proposes with a **monthly-needed forecast**
    in the summary. Confirm → `commit._commit_goal` writes a `Goal` row
    (mirrors `routers/goals.py::create_goal`). `merge_reply` handles the
    `goal_target_amount`/`goal_name` clarification replies; `_parse_amount_es`
    grew "millones" support (×1\_000\_000). The deterministic path is fully
    tested (`tests/test_phase_6f_chat_create_goal.py`, 9 cases) via
    FixtureLLMClient + direct dispatch. **Operator on-device sign-off received
    2026-05-31** (real LLM routes the goal phrasing to create_goal correctly).
  - **Income — ✅ second slice implemented 2026-05-31.** New
    `Intent.CREATE_INCOME` + `income_type`/`income_frequency`/`income_next_date`
    fields (amount/currency reused). `_dispatch_create_income` clarifies each
    NOT-NULL field in turn (amount → frequency → next_payment_date), resolves
    the date hint (`_resolve_next_payment_date`: ISO, "el N"/"día N", "fin de
    mes", "hoy/mañana"), defaults `income_type`→salary + a Spanish name, and
    proposes. Confirm → `commit._commit_income` writes a `RecurringIncome`
    (non-derived; mirrors `routers/recurring_incomes.py`). The aguinaldo +
    salario_escolar **derive stays the Incomes screen's one-tap action** (chat
    creates, structured screen acts); the CRC-salary confirmation points there.
    `merge_reply` + `_parse_frequency_es` handle the frequency/date
    clarifications. Tested (`tests/test_phase_6f_chat_create_income.py`, 10
    cases). **LLM classification (recurring create_income vs one-time log_income)
    needs operator on-device sign-off.**
  - **Bills — ✅ third slice implemented 2026-05-31.** New `Intent.CREATE_BILL`
    + `bill_name`/`bill_frequency`/`bill_day_of_month` (amount→amount_expected,
    currency, category_hint→category reused). `_dispatch_create_bill` clarifies
    amount → frequency → name; the required+validated `category` falls back to a
    valid default (`servicios`) when the hint isn't a CR category;
    `day_of_month` optional (`generate_occurrences` anchors on start_date.day);
    `start_date` defaults to today. Confirm → `commit._commit_bill` creates the
    `RecurringBill` **and runs `recurrence.generate_occurrences`** (mirrors
    `routers/recurring_bills.py`, so the calendar populates). `_parse_bill_
    frequency_es` covers all 7 cadences (custom/RRULE excluded). Tested
    (`tests/test_phase_6f_chat_create_bill.py`, 9 cases). **LLM classification
    (recurring create_bill vs one-time log_expense) needs on-device sign-off.**
  - **Debt — ✅ closed 2026-06-01 (D1–D4).** Operator on-device sign-off
    received 2026-06-01; this closes the conversational-creation backlog
    (goals/income/bills/debt all have working create paths). The
    hybrid: chat-initiated (`Intent.CREATE_DEBT`, light extraction) → native
    `DebtCreateScreen` (pre-filled, live cuota preview via a lifted
    `mobile/src/lib/amortization.ts`, Ley 7472 warning). Adds PDF term
    extraction so a user who doesn't know the interest rate uploads the loan
    contract. **No-rate fallback** (D3 form copy): voseo *"Por favor llamá a tu
    entidad financiera para confirmar la tasa de interés. Cuando la tengás,
    volvé y registramos el préstamo."* Commit reuses the existing `POST /debts`
    (no new write path). Full spec: vault `Decision - Debt Creation - Hybrid
    Form Plus PDF Term Extraction`. Sub-plan D1–D4:
    - **D1 ✅** `Intent.CREATE_DEBT` + light `debt_*` fields
      (`debt_name`/`debt_principal`/`debt_interest_rate` [percent]/
      `debt_term_months`/`debt_lender`) + prompt guidance/examples. Unlike the
      other 3 creators, debt does **not** confirm/commit in chat: new
      `OpenScreenAction` dispatcher result → `BotReply.open_screen` /
      `ChatMessageResponse.open_screen` (`{screen:"debt_create", prefill}`); the
      native client opens the form pre-filled. Telegram ignores `open_screen`.
    - **D2 ✅** `POST /debts/parse-document` (multipart PDF, 415 non-PDF, 413
      over 4 MB) → `api/services/llm_extractor/document.py::extract_debt_terms`
      (Claude `document` block, Haiku→Sonnet retry < 0.65, mirrors `vision.py`,
      logs to `llm_extractions` with `pdf_b64` inline) → validated
      `DebtTermsExtraction` (interest_rate as 0–1 fraction). Returns terms to
      pre-fill the form; does **not** create the debt.
    - **D3 ✅** mobile: lifted `mobile/src/lib/amortization.ts`; new
      `mobile/src/screens/DebtCreateScreen.tsx` (prefill consumed from the chat
      handoff; rate entered as %→converted to 0–1 fraction on submit; live cuota
      preview + "usar como cuota"; Ley 7472 note; **PDF upload** via
      `expo-document-picker` → `parseDebtDocument` fills fields, low-confidence
      note < 0.65; **no-rate fallback** blocks submit + shows the voseo "llamá a
      tu entidad financiera" card). `mobile/src/api/{chat,debts}.ts` grew
      `open_screen`/`DebtPrefill`, `createDebt`, `DebtTermsExtraction`,
      `parseDebtDocument`. New `mobile/src/navigation/ChatNavigator.tsx` wraps the
      Chat tab in a stack (Chat → DebtCreate modal); `ChatScreen` navigates on
      `open_screen.screen==="debt_create"`; `AppNavigator` Chat tab → `ChatNavigator`.
      Submits fixed-rate, brand-new loans (variable-rate + refinance not exposed
      in v1). New dep `expo-document-picker@~14.0.8` (SDK 54 aligned) + app.json plugin.
    - **D4 ✅** E2E chat→form→(PDF or manual)→`POST /debts` verified; operator
      on-device sign-off received 2026-06-01 (no native CI — `tsc --noEmit` is
      the only automated guard).
    Verification: `tests/test_phase_6f_chat_create_debt.py` (4) +
    `tests/test_phase_6f_debt_parse_document.py` (6); full `scripts/test_phase_6f.sh`
    green (mobile `tsc --noEmit` + 111 focused + 27 regression); `alembic current`
    still `0021 (head)` (no migration).
- **Memoria edit** — stays conversational (the bot's `/editar_memoria`); native
  `MemoryScreen` remains list/delete/export. No new edit form (consistent with
  the chat-first decision).
- Accounts + Categories: at parity. Transaction edit: closed (above).

**Hard rules for Phase 6f:**

- The native chat must NOT introduce a parallel extractor or dispatcher. It
  reuses `process_message()` directly. The LLM-never-writes-financial-data
  rule is preserved because the same deterministic write dispatcher commits.
- The bearer-token branch in `current_user` MUST decode through the same
  JWT codec the cookie path uses. No second secret, no second token shape.
- Receipt vision extraction MUST emit the same `ExtractionResult` Pydantic
  shape as text extraction so the write dispatcher consumes it identically.
- Redis keys keep the `telegram:` prefix. They are transport-neutral
  historically; renaming would break existing Telegram clients and is not
  worth the churn. Tech debt entry tracked.
- All user-facing copy stays Spanish (voseo, CR).
- `transaction_date` stays `DATE`. The in-app chat must reject intra-day
  claims cleanly, same as the bot.

---

## Native Gmail (post-6f) — connect + senders + shadow review

Brings Gmail ingestion (Phase 6b, previously Telegram-only) to the native app.
Backend (G1) done + verified; mobile (G2–G4) code-complete, **operator
on-device sign-off pending**. No schema change (`alembic` still `0021`).

- **Shared service** `api/services/gmail/shadow_review.py`:
  `list_shadow` / `confirm_shadow(items=ShadowEdit[]|None)` /
  `discard_shadow(ids|None)`. The bot's `/aprobar_shadow` + `/rechazar_shadow`
  were repointed to it (DRY) — `items=None`/`ids=None` = "all". Shadow rows
  can't be PATCHed (409 by design), so the native review applies per-row edits
  **atomically inside confirm**.
- **REST** (new in `api/routers/gmail.py`, `current_user` auth; OAuth
  `/oauth/start` + `/status` already existed): `POST /gmail/scan` (alias of
  `enqueue_backfill`, **guarded**: 409 if not connected, 400 if no senders — the
  scanner silently no-ops on either, so the guards give immediate feedback);
  `GET /gmail/scan/status` (connected / revoked / senders_count / latest
  `gmail_ingestion_runs` snapshot: running + counts + has_errors — the native
  scan-progress signal); `GET/POST/DELETE /gmail/senders` (comma/space/newline
  email blob per `bank_name`, regex-validated, `ACTIVE_CAP=8` enforced);
  `GET /gmail/shadow`; `POST /gmail/shadow/{confirm,discard}`. Schemas in
  `api/schemas/gmail.py`. Reuses `whitelist.py` (`add_sender`/`list_active`/
  `remove_sender_by_id`/`count_active`).
- **Mobile**: `mobile/src/api/gmail.ts` + three screens wired through
  `MasNavigator` → "Más" hub: `GmailScreen` (connect via `expo-web-browser`;
  polls `GET /gmail/scan/status` → shows connection, a "no senders" prompt, a
  **scanning spinner**, last-run summary, and a result/failure alert after
  "Escanear ahora" — the feedback that was missing), `GmailSendersScreen`
  (comma-separated emails per bank), `GmailReviewScreen` (per-row keep/discard +
  local-draft edit + "Aplicar"; loading / error+retry / empty states +
  pull-to-refresh + **refetch-on-focus** so it isn't a stale empty cache after a
  scan). Connect is poll-based (the OAuth callback redirects to a static page,
  not `ledgercr://`); callback→deep-link auto-close is deferred polish.
- **Disconnect/reconnect handling.** `scan_user_inbox` now opens the
  `gmail_ingestion_runs` row **up front** and finalizes it (with an `auth`
  error) even when the credential is revoked — previously it early-returned
  without a run, so the native poll hung forever. `GmailScreen` ends its
  spinner on `status.connected===false` (→ "Gmail desconectado, reconectá"),
  has a 90s safety timeout, and always shows a **"Reconectar Gmail"** action
  (tokens can die silently).
- **Transient-failure retry.** `_already_seen` treats a prior `failed` outcome
  as retryable (not "seen"); `_mark_seen` upserts `failed → terminal` on a
  successful retry but guards terminal rows from clobber (`WHERE outcome=
  'failed'`, preserving the race-safety intent). Fixes the "recent transactions
  never show up after a failed scan" bug — a revoked/errored scan no longer
  buries the emails it touched forever.
- **Known root cause (operational, not a code bug):** the GCP OAuth consent
  screen is in **Testing** publishing status, so Google expires `gmail.readonly`
  refresh tokens after **~7 days** → recurring "se desconectó tu Gmail". The
  app now handles this gracefully (reconnect anytime), but stopping the weekly
  disconnect requires publishing the consent screen to Production (+ likely
  verification for the sensitive scope) — **P8 work** (tech-debt below).
- Verification: `tests/test_gmail_native.py` (12: senders bulk-add/cap, sender
  list/remove, **native-connect activation**, shadow list/confirm-with-overrides/
  discard, auth, scan guards 409/400, scan status, revoked-run regression,
  failed-retry) + the full `-k gmail` slice pass; mobile `tsc` clean.

### Scheduled daily scan — two-bug fix (2026-06-25, on `dev`)

The Azure cron job `ledger-cr-gmail-daily` (`0 9 * * *` UTC) was firing daily
and reporting **Succeeded**, but the operator's inbox was never auto-scanned.
Diagnosis from Log Analytics found **two bugs in series** — fixing the first
exposed the second. Both are committed on `dev`; the worker-image fix is
deployed + verified in prod (a manual run created 2 shadow rows), the teardown
fix lands on the next worker rebuild.

1. **Activation parity gap** (`2ba9344`). The OAuth callback
   (`api/routers/gmail.py::oauth_callback`) upserts `gmail_credentials` with
   `granted_at` but **never `activated_at`** — only the bot's bank-confirmation
   flow (`bot/gmail_handlers.py::_activate_and_persist`) flipped it. The daily
   worker (`workers/gmail_daily.py`) iterates only
   `activated_at IS NOT NULL AND revoked_at IS NULL`, so a user who onboards
   Gmail **entirely in the native app** is silently skipped forever (`users=0`),
   even though manual in-app scans work (the scan path loads the credential by
   `user_id`, no `activated_at` filter). Fix: `POST /gmail/senders` now flips
   `activated_at` on first sender add (granted, not revoked, still NULL),
   mirroring the bot — configuring senders is the native equivalent of
   confirming banks and is the single choke point (scan 400s without senders).
   The operator's existing prod row was backfilled manually (one guarded
   `UPDATE … SET activated_at = now()`).
2. **Worker image missing the `azure` extra** (`5d7e786`). Once the worker
   reached the user, every scan threw `ModuleNotFoundError: No module named
   'azure'` → `RuntimeError: azure_kv backend requires uv sync --extra azure`,
   swallowed by `_scan_one_user`'s per-user `except` (logged `daily_scan_error`,
   `gmail_ingestion_runs.finished_at` left NULL, job still exits 0).
   `Dockerfile.worker` ran `uv sync` **without `--extra azure`** (while
   `Dockerfile.prod` has it), so `centro-worker` could never build the
   `azure_kv` secret store to read the Gmail refresh token from Key Vault. Manual
   scans worked because they run inside the API container (`centro-api`, which
   has the azure libs). Fix: add `--extra azure` to `Dockerfile.worker`,
   restoring the documented "mirror the API builder" intent. **Requires a worker
   image rebuild + `containerapp job update` to take effect in prod** (done
   2026-06-25).
3. **One-shot worker teardown** (`a04bed0`). The worker exited without closing
   the `azure_kv` store (`DefaultAzureCredential` + `SecretClient`, each owning
   an aiohttp `ClientSession`) or the redis pool → `Unclosed client session` /
   `Unclosed connector` / `Event loop is closed` noise via `__del__` after the
   scan succeeded. Added `AzureKeyVaultStore.aclose()` + module
   `secrets.close_secret_store()` (no-op for Env/File backends) and call it +
   `close_redis()` in `main()`'s `finally`. Cosmetic only; deploy on next
   worker rebuild. Tests: `tests/test_secret_store.py` (+3).

4. **Worker error-handling hardened** (`b8b23a1`). The blanket per-user
   `except` is what let bug #2 stay invisible (job green while scanning
   nobody). Now: `secrets.SecretStoreUnavailable(RuntimeError)` marks the three
   build-time secret-store failures; `gmail_daily._is_systemic()` walks the
   cause/context chain for import/secret-store errors and `_scan_one_user`
   **re-raises** those (aborts the run) while still swallowing genuine per-user
   errors; the loop tallies `ok/failed` and `_raise_if_all_failed` exits
   **non-zero** when every eligible user failed. A finished-but-empty run now
   shows **Failed** + triggers an orchestrator retry instead of a silent green
   no-op (zero eligible users still exits 0). Tests:
   `tests/test_gmail_worker_hardening.py` (9).

**Lessons:** (a) a per-user `try/except` that swallows a *hard dependency*
error as a soft per-user failure keeps the job green while doing nothing — infra
errors deserve a louder signal (now enforced by `_is_systemic` re-raise +
all-failed guard, `b8b23a1`). (b) `Dockerfile.worker` and `Dockerfile.prod`
must keep the same `uv sync` extras; any KV-touching worker needs `--extra
azure`. (c) a one-shot asyncio worker must close singleton network clients in a
`finally` before the loop tears down.

---

## Envelope budgeting ("Sobres", post-6f) — spending caps

The interactive home-tab feature: user-named monthly **spending-cap** envelopes,
each classed `needs | wants | savings | investing`. The bar shows **money left**
— it starts full (100% of the limit) and **drains** with each expense, going
**red in the last 5%** (and once over the limit). Spend is **computed live** from transactions (no
stored running balance, so a bar can never drift from the ledger); the month
window resets in the user's timezone. An expense is tagged to at most one
envelope via `transactions.envelope_id`. Sub-plan E1–E5; backend + mobile
code-complete, **operator on-device sign-off pending**. Decision note:
`05_Decisions/Decision - Envelope Budgeting - Spending Caps.md`.

- **Schema (migration 0022):** `envelopes` table (`id`, `user_id` FK CASCADE,
  `name`, `envelope_class` [CHECK in needs/wants/savings/investing],
  `limit_amount` NUMERIC(12,2) [CHECK > 0], `currency` default CRC, `period`
  default monthly, `is_active`, `archived`, timestamps; partial index
  `ix_envelopes_user_active WHERE archived=false`). `transactions.envelope_id`
  UUID nullable FK → `envelopes.id` `ON DELETE SET NULL` (deleting an envelope
  unlinks, never deletes the transaction) + partial index
  `ix_transactions_envelope WHERE envelope_id IS NOT NULL`.
- **Backend:** `api/models/envelope.py`; `api/schemas/envelopes.py`
  (`EnvelopeUpdate` is `extra="forbid"` — `currency`/`period` immutable
  post-create); `api/services/envelopes.py::compute_envelope_summary` (one
  grouped query: confirmed, non-archived, non-transfer, `amount < 0` rows in the
  current month, grouped by `envelope_id` **+ currency**; per-class subtotals; a
  best-effort `monthly_income` line from active recurring incomes).
  **Cross-currency spend is converted** via `api/services/fx.py::convert` — a
  US$ expense tagged to a CRC envelope counts at the fixed reference rate
  (`FALLBACK_USD_TO_CRC = ₡500/US$`); per-envelope figures stay in the
  envelope's currency, class subtotals + `total_limit` in the user's currency.
  **₡500 is a placeholder pending the BCCR API (tech-debt below).** Router
  `api/routers/envelopes.py` — `POST/GET /envelopes`, `GET /envelopes/summary`
  (declared **before** `/{id}`), `GET/PATCH/DELETE /envelopes/{id}` (DELETE =
  soft archive by default; `?hard=true` permanently removes the row — the
  `transactions.envelope_id` FK is `ON DELETE SET NULL`, so tagged transactions
  are unlinked, never deleted). `PATCH /transactions/{id}` now also accepts `envelope_id`
  (validated: the envelope must belong to the caller and be non-archived, else
  400 "Sobre inválido."; the existing shadow/transfer/archived 409s still
  apply). `TransactionResponse` carries `envelope_id`.
- **Query (read-only):** `app/queries/tools/envelopes.py::get_envelope_spending`
  — a Phase 6a read-only tool so "¿cuánto gasté en <sobre>?" / "¿cuánto me
  queda en mis sobres?" is answered by the Sonnet query dispatcher. It reuses
  `compute_envelope_summary` (so the answer can't drift from the bars),
  case-insensitively matches the envelope by name, and returns
  `available_envelope_names` for a graceful "no tenés ese sobre" miss.
  Registered before `compare_periods` (which stays the cache-anchor last).
- **Capture flow (chat):** after an **expense** commits, `process_message()`
  returns an `open_screen` hint `screen="assign_envelope"` carrying the new
  `transaction_id`. The native chat renders an in-chat **"Asignar a un sobre"**
  chip → a picker sheet → `PATCH /transactions/{id}{envelope_id}`. Income never
  gets the hint (envelopes are spending caps). Telegram ignores `open_screen`
  (envelopes are native-only, same pattern as the debt form). An explicit
  **at-capture** account picker is NOT part of this slice — it's a separate
  pre-commit concern (see parity note below). (Account was immutable
  post-create at the time; **post-create reassignment via the edit-modal "Cuenta"
  dropdown landed 2026-06-15** — see "Reassign Movement To Account" below — but
  the at-capture picker remains deferred.)
- **Mobile:** `mobile/src/api/envelopes.ts` (CRUD + `/summary` +
  `assignTransactionEnvelope` + `archiveEnvelope`/`deleteEnvelope`);
  `api/transactions.ts::fetchMonthExpenses` (current-month confirmed expenses
  for the bulk-assign view); `lib/format.ts` (shared `formatMoney`);
  `components/EnvelopePickerModal.tsx` (reused by Chat + the edit modal),
  `components/EnvelopeEditModal.tsx` (create/edit + **Archivar** soft +
  **Eliminar** hard-delete), `components/EnvelopeDetailModal.tsx` (tap an
  envelope → spend bar + this month's expenses with per-row assign toggles +
  "Editar"), `components/SobresSection.tsx` (home-tab section: per-class
  roll-ups + per-envelope money-left bars that drain from full and go red in
  the last 5%, "+ Nuevo", tap-to-detail). The bar direction (drain vs fill) +
  the 5% red threshold live in `api/envelopes.ts::envelopeProgress` (shared by
  the section + detail). The create/edit sheet carries a voseo help text under
  the limit field explaining the drain model. Wired into `screens/Dashboard.tsx`;
  `components/TransactionEditModal.tsx` gained a "Sobre" field (expenses only).
- **Verification:** `tests/test_envelopes.py` (CRUD, hard-delete unlinks
  transactions, `/summary` spend math + exclusions, over-limit + per-class
  subtotals, transaction envelope_id assignment) +
  `tests/test_phase_6f_chat_assign_envelope.py` (post-commit expense hint
  present / income hint absent) — both in `scripts/test_phase_6f.sh`. Full gate
  green: mobile `tsc` clean, 118 focused backend tests + 27 regression.
  `alembic current` → `0022 (head)`.
- **Shared envelopes ("Sobres compartidos") — IMPLEMENTED 2026-06-15** (branch
  `feature/shared-envelopes`, migration `0034`; operator on-device sign-off
  pending). Pulled forward from P8 as a **single-envelope share via a 6-char
  security code** (NOT the full household/tenant model — that stays P9). An owner
  shares a **root** envelope (subtree included) with **≤ 9** others. New
  `envelope_members` table (`UNIQUE(envelope_id,user_id)`, FKs CASCADE);
  `api/services/envelope_sharing.py` (`mint_share_code` owner+root only;
  `redeem_share_code` multi-use `redis.get` + root `FOR UPDATE` + cap, idempotent;
  `remove_member` unlinks the member's tagged tx via `envelope_id→NULL`;
  `fetch_shared_trees` / `shared_summary_items`). Code lives in Redis at
  `envelope:share_code:{CODE}` (24h, `bot/redis_keys.py`). Endpoints:
  `POST /envelopes/{id}/share`, `POST /envelopes/redeem` (declared before
  `/{id}`), `GET /envelopes/{id}/members`, `DELETE /envelopes/{id}/members/{user_id}`
  (owner removes anyone; a member removes only self); `GET /envelopes` appends
  shared trees. **A member can add/remove only their OWN expenses**
  (`can_assign_transaction_to_envelope` — the only place the owner-only rule is
  widened; bill/debt attach stays owner-only via `is_valid_envelope_target`) and
  sees only aggregate `spent` + their own `your_spent`, never another member's
  tx lines. **Shared cap**: `compute_envelope_summary`'s own-spend query was
  rescoped from `Transaction.user_id == user.id` to `envelope_id IN (the user's
  own envelope ids)` so an owner's bar drains with the COMBINED spend — identical
  absent sharing (only the owner/members can tag an envelope). **Byte-lock
  preserved**: shared envelopes a user only *joined* are appended to
  `summary.envelopes` as `is_shared` display-only items and are NEVER in
  `by_class`/`total_*`/`committed_outflows`/`has_budget`/snapshots — `cashflow`
  (`has_budget = any(not e.is_shared …)`), `affordability.gather_financial_context`
  (own-only), and `snapshots` (skip shared) are guarded; the unified-cashflow
  byte-lock stays identical. Mobile: `SobresSection` "Unirme con código" +
  "Compartidos con vos" block, `EnvelopeDetailModal` member read-only / owner
  share+members panel, new `JoinEnvelopeModal`. Verification:
  `tests/test_envelope_sharing.py` (8) + envelope/cashflow/affordability/snapshot
  regression (89, incl. byte-lock) green; mobile `tsc` + ruff clean; `alembic →
  0034 (head)`. **Still deferred**: general household/tenant model + RLS (P9),
  sharing bills/debts into a shared envelope, cross-currency per-member display.
  Vault `Decision - Shared Household Envelopes (Deferred P8)` (status flipped to
  implemented). An at-capture *account* picker remains deferred (a pre-commit
  proposal change) — note **post-create** account reassignment now exists via the
  edit-modal "Cuenta" dropdown (2026-06-15), distinct from this at-capture concern.

---

## Phase 7a (active) — Nested Envelopes + Context-Aware Pushback

Two operator-requested extensions on top of envelopes + the Phase 7
affordability engine. Backend + mobile **code-complete; operator on-device
sign-off pending** (do not flip to ✅ until then). Canonical:
`docs/phase-7a-decisions.md`; vault `Decision - Nested Envelopes (Sub-Sobres)`
+ `Decision - Context-Aware Pushback`.

**A — Sub-sobres (nested envelopes).** Envelopes nest **up to 5 levels**.
- **Schema (migration 0024):** `envelopes.parent_id` UUID nullable FK →
  `envelopes.id` `ON DELETE CASCADE`; `envelopes.depth` SMALLINT NOT NULL
  default 1 + `CHECK depth BETWEEN 1 AND 5`; partial index `ix_envelopes_parent
  WHERE parent_id IS NOT NULL`. **No `transactions` change** — a transaction
  still tags exactly one node (a leaf, or a parent directly). `alembic current →
  0024 (head)`.
- **Rules:** a child **inherits the root's `envelope_class` + `currency`**
  (one tree = one class + one currency); editing a root's class **cascades** to
  the subtree, a child's class is read-only (422). `parent_id` is **create-only**
  (re-parenting out of scope v1). Create is rejected at depth 6 (422).
- **Allocation = hard cap** (revised from "soft" after operator dogfood
  2026-06-08): a sub-sobre's limit can't exceed the parent's **remaining**
  budget (`parent.limit − Σ siblings`), and a parent can't shrink below its
  allocated total — `Σ(children) ≤ parent` always holds (router returns 422
  otherwise via `_parent_available`; the create sheet shows the available
  amount). The summary still carries `allocated`/`unallocated`/`over_allocated`
  (the last only for legacy rows).
- **Live roll-up:** `compute_envelope_summary` is a post-order DFS — each node
  keeps `direct_spent` (own), the bar shows `spent = own + Σ descendants`. **No
  stored balance.** Class subtotals: `spent_total` counts every node's own spend
  once; `limit_total` counts **roots only** (a parent's limit already contains
  its descendants' sub-allocations). Soft-archive/hard-delete a parent cascades
  the subtree (hard delete unlinks tagged transactions via the FK `SET NULL`).
- **Query tool:** `get_envelope_spending` matches a parent or child by name and
  returns rolled-up `spent` + `direct_spent` + a nested `children` breakdown.
- **Mobile:** `api/envelopes.ts` (`parent_id`/`depth` + allocation fields +
  `flattenEnvelopeTree`), tree-indented `SobresSection` + `EnvelopePickerModal`,
  "+ Sub-sobre" in `EnvelopeDetailModal`, inheritance-aware `EnvelopeEditModal`.

**B — Context-aware pushback (stable verdict).**
`api/services/finance/affordability.py::gather_financial_context(db, *, user,
today=None, horizon_days=60)` returns deterministic **context signals**:
envelope execution (reuses `compute_envelope_summary` → can't drift from the
bars: totals, `pct_consumed`, over-limit envelopes) + upcoming bills/events
(reuses `recurrence.get_upcoming_feed`, ≤8 soonest, amounts FX-converted). Wired
into the `assess_purchase` chat tool (`context` block) + a one-line non-blocking
note at conversational goal creation (`_goal_context_note`). **The headline
`feasible = monthly_needed ≤ 0.80 × (income − fixed − debt)` is UNCHANGED**
(`tests/test_phase_7a_context.py` locks it byte-identical); signals are **never
folded into disposable** — the monthly-fixed already amortizes recurring bills,
so subtracting upcoming lumps would double-count. LLM explains; rules decide.

**Verification:** `tests/test_phase_7a_subenvelopes.py` (10) +
`tests/test_phase_7a_context.py` (6) + the envelope/affordability/nudge/
goal-create/tool-registry/system-prompt regression green (74 + 46 in the
slice); mobile `npx tsc --noEmit` clean. `alembic current → 0024 (head)`.

---

## Fixed-Expense Attachment (post-7a, 2026-06-10)

A recurring bill / debt attaches to an envelope so its expected amount is
**reserved** inside the envelope and the `under_coverage` gate becomes per-item.
Backend + native data-layer **code-complete; on-device sign-off pending**.
Decision: `~/Finance_project/30_Projects/Finance-Agent/05_Decisions/Decision -
Fixed-Expense Attachment.md`.

- **Schema (migration `0026`)**: nullable `recurring_bills.envelope_id` +
  `debts.envelope_id` FK → `envelopes.id` `ON DELETE SET NULL` (deleting an
  envelope detaches, never deletes the obligation). Migration `0027` adds
  `notification_events.debt_id` + relaxes the "exactly one target" CHECK.
- **Attach/detach**: `PATCH /recurring-bills/{id}` + `PATCH /debts/{id}` accept
  `envelope_id` (validated active/same-user/non-archived via
  `envelopes.py::is_valid_envelope_target`); chat `Intent.ATTACH_EXPENSE` ("poné
  el recibo del ICE en el sobre Servicios") proposes → commits. Soft-archiving an
  envelope detaches its obligations explicitly in `archive_subtree` (the FK only
  fires on hard delete).
- **Reservation (compute-live, B2)**: `compute_envelope_summary` adds
  `reserved` + `available = limit − reserved − spent`. An attached obligation
  reserves its expected amount while **unpaid this cycle** (bill released when its
  current-month occurrence is paid; debt released when a `DebtPayment` lands this
  month; variable bills reserve 0) — once paid, the actual txn counts as spend
  instead, **never both**. Mark-paid / debt-payment propagate the obligation's
  `envelope_id` onto the payment transaction. No stored balance.
- **`committed_outflows` UNCHANGED** — Model A holds; attachment only moves the
  per-item gate, never the top line. A byte-identical regression locks this.
- **Per-item gate (B3)**: `under_coverage ⟺ ∃ active bill/debt with envelope_id
  IS NULL`. `MonthlyCashflow.unattached_obligations` (name/amount/source) drives
  `gate_reason` + the named copy. The aggregate `allocations < debt + bills`
  comparison is gone.
- **Suggestion (B4)**: the query **dispatcher** (not the LLM) appends a
  once-per-conversation voseo nudge to attach obligations when a cashflow tool
  finds unattached ones.
- **Debt projection (B5)**: a debt's cuota surfaces as a fixed expense in
  `recurrence.get_upcoming_feed` (`item_type="debt"`) + notifications, derived at
  read time from `payment_due_day` + `minimum_payment` — **no `RecurringBill`
  row**, so a paid-off / archived debt stops projecting with zero cleanup.
  `debt_payments` and `recurring_bills` sums stay disjoint.

**Verification:** `tests/test_fixed_expense_attachment.py`,
`test_envelope_reservations.py`, `test_phase7_per_item_gate.py`,
`test_phase7_attach_suggestion.py`, `test_phase7_debt_projection.py` + the full
Phase-7 regression migrated and green; mobile `tsc --noEmit` clean. `alembic
current → 0027 (head)`. **Remaining polish:** symmetric bills/debts-screen attach
entry point; "cuota de préstamo" label on debt feed entries.

## Phase 7b (active) — Accounts CRUD + Hard Delete, Transfers, Credit-Card Clarity

Three operator asks packaged as one phase (decisions locked 2026-06-11).
**Code-complete 2026-06-11 — operator on-device sign-off pending.**
Verification: `scripts/test_phase_7b.sh` green (mobile `tsc --noEmit`; 47
focused + 136 regression incl. the byte-locked unified-cashflow regression);
`scripts/test_phase_6f.sh` cross-check green; `alembic current → 0029 (head)`.
Canonical: `docs/phase-7b-decisions.md`; vault `Decision - Full Transfers In
Chat (Card Payment Is A Transfer)`, `Decision - Account Hard Delete With
Cascade`, `Decision - Credit Card Terms As Account Parameters (Not A Debt)`.

- **B1 — Full transfers (chat + native, no migration).** A card payment is a
  **transfer** (source → credit account), never an expense — fixes the
  double-count where "pagué la tarjeta" logged a second charge. New
  `Intent.LOG_TRANSFER` + `transfer_from_hint`/`transfer_to_hint`;
  deterministic dispatch (clarify missing side; card-payment copy when the
  destination is credit; **cross-currency chat-rejected v1** → native modal);
  commit through the same `create_transfer_with_transactions` the REST path
  uses; undo deletes transfer + both legs. `GET /transfers` gains
  `account_id` + `offset`. Mobile: `TransferModal` (keyboard scaffold) +
  "Transferir" on accounts list + "Registrar pago" on credit detail.
- **B2 — Account edit + TRUE hard delete (migration `0028`).**
  `DELETE /accounts/{id}?hard=true&confirm=<typed name>` cascades: the
  account's transactions are deleted; debts/bills/goals **detached, never
  deleted**; transfers touching the account deleted with the surviving
  other-account leg kept (amount unchanged, `transfer_id` NULLed, annotated
  "cuenta eliminada"). `GET /accounts/{id}/delete-impact` previews counts.
  Migration `0028` flips `debt_payments.transaction_id` +
  `bill_occurrences.transaction_id` to `ON DELETE SET NULL` (also fixes the
  latent `bot/undo.py` FK-violation on linked transactions). Mobile:
  `AccountEditModal` (name/type) + danger-zone typed-name delete.
- **B3 — `credit_card_terms` (migration `0029`).** 1:1 table on `account_id`
  (UNIQUE, CASCADE): purchase/cash-advance APR (0–1 fractions),
  `minimum_payment_pct` + `minimum_payment_floor` (mínimo = max(pct·saldo,
  piso)), `credit_limit`, `statement_day`, `payment_due_day`, `envelope_id`.
  **No balance column ever** — live from `compute_account_balances`. NOT a
  reused `Debt(debt_type='credit_card')` (static balance would drift; French
  endpoints wrong for revolving). `extract_card_terms` (Haiku→Sonnet 0.65) +
  `POST /accounts/parse-card-document` + `GET/PUT/DELETE
  /accounts/{id}/card-terms`. Chat `Intent.CREATE_CARD` → `open_screen
  card_create` (chat never commits a card). Mobile `CardAccountCreateScreen`
  (mirrors DebtCreateScreen: **contract-first** PDF prefill — "subí el
  contrato", statements also accepted —, low-confidence note, no-rate
  "llamá a tu banco" fallback) + `CardTermsEditModal`; credit accounts render
  "Debés ₡X" + "Disponible ₡(límite − deuda)". **Dual-currency cards** (most
  CR cards run ₡ AND $): "₡ + $ Ambas" creates TWO credit accounts
  ("<nombre> ₡"/"<nombre> $"), each with its own terms (per-currency rate/
  límite/piso/saldo; shared mínimo %/corte/fecha límite) — rides the
  single-currency machinery untouched; extraction `*_usd` fields
  auto-switch the form to Ambas. No multi-currency account.
- **B4 — Revolving engine + analysis.** Pure `app/domain/credit/revolving.py`
  (no LLM/DB/network): minimum-only projection with **never-payoff detection**
  (payment ≤ monthly interest), fixed-payment projection, strategy comparison.
  `GET /accounts/{id}/card-analysis` on the live balance; read-only chat tool
  `get_card_analysis` (registered before the `compare_periods` cache anchor);
  mobile `CardAnalysisCard` ("Si pagás solo el mínimo: N meses, ₡Y solo en
  intereses").
- **B5 — Card minimum as first-class obligation** (mirrors the debt-cuota
  integration; no new migration): feed `item_type="card_payment"` derived
  live; envelope reservation while unpaid this cycle (released by Σ transfer
  legs into the card this month ≥ minimum; the deterministic transfer path
  stamps the **debit leg** with the card's envelope so reservation swaps to
  spend, never both); per-item `under_coverage` + affordability transparency.
  `ATTACH_EXPENSE` gains `kind="card"`. **Coexistence:** a `credit_card` Debt
  linked to an account with terms is excluded from feed/gate/affordability.
  `committed_outflows` UNCHANGED (Model A; byte-locked regression).
- **B6 — `scripts/test_phase_7b.sh`** (mobile tsc + focused + regression
  slice). Card notifications deferred (needs a `notification_events` 4th
  target — 0027 pattern).

**Hard rule added:** a payment between the user's own accounts (incl. "pagué
la tarjeta") is `log_transfer`, never `log_expense`. The LLM never calculates
card interest — `app/domain/credit` does.

## Credit Card Contado Recurring Payment (post-7b, 2026-06-25)

Operator ask: a credit card's payment date + amount should count as a recurring
payment the agent considers, deleted with the card. **Code-complete on `dev`;
operator on-device sign-off pending.** Migration `0040`.
`committed_outflows`/cashflow byte-lock untouched. Canonical: vault
`Decision - Credit Card Contado Recurring Payment`.

Most of the ask already held: the card **minimum** projects **live** as
`item_type="card_payment"` in `get_upcoming_feed` (7b B5) and disappears for
free on card deletion (1:1 `credit_card_terms` `account_id` `ON DELETE
CASCADE`). Two real gaps closed here:

- **Per-card `payment_mode`** (`minimum` | `full`/contado) on
  `credit_card_terms` (migration `0040`, default `minimum`, CHECK). It selects
  **which live figure the upcoming-payment projection surfaces** — `minimum`
  (`card.minimum_due`, unchanged) or **de contado** (the full live
  `card.balance_owed`). **Projection, NOT a materialized `recurring_bills` row**
  (preserves "debts/cards projected, not materialized"). New
  `CardWithTerms.recurring_payment_due` property; `get_upcoming_feed` card
  branch uses it + sets `FeedEntry.payment_mode` + a "(de contado)" title;
  `UpcomingFeedItem` + `/calendar/upcoming` + the mobile `cardTerms`/`dashboard`
  types carry `payment_mode`. Toggle in `CardAccountCreateScreen` +
  `CardTermsEditModal`; `BillsScreen` labels the row mode-aware.
- **Agent awareness:** `app/queries/tools/recurring_bills.py::list_recurring_bills`
  now merges projected `debt` + `card_payment` entries from `get_upcoming_feed`
  (it had only read `bill_occurrences`, so projected obligations were invisible
  to the chat agent). This fixes the latent gap for debt cuotas too.

**Scope = reminder + agent only** (operator decision). The contado amount drives
the feed + the agent's answers; it does **NOT** change the budget — envelope
**reservation** (`compute_envelope_summary`) and **affordability transparency**
(`gather_affordability_inputs`) keep using the **minimum** (the must-pay floor),
and `committed_outflows` is byte-identical. Avoids the consumption double-count
(card charges are already envelope spend).

**Verification:** `tests/test_credit_card_contado_mode.py` (6: property,
contado projection + title + `payment_mode`, minimum-mode unchanged,
reservation-stays-on-minimum, affordability byte-stable across modes, query
tool surfaces the contado payment) + `scripts/test_phase_7b.sh` green (141,
cashflow byte-lock intact) + mobile `tsc --noEmit` clean. `alembic → 0040`.
**Deferred:** card payment notifications (still no `notification_events` card
target — 7b B6 deferral); letting contado drive the budget (rejected — double
count).

## Account creation + form keyboard UX (2026-06-25)

Two operator UX asks, mobile-only, no migration. **On `dev`; on-device sign-off
pending.**

- **Account type first.** `AccountCreateScreen` reordered so **Tipo de cuenta**
  is the first field (was Name → Type); `autoFocus` removed from Name so the
  keyboard doesn't pop before the user taps a type (and the credit→`CardCreate`
  redirect now triggers first). One file.
- **Keyboard no longer hides the last fields.** The 12 bottom-sheet input modals
  already had the `KeyboardAvoidingView`+`ScrollView` scaffold; the three
  full-screen create forms (`AccountCreateScreen`, `CardAccountCreateScreen`,
  `DebtCreateScreen`) had a `ScrollView` but **no** KAV. Targeted fix (operator
  chose targeted over a shared-scaffold refactor): added iOS
  `automaticallyAdjustKeyboardInsets` to each + bumped `contentContainerStyle`
  `paddingBottom` to `Spacing.xl * 2`. No new dep, no shared component.

## Phase 7c (active) — UI 2.0: Neutral Theme + Money Clarity

Operator ask 2026-06-11 ("too hard to understand my money; make the UI
extremely professional/modern, German form-follows-function, subtle icons,
minimal"). **Code-complete on branch `phase-7c-ui` — operator on-device
sign-off pending.** Canonical: `docs/phase-7c-decisions.md`; vault
`Decision - UI 2.0 - Neutral Theme & Money Clarity`.

- **Theme v2 (token swap, no key renames):** `mobile/src/theme.ts` moves from
  warm parchment to a neutral Rams palette — off-white `#F7F7F4` canvas, white
  cards, graphite ink `#191917` text **and accent** (primary actions are ink,
  not a brand hue). Color = meaning only: income green, expense/overdue brick
  red (rule unchanged), ochre caution, class hues on envelope bars. Propagates
  to every screen via tokens.
- **Inter (scoped):** static TTFs vendored in `mobile/assets/fonts/`, loaded
  via the existing `expo-font` dep in `App.tsx` (proceeds on system font if
  loading fails). `theme.ts` exports `Fonts`; rule — set `fontFamily` from
  `Fonts`, never also `fontWeight` (static weights; iOS would fake-bold).
  Inicio + nav chrome use Inter now; other screens adopt it as touched
  (RN has no global font override; a 60-file sweep was rejected — no native
  CI to catch regressions).
- **Inicio restructure:** hero **"Te queda este mes"** =
  `total_available` from `/envelopes/summary` (never derived client-side) with
  a draining bar (red last 5% / over budget) + "quedan N días"; **Próximos
  pagos always visible** (overdue first, 3 + expand) now including projected
  debt cuotas **and card minimums** — the mobile feed type was missing
  `"card_payment"` since 7b B5, so they never showed on Inicio (fixed in
  `api/dashboard.ts`); then Sobres; then Resumen (period picker scoped HERE —
  hero/pagos/sobres are always "now"; categorías on demand; saldo total as a
  quiet footer row). Still no chart lib.
- **Backend (additive, no migration):** `EnvelopeSummaryResponse` gains
  `total_spent` / `total_reserved` / `total_available` (roots only, summary
  currency — same no-double-count rule as `total_limit`), computed in
  `compute_envelope_summary` next to the bars so the headline can't drift.
  `committed_outflows` untouched (byte-locked regression green).
- **Verification (2026-06-11):** `scripts/test_phase_7b.sh` green on the
  branch (mobile `tsc --noEmit`; 48 focused + 136 regression); envelope
  suites with new totals assertions 14 passed; envelope-adjacent slice 36
  passed; `alembic current` still `0029 (head)`.
- **Deferred:** full Inter body migration; per-screen layout polish beyond
  Inicio; any trend chart.

## Phase 7e (active) — Data Foundation: Advice Trace, Snapshots, Consent

Strategic data substrate for the long-term counsel/underwriting vision
(vault: `Long-Term Strategy - Financial Institution`). **Code-complete
2026-06-11 on branch `phase-7e-data`.** No product behavior changes —
everything accretes passively. Canonical: `docs/phase-7e-decisions.md`;
vault `Decision - Data Foundation (Advice Trace, Snapshots, Consent)`.

- **`advice_events` (migration `0031`)** — append-only trace of every
  deterministic verdict surfaced to the user, with FULL `inputs`+`result`
  JSONB (the reproducibility rule extended from calculations to decisions).
  Wired surfaces (5): `assess_purchase`, `get_savings_capacity`,
  `get_card_analysis` (query tools — telemetry-class write like
  `llm_query_dispatches`; the read-only rule = no financial-state mutation),
  the conversational goal feasibility gate (write dispatcher), and
  `over_commitment` (recorded at the orchestrator's post-dedup INSERT — one
  trace per fired nudge). Recorder `api/services/advice_trace.py`: **own
  session via the `app/queries/session` settable proxy, swallow-on-fail with
  loud logging** — can never break an advice path. New kinds go in
  `KNOWN_KINDS`. `outcome_*` columns reserved for a future labeling worker
  (advice → action → outcome).
- **`envelope_snapshots` + `cashflow_snapshots` (migration `0031`)** —
  frozen per-period copies of the envelope summary figures + the
  MonthlyCashflow picture, so "¿respeté mi presupuesto en marzo?" survives
  limit edits and hard deletes (identity denormalized; `envelope_id` FK
  `SET NULL`). `api/services/snapshots.py::capture_user_snapshots` upserts
  idempotently (partial UNIQUE `(envelope_id, period)`;
  `UNIQUE(user_id, period)` for cashflow); on day ≤ 3 of a month it
  recaptures the just-closed period via the new optional `today` override on
  `compute_envelope_summary` (limits/reservations still read current rows —
  accepted imprecision, documented). Runs in the nightly insights worker
  (own session + own try/except) and via `POST /api/v1/jobs/capture-snapshots`.
- **`user_consents` (migration `0031`)** — append-only, versioned consent
  ledger; purposes CHECK-constrained (`core_service`, `behavioral_insights`,
  `product_research`, `aggregated_datasets`; widen by migration). Current
  state = latest row per (user, purpose). `GET/POST /api/v1/users/me/consents`
  (POST appends grant/revoke; unknown purpose → 422). Consent UX is P8
  onboarding work; the ledger lands first because consent can't be
  retro-fitted.
- **Verification:** `scripts/test_phase_7e.sh` — focused
  `tests/test_phase_7e_data_foundation.py` (7) + wired-surface regression
  (107) green; `scripts/test_phase_7b.sh` cross-check green (48 + 136,
  byte-locked cashflow intact). `alembic current → 0031 (head)` (sits on top of the parallel Phase 7d goal-funding migration `0030`).
- **Ops incident (preserved):** this block was first numbered 0030/"Phase
  7d" and collided with the operator's PARALLEL, uncommitted Phase 7d
  goal-funding work whose migration `0030` was already applied to the shared
  dev DB — so the first `upgrade head` silently no-opped (version said 0030,
  tables missing). Resolution: renamed to Phase 7e / migration `0031`
  (`down_revision=0030`), version table corrected. Lessons: migration
  numbers + phase letters must be claimed against BOTH committed history and
  in-flight working trees; if a fresh migration "applies" without a "Running
  upgrade" log line, compare `alembic_version` against physical tables.
- **Deferred:** outcome-labeling worker (definitions of "followed" needed);
  consent onboarding UX + `core_service` auto-seed at register (P8);
  snapshot read API; `assess_financing` trace kind.

## Phase 7f (active) — Capture & Forms UX

Four operator UX asks (2026-06-12), **code-complete same day — operator
on-device sign-off pending**. Canonical: `docs/phase-7f-decisions.md`; vault
`Decision - Capture & Forms UX (Phase 7f)`. No migration (`0031` head).
Gate: `scripts/test_phase_7f.sh` (mobile `tsc --noEmit`; 6 focused + 79
regression); `scripts/test_phase_7b.sh` + `test_phase_6f.sh` +
`test_phase_7d.sh` cross-checks green.

- **B1 — Account buttons on clarification (backend, both channels).**
  `AskClarification` gains `options: list[str]` (active account names, cap
  `MAX_ACCOUNT_OPTIONS = 8`); the 7 "¿De qué cuenta?" / transfer-side sites
  fill it; the question copy drops the inline "Opciones: …" listing (now
  "Tocá una opción o escribime el nombre"). `ClarificationState` gains
  `options` + `nonce` (defaulted — old states/tests valid). `_apply_decision`
  renders options as `ConfirmButton`s with `callback_data="clarify:{nonce}:
  {idx}"`; re-asks re-attach the buttons. **Native chat: zero changes** —
  chips already post the label as text into the existing merge path.
  Telegram: new `clarify:` callback namespace → `bot/handlers.py::
  on_clarify_callback` → `pipeline.handle_clarify_callback` (nonce-validated,
  routes the label through the SAME `merge_reply → dispatch` path; stale taps
  → `CLARIFY_EXPIRED`; reply rendered through `_send` because it can carry
  the proposal's Sí/No/Editar keyboard). No "crear cuenta" button — typing an
  unknown name already enters the 6d B8/B9 lazy-create flow with origin
  replay. Tests: `tests/test_phase_7f_account_buttons.py`.
- **B2 — Debts + card minimums in Gastos fijos (mobile only).** `BillsScreen`
  "Próximos pagos" merges the unified feed's `item_type="debt"/"card_payment"`
  items (labels "Cuota de préstamo" / "Pago mínimo de tarjeta"; same urgency
  coloring; debt rows navigate to `DebtDetail` via the feed's `debt_id`, card
  rows informational); "Todos" gains a read-only "Deudas" section (cuota ·
  día de pago → DebtDetail). Projected, never materialized — no backend
  change. Closes the "'cuota de préstamo' label on debt feed entries" polish
  item.
- **B3 — Prominent envelope indicator (mobile only).** In
  `EnvelopeDetailModal`, expenses assigned to ANOTHER envelope leave the
  assignable list and collapse under "En otros sobres (N)": each row shows a
  class-colored pill naming the envelope + an explicit "Mover aquí" button
  (no more ambiguous empty-circle + tiny "en otro sobre" text).
  `TransactionDetailScreen` gains a class-colored "Sobre «X»" badge.
- **B4 — Form polish (mobile only).** New dep
  `@react-native-community/datetimepicker@8.4.4` (the SDK 54-bundled version
  — runs in Expo Go). New shared fields in `mobile/src/components/fields/`:
  `AmountInput` (live space-grouped thousands `1 000 000`, display-only —
  the form state keeps the plain numeric string so every existing
  `Number(v.replace(",", "."))` parse is untouched) and `DateField`
  (calendar sheet replacing every "AAAA-MM-DD" text input; day-level only).
  Swept: TransactionEditModal, TransferModal, GoalFormModal, IncomeFormModal,
  BillFormModal, EnvelopeEditModal, DebtEditModal, CardTermsEditModal,
  DebtCreateScreen, CardAccountCreateScreen, AccountCreateScreen,
  SalaryCalculator, GoalDetail contribution form. Percent/day/term inputs
  stay plain. (Category was free text here under 6e B11; **superseded
  2026-06-13** — see "Categories Reconciled" below: transaction-edit + bills now
  pick from the Categorías-screen list.)

## Phase 7g (active) — Income Model Rework (2026-06-13)

Two income correctness bugs, **code-complete 2026-06-13 — operator on-device
sign-off pending**. Migration `0032`. Canonical: `docs/phase-7g-decisions.md`;
vault `Decision - Income Model Rework`. Gate: `scripts/test_phase_7f.sh` +
7d/7b/6f cross-checks green; mobile `tsc` clean.

- **Frequency division.** A salary is entered MONTHLY (gross→net); the row now
  stores the **per-payment** amount (`monthly_net ÷ payments-per-month`) so the
  shared normalizer multiplies it back — a quincenal salary stores `net/2`, no
  longer inflated ~2.17×. Single factor source `api/services/income_frequency.py`
  (`PAYMENTS_PER_MONTH`, **CR quincenal = 2/month**, not 26/12);
  `envelopes._FREQ_TO_MONTHLY` aliases it and `affordability._BILL_FREQ_TO_MONTHLY`
  sets biweekly=2 to match (incomes + bills). `_dispatch_create_income` divides
  the CRC-salary net; USD/freelance ("cada pago") stay per-payment. Mobile mirror
  `mobile/src/lib/incomeFrequency.ts` + `IncomeFormModal` divides on save,
  reconstructs monthly on edit, shows a per-paycheck preview. Round-trip locked
  by `test_quincenal_per_payment_roundtrips_to_monthly_net`.
- **CR aguinaldo + salario escolar.** New pure module
  `app/domain/payroll/cr_cycles.py` (`compute_aguinaldo`,
  `compute_salario_escolar`): aguinaldo = **gross** × days worked in Dec1–Nov30
  ÷ window days; salario escolar = **8.33% × annual gross earned**, day-prorated
  over the calendar year. `api/services/finance/incomes.py::derive_amount_for`
  rewired to `(monthly_gross, hire_date, as_of_year)`. New
  `recurring_incomes.hire_date` (migration `0032`); `derive-cycles` accepts +
  persists a `hire_date` body, uses `gross_monthly` (or `amount × cadence`
  fallback), and recomputes existing rows on re-derive. Mobile "Derivar" prompts
  the fecha de incorporación (`DateField`, prefilled) with an "Año completo"
  escape. Cycles stay `frequency="annual"`, excluded from `_monthly_income`.
- **Existing data:** migration doesn't rewrite amounts; opt-in
  `scripts/backfill_income_per_payment.py` (dry-run default) divides pre-change
  non-monthly salary rows. Operator's 1–2 incomes: re-enter.
- **Hard rule reaffirmed:** the LLM never computes a cycle/salary figure — the
  pure `app/domain/payroll` rules layer does; the dispatcher only routes.

## Phase 8 (started) — B1: Telegram cold-start registration (2026-06-12)

A stranger can now onboard with zero curl. Background: the operator hit the
dead-end himself post-reset — unpaired `/start` pointed at an authenticated
pairing endpoint. Key insight: **pairing codes are only needed API→Telegram**
(prove ownership of an existing user row); in the inverse direction Telegram
already authenticates `telegram_user_id`, so the bot creates + binds in one
step. Canonical: `docs/phase-8-decisions.md`; vault `Decision - Telegram
Cold-Start Registration`.

- **Flow** (deterministic, zero LLM, mirrors 6d B9): unknown tg id sends
  `/start` → `bot/registration.py` Redis mini-flow
  (`telegram:registration:{tg_id}`, TTL 15 min, keyed by TELEGRAM id — no
  user row exists yet): email (uniqueness-checked; taken → pairing-path copy
  without burning the flow) → nombre → confirmación con ToS → user created
  with `telegram_user_id` bound in the same INSERT → `shortcut_token`
  delivered in-chat (<code>, rotate-able; same exposure class as `/login`
  codes) → `/setup` (6d onboarding takes over). `/cancel` aborts;
  `begin_registration` is re-entrant; defaults CR/CRC/es-CR not asked.
- **Shared creation path**: `api/services/users.py::create_user_with_defaults`
  extracted from the register router — REST register + bot flow mint the
  token and seed the default notification rule + categories in ONE place.
- **First real Phase 7e consent use**: confirming the summary IS the
  `core_service` grant (`record_consent(..., version=registration.
  TERMS_VERSION, source="telegram")`, same transaction as the user row).
  Bump `TERMS_VERSION` when the confirm copy changes.
- `PAIR_PROMPT` rewritten: unknown users → `/start` first; the authenticated
  pairing-code path stays for linking existing users.
- **Verification:** `tests/test_phase_8_registration.py` (6) + handler/
  welcome/endpoints/dispatcher regression (58) + 7e/goal/magic-link slice
  (27) green. No migration (`0031` head).
- **Deferred:** currency/timezone prompts (defaults CR), registration rate
  limiting per tg id (beta scale doesn't need it), self-serve account
  delete, formal ToS document.

## Phase 8 — Activation & Advisor UX (planned, B2–B6, 2026-06-25)

Driven by the first two non-technical user tests (2026-06-25) + a prior
UX/psychology audit. **Forks locked by operator:** reactive advisor (not
proactive) + chat-led first run (not a visual wizard). Principle: **make depth
optional, make the first win free, reward real progress** — without breaking
"LLM extracts; rules decide" or chat-first. Canonical: `docs/phase-8-decisions.md`
(Activation & Advisor UX track); vault `Decision - Activation & Advisor UX
(Phase 8)`. **PLANNED — not started.**

- **User-test evidence.** Tester 1 froze after login ("no instructions / no
  step-by-step") and was overwhelmed by Gmail/sender config reached too early →
  B2 + B3. Tester 2 expected an advisor: *"estoy por pasarme de Gustos, ¿de dónde
  muevo plata?"* → B4 + B6 (advice-forward + move-budget-between-sobres +
  over-limit-as-decision).
- **B2 — Activation: chat-led first run + redefine "activated."** First
  post-login surface is the chat, zero forms: "¿cuánto tenés en tu cuenta?" →
  account + balance **anchor** (reuse `Intent.SET_BALANCE` + 6d B9) → real number
  in ~20s. "Activated" = 1 account + 1 balance + 1 expense (drop the 4-entity
  gate + the "te falta registrar" guilt copy). Deep-link new registrants into the
  app (reuse B15 `ledgercr://`), CR bank picker, net-salary fast path, email-taken
  recovery, dashboard empty-state CTAs.
- **B3 — Gmail out of the critical path + guided connect.** Gmail becomes an
  opt-in power-up with a step-by-step guided flow (incl. the ~7-day reconnect
  caveat); no new user stumbles into sender config. Mobile + copy only.
- **B4 — Reactive advisor: reallocation-on-request.** New deterministic
  **move-budget-between-sobres** primitive (atomic, same-currency v1, respects
  parent/child caps; likely `POST /envelopes/reallocate`) + a read-only tool that
  computes where to pull from (reuses `compute_envelope_summary`). LLM proposes a
  specific move, user confirms, deterministic commit; also fires when a sobre
  limit is hit mid-capture. Reactive only.
- **B5 — Emotional symmetry: earned-celebration layer.** Real peaks at goal
  achieved / debt paid off / first full month / stayed-under-budget. The
  *decision* to celebrate is deterministic (mirrors "LLM never decides
  whether/when to nudge"); LLM may phrase. **No streaks/points/badges.** Aguinaldo
  + salario escolar as CR fresh-start anchors.
- **B6 — Envelope humanization.** Over-limit → "¿cubrís moviendo de otro sobre?"
  (reuses B4). Progressive disclosure: default the *tipo*, hide allocation jargon
  until a sobre has children, hide nesting by default, starter-sobres pack. Batch
  bulk-assign prompt for unassigned gastos. A deterministic **merchant→sobre
  memory** is a SEPARATE mini-decision (a user-confirmed mapping, NOT an LLM
  normalization map — the no-synonym-maps rule stands).
- **Sequence:** B2 → B4 → B6 (B6 reuses B4's primitive); B3 + B5 independent.
  **Deferred:** proactive advisor (flag); cross-currency reallocation;
  merchant→sobre memory (own decision); net-worth trend view; visual wizard.

## Phase 7h (active) — Savings Clarity + Analytics Screen (2026-06-13)

Two operator dogfooding asks. **Code-complete on branch `phase-7h-analytics`
— on-device sign-off pending.** No migration. Canonical:
`docs/phase-7h-decisions.md`; vault `Decision - Savings Excluded From Available
Balance`, `Decision - Charts Via react-native-svg`.

- **Savings is "plata apartada" (excluded from the available total).** The home
  total was confusing because it summed savings + checking. New
  `api/services/dashboard/summary.py::_balance_split` returns `(available,
  savings)` bucketed by `Account.account_type` (JOIN txns→accounts so a
  checking→savings transfer lowers available, raises savings — transfers still
  net). `DashboardSummary` gains `available_balance` (savings EXCLUDED — the
  home figure) + `savings_balance` (shown apart); `balance_total` unchanged
  (back-compat). Mobile: `Dashboard.tsx` footer → "Disponible" + "Ahorros
  aparte"; the DISPONIBLE strip is the home **headline**; `AccountsScreen` strip
  mirrors it; `AccountCreateScreen` shows a savings-only hint. **Envelope spend
  UNCHANGED** (still all accounts — the operator only excluded savings from the
  balance). No `is_primary`/"cuenta principal" (operator dropped it).
- **Home leads with Disponible, not the budget (revision 2026-06-13).** The
  budget hero **"Te queda este mes"** was **removed from the home** and **moved
  to the top of the Analytics screen** at the operator's request. Inicio order
  is now Disponible → Próximos pagos → Sobres → Resumen. **Supersedes UI 2.0 §3**
  ("Inicio answers ¿cuánto me queda este mes? first") for the home surface.
- **Analytics screen + "explícame este gráfico".** New dep
  `react-native-svg@15.12.1` (Expo SDK 54-bundled, Expo Go-safe; reverses UI
  2.0 §5 "no chart lib"). `mobile/src/components/charts/{DonutChart,LineChart}.tsx`
  (on-palette SVG; color = meaning; bars stay flex). `AnalyticsScreen.tsx`:
  the **"Te queda este mes" budget hero** (moved from home) on top, then 3
  cards from EXISTING endpoints (cash-flow line, category donut, envelope
  by-class bars) — **no new backend**. `InicioNavigator` wraps the Inicio tab
  (DashboardHome → Analytics); entered via a "Análisis" affordance on the
  `SobresSection` card. Each chart's **"Explícame"** cross-tab navigates to the
  Chat tab with a prefilled, **auto-sent** question (`ChatNavigator` Chat param
  `initialMessage`; `Chat.tsx` one-shot auto-send). The existing read-only
  query tools fetch the data; the LLM explains — rules provide data, LLM
  explains, zero backend change.
- **Verification:** `scripts/test_phase_7h.sh` green — mobile `tsc` clean; 2
  focused (`tests/test_phase_7h_savings_balance.py`) + 37 regression
  (dashboard/cashflow byte-lock/envelopes/goals). No migration.
- **Deferred:** quincenal budget periods; scoping envelope spend by account;
  credit-account treatment in the available total; passing chart raw-data to
  the LLM.

## Categories Reconciled — Single Source (2026-06-13)

Operator ask: one category list across the app + an easy picker. Transaction
edit + bills + envelope label **signed off on device 2026-06-15**; the Gmail
shadow-review picker (follow-up same day) is code-complete, on-device sign-off
pending. On `dev`. No migration. Decision note: vault `Decision - Categories
Reconciled (Single Source)`.

- **`user_categories` (the Categorías screen) is the single source for every
  manual category picker.** New shared `mobile/src/components/CategoryPickerModal.tsx`
  (mirrors `EnvelopePickerModal`), filtered by `kind` to the transaction's sign.
- **Transaction edit (`TransactionEditModal`) → dropdown, no free text** (**supersedes
  6e B11 "category stays free text"** for this surface). On select it sends BOTH
  `category` (name — what list/detail screens display) AND `category_id` (FK the
  backend already validated) so display + FK + per-category counts stay in sync.
  `TransactionUpdate` (mobile) gained `category_id`.
- **Bills (`BillFormModal`) share the picker** (kind `expense`); bills store the
  category **name** string (no FK on `recurring_bills`). `fetchBillCategories` /
  `GET /onboarding/categories` no longer used by the form (endpoint kept for the
  bot/onboarding). **Backend validation relaxed**: `RecurringBillCreate`/`Update`
  accept any non-empty `≤50`-char category (custom names like "mascotas" no
  longer 422). `VALID_RECURRING_BILL_CATEGORIES` stays — still used by the chat
  `create_bill` dispatcher for the `servicios` fallback (chat path unchanged).
- **Envelope "Categoría" → "Tipo" (cosmetic only)** in `EnvelopeEditModal` +
  `SobresSection` — the 4 budget classes and their math are unchanged.
- **Gmail shadow-review picker (`GmailReviewScreen`, follow-up 2026-06-15).** Its
  per-row edit also used free-text category; now the same `CategoryPickerModal`.
  Shadow rows can't be PATCHed (409), so the edit lands at confirm time:
  `ShadowConfirmItem` + `shadow_review.ShadowEdit` gained `category_id`, and
  `POST /gmail/shadow/confirm` validates it (active + caller-owned, 400 else) then
  sets `category` (name) + `category_id` on the promoted row.
- **Preserved:** LLM still emits free-text `category_hint` (no extractor/enum
  change). **Deferred:** backfill linking existing free-text `tx.category` to a
  matching `category_id` (manual via the dropdown for now).
- **Verification:** mobile `tsc --noEmit` clean; `tests/test_phase_6e_b5_transactions`,
  `test_phase_6e_b11_categories`, `test_phase_6d_b2_endpoints`,
  `test_phase_6f_b10_bills` (+ a new custom-category test),
  `test_phase_6f_chat_create_bill`, `test_gmail_native` (+ 2 new category_id
  confirm tests) green.

## Duplicate Detection + Transaction Hard Delete (2026-06-15)

Operator ask: warn about likely-duplicate gastos (notify via Telegram + in-app,
let the user keep/delete) + a TRUE permanent delete from the app (not just
archive). **Code-complete on branch `feature/dedup-hard-delete`; operator
on-device sign-off pending.** Migration `0033` (CHECK widen only). No new dep.
`committed_outflows`/cashflow math untouched (byte-lock green). Canonical:
`docs/duplicate-detection-decisions.md`; vault `Decision - Duplicate Detection &
Transaction Hard Delete`.

- **Detector** `api/services/dedup/duplicate_detector.py` (deterministic — LLM
  never decides a dupe, only phrases the push). `find_likely_duplicate`: same
  currency + magnitude (±0.01), `transaction_date` within ±3 days, both
  confirmed non-archived expenses, no transfer/goal flow; merchant similarity
  **boosts/breaks ties, not required** (catches manual-vs-Gmail dupes whose
  merchant text differs). Only the **newer** row is flagged via
  `transactions.is_duplicate` (column unused since migration `0001` — **no
  migration for the flag**). `flag_and_notify` (at-capture, best-effort
  swallow-on-fail like `advice_trace`) flags + raises the nudge (idempotent,
  dedup_key `duplicate:{txn_id}`) and returns the matched row + nudge_id.
- **At-capture hooks**: chat post-commit (`bot/pipeline.py` `_handle_confirm`
  log_expense), `POST /transactions`, `POST /transactions/shortcut`. Gmail keeps
  its reconciler dedup (not re-hooked). A safety-net evaluator
  (`evaluators/duplicate_transaction.py`) turns any flagged-but-unnudged row into
  a nudge.
- **Notification = Phase 5d nudge rails.** New `nudge_type="duplicate_transaction"`
  (migration `0033` widens the CHECK on `user_nudges` + `user_nudge_silences`),
  buttons `[Eliminar(act), Conservar(dismiss)]`, phrasing prompt, feed render.
  Delivers to **Telegram + in-app Alertas**. WhatsApp not wired (P5c) → inherited
  when it lands.
- **"Ambas" surface** (operator choice): an inline chat warning at capture
  (`open_screen screen="duplicate_warning"` — mirrors `assign_envelope`, preferred
  over it when a dupe is found) **and** the proactive nudge. Native `Chat.tsx`
  renders Eliminar/Conservar wired to the nudge act/dismiss endpoints, so chat +
  Alertas + Telegram resolve identically.
- **Keep ≠ silence (hard rule):** the dupe nudge resolves via `mark_acted_on` for
  BOTH buttons (verb only picks delete-vs-keep), bypassing the generic
  `mark_dismissed` auto-silence — saying "no es duplicado" twice must NOT mute
  duplicate detection. `resolve_duplicate(keep)` is the one place; wired in
  `bot/pipeline.py::handle_nudge_callback` + the REST `/nudges/{id}/act|dismiss`
  duplicate branch.
- **Permanent delete**: `DELETE /api/v1/transactions/{id}` (distinct from the
  archive bulk endpoint) → `hard_delete_transaction` (generalized from `/undo`'s
  `delete_telegram_transaction`). Guards mirror PATCH + the `/undo` bill guard →
  409: shadow / transfer leg / goal flow / linked-to-bill / linked-to-debt
  (FK is `SET NULL`, so deleting a linked row would leave a bill/debt paid with no
  movement). Archived rows ARE deletable. Spanish copy `TXN_DELETE_REASON_ES`.
  Native "Eliminar definitivamente" on `TransactionDetailScreen` + simple
  destructive Alert (operator choice — one row, no cascade).
- **Verification:** `tests/test_duplicate_detection.py` (15) +
  `tests/test_transaction_hard_delete.py` (8) + nudge/transactions/chat-post-commit
  regression (94) + cashflow byte-lock (16) green; mobile `npx tsc --noEmit`
  clean. `alembic → 0033 (head)`. **Deferred:** WhatsApp delivery (P5c);
  auto-merge; backfilling `is_duplicate` on historical rows.

## Reassign Movement To Account (Edit Modal, 2026-06-15)

Operator ask: adjuntar un movimiento a otra cuenta desde la pantalla de
Movimientos, con la selección por **dropdown**, y que el saldo de la cuenta
refleje el monto. **Merged to `dev` (`87dcb8a`); operator on-device sign-off
pending.** No migration. `committed_outflows`/cashflow math untouched (byte-lock
green). Canonical: `docs/transaction-account-reassignment-decisions.md`; vault
`Decision - Reassign Movement To Account (Edit Modal)`.

- **`account_id` is now editable** — was immutable post-create (the
  `TransactionUpdate` schema comment said so). The "se descuenta el monto" is
  **automatic**: balances aren't stored — `compute_account_balances` derives them
  live as `initial_balance + Σ confirmed non-archived txns WHERE account_id = X`,
  so changing `transactions.account_id` moves the amount between accounts with
  zero balance-update code (no drift, same principle as envelope spend).
- **Backend** (`api/`): `account_id` added to `TransactionUpdate`;
  `PATCH /transactions/{id}` validates the account is the caller's + active (400
  "Cuenta inválida.", mirroring the create-time check). Existing immutability
  guards UNCHANGED — shadow / transfer leg / goal flow / archived → 409.
- **Cross-currency** (operator: "cualquier cuenta … se realiza la conversión").
  Reassigning to a different-currency account converts the amount via
  `api/services/fx.py::convert` (fixed ₡500/US$) and **rewrites
  `transactions.currency` to the destination account's** — mirroring the
  transfers convention (each leg stored in its account's currency) so per-account
  balance sums (currency-naive `SUM(amount)`) stay correct. The client edits the
  amount in the row's CURRENT currency, so the effective amount is interpreted in
  `txn.currency` before converting. **No funds guard** (unlike transfers —
  reassigning doesn't move money the user lacks, only relabels which account a
  movement hit).
- **Mobile** (`mobile/`): new `components/AccountPickerModal.tsx` (bottom-sheet,
  mirrors `CategoryPickerModal`/`EnvelopePickerModal`; lists active accounts of
  ALL currencies with name + ₡/$ + type, plus a "Sin cuenta" clear row); new
  **"Cuenta"** dropdown field in `TransactionEditModal` beside Categoría/Sobre,
  with a one-line conversion hint when the chosen account's currency differs.
  `TransactionDetailScreen` unchanged (already renders the Cuenta row + invalidates
  the accounts/dashboard caches on save). `TransactionUpdate` (mobile) gained
  `account_id`.
- **Operator decisions:** the dropdown lives in the **edit modal** (not inline on
  list rows); attach to **any account regardless of currency** with conversion
  (not same-currency filtering).
- **Verification:** `tests/test_phase_6e_b4_accounts.py` (8 — 3 new: reassign
  moves balance between accounts, ₡→$ converts to −$2.00, foreign/archived account
  → 400) + `tests/test_envelopes.py` (7, post-rebase over the envelope-sharing
  commit) green; mobile `tsc --noEmit` clean; `scripts/test_phase_7b.sh` green
  (cashflow byte-lock + transfers intact). No migration.
- **Deferred:** inline-on-list assignment; manual fx rate (uses the fixed
  reference); preserving the original amount/currency of a converted row; the
  **at-capture** account picker (pre-commit) stays deferred (separate concern).

## Movimientos Sin Cuenta + Chat Error Nets (2026-06-15)

In-app chat dogfood: it 500'd on "patrón de gastos → Este mes" (the app shows a
generic "Hubo un error"), and "movimientos sin cuenta" got confused with "sin
categoría". **Committed to `dev` (`adcd827`); operator on-device sign-off
pending.** No migration. `committed_outflows`/cashflow untouched (byte-lock green).

- **Chat error nets (the 500).** The native chat endpoint (`api/routers/chat.py`)
  was the ONLY surface that turns an uncaught `process_message` throw into a raw
  500 (Telegram tolerates the same throw). `post_chat_message` + `post_chat_image`
  now guard it: re-raise `HTTPException`, otherwise `log.exception` + return
  `messages_es.CHAT_UNEXPECTED_ERROR` at HTTP 200. The extractor `except` in
  `bot/pipeline.py` was broadened past `(LLMClientError, ValidationError)` to a
  catch-all (a raw Anthropic SDK overload/429/529 on the "Este mes" follow-up was
  the likely escape). c78336a had patched internal seams but not the endpoint.
- **"Movimientos sin cuenta" (orphan rows, `account_id IS NULL`).** Orphans come
  from Gmail shadow / shortcut / chat-no-match and are excluded from every account
  balance (`compute_account_balances`). New `GET /transactions?no_account=true`
  filter; new read-only query tool `list_unassigned_transactions`
  (`app/queries/tools/transactions.py`, confirmed orphans only, registered before
  the `compare_periods` cache anchor); a cuenta-vs-categoría note in the QUERY
  system prompt (`app/queries/prompts/system.py`) — the extractor prompt is
  unchanged.
- **Chat → screen handoff.** When the orphan tool fires, the read path attaches
  `open_screen={screen:"assign_account"}` — `DispatchOutcome` gained an optional
  `open_screen`, and `bot/pipeline.py::_route_extraction` switched its query branch
  from the str-only `handle` to `run_dispatch` to carry it (the 6 tests that
  patched `query_dispatcher_handle` were repointed to `run_dispatch`). Native chat
  (`Chat.tsx`) cross-navigates to a real **"Sin cuenta" filter** on
  `TransactionsScreen` (`no_account=true`); each row reuses the existing
  `TransactionEditModal` / `AccountPickerModal` → `PATCH /transactions/{id}`
  account_id. No new write path.
- **Deferred:** the `category` (text) vs `category_id` (FK) "Sin categoría"
  mislabel (separate backfill/reconciliation).
- **Verification:** `tests/test_unassigned_transactions.py` + extended
  `test_query_robustness.py` / `test_phase_6a_routing.py` /
  `test_phase_6e_b5_transactions.py`; `scripts/test_phase_7b.sh` green; mobile
  `tsc` clean.

## Chat Command Menu (/menu), /resumen & Screen Launchers (2026-06-15)

Operator ask: chat placeholder → "/menu", a tappable `/menu` (commands + recommended
prompts), a `/resumen` expense table, shorter command aliases. **`/menu`+`/resumen`
committed (`adcd827`); the Gmail/launcher-hub extension uncommitted on `dev`;
on-device sign-off pending.** No migration. Deterministic — NO LLM in either path.

- **Where it runs.** `/menu` + `/resumen` (+ the launchers) live in
  `process_message`'s command short-circuit (`bot/pipeline.py`, before the LLM);
  builders in the new `bot/menu.py`. Native chips already post their label as text,
  so a menu = a `BotReply` with chips + an `open_screen="menu"` marker that keeps
  the chips **repeatable** (`Chat.tsx`).
- **`/menu`** = chat commands (`/resumen`, `/deshacer`, `/cancelar`, `/help`) +
  **screen launchers** (`/cuentas`, `/movimientos`, `/sobres`, `/gmail`,
  `/memoria` — a `_LAUNCHERS` dict maps each to an `open_screen` value; `Chat.tsx`
  cross-tab-navigates to the screen) + recommended example prompts. Telegram `/menu`
  lists the commands as text (Telegram auto-links `/comandos`) + points to the real
  Telegram commands for the screen features.
- **`/resumen`** → period chips (`/resumen_mes|_semana|_hoy`) → a plain-text expense
  table (monto · categoría · fecha · sobre; `📭` when unassigned), FX-totaled in the
  user's currency, via the new deterministic
  `api/services/transactions.py::period_expense_breakdown`. Empty →
  "Aún no tengo registros para …".
- **Short Telegram aliases** (additive; long names still work): `/desc_mail`,
  `/con_mail`, `/est_mail`, `/rev_mail`, `/ok_shadow`, `/no_shadow`, `/edit_mem`,
  `/recalc_mem`. Plus thin Telegram `/menu` + `/resumen` + `/gmail` handlers.
- **Decision:** the menu was first scoped chat-first (prompts + chat commands, NO
  screen launchers); reversed 2026-06-15 at operator request to a launcher hub.
- **Verification:** `tests/test_chat_menu_resumen.py` (6); mobile `tsc` clean;
  `scripts/test_phase_7b.sh` green.

## SINPE Móvil Direction Rule — LLM Extracts Parties, Rule Decides (2026-06-15)

Operator dogfood: an INCOMING SINPE Móvil receipt photo (the user is the recipient)
was classified as an **internal transfer** (cuenta→cuenta) and hit "La cuenta origen
y destino no pueden ser la misma." **Uncommitted on `dev`; on-device sign-off
pending.** No migration. Reinforces the LLM-extracts-rules-decide rule — the LLM
must NOT decide direction.

Root cause (mixed): the LLM set the intent (= direction) and the dispatcher trusted
it verbatim; the static extractor `SYSTEM_PROMPT` never receives the user's identity,
so a 3rd-person receipt ("EDGAR … a nombre de DANIEL") → the model can't tell DANIEL
is the user → "transferencia" → `log_transfer`; and there were no structured
counterparty fields to feed a rule.

- **Identity** (`api/services/identity.py`, no migration): `name_matches`
  (accent/case-insensitive, ≥2-token subset), `phone_matches` (last 8 digits, CR),
  `is_user(user, name?, phone?)` over `users.full_name` + `users.phone_number`.
  **Operator decision:** reuse existing identity (no migration); a multi-phone SINPE
  set is a future block.
- **Extractor emits raw parties**: `ExtractionResult` gains `is_transfer_receipt` +
  `sender_name/phone` + `recipient_name/phone` (+ tool schema + prompt rule #16 +
  a 3rd-person SINPE example + a `vision.py` hint). The LLM fills the parties + a
  `log_transfer` placeholder intent; it never decides direction.
- **Deterministic rule** (`api/services/dispatch/transfer_direction.py`, pure):
  `classify_transfer_direction` → recipient=user→income, sender=user→expense,
  both→internal, neither→unknown.
- **Dispatch**: `telegram_dispatcher.dispatch()` runs the rule when
  `is_transfer_receipt` and **overrides the intent** (income→`LOG_INCOME` → existing
  `_dispatch_log` asks ONE account; expense→`LOG_EXPENSE`;
  internal→`_dispatch_log_transfer`), bumps confidence + clears the flag.
- **Fallback = ask** (operator decision: never default in silence): unknown →
  `AskClarification(awaiting_field="transfer_direction")` with options
  Ingreso/Gasto/Entre mis cuentas; `bot/clarification.py::merge_reply` routes the
  answer (`_parse_transfer_direction_es`).
- **Verification:** `tests/test_sinpe_direction.py` (11); extractor / dispatcher /
  transfers / vision regression + `scripts/test_phase_7b.sh` (byte-lock) green.

## Reclassify Movement (Gasto ↔ Ingreso) — Chat + Native (2026-06-18)

Operator ask: reclasificar un movimiento entre gasto e ingreso desde el chat y
desde la app nativa. A movement's kind is the **sign** of `transactions.amount`;
a misclassified capture had no fix (native edit modal preserved the sign on
purpose; chat had no edit-existing intent). **On `dev`; operator on-device
sign-off received 2026-06-18.** No migration — balances are computed live from `SUM(amount)`,
so flipping the sign moves the amount between the income/expense sides for free.
`committed_outflows`/cashflow byte-lock untouched. Decision: vault
`Decision - Reclassify Movement (Gasto Ingreso)`.

- **Shared backend** (`api/services/transactions.py`): pure
  `reclassify_blocker(txn, to_income)` (mirrors the PATCH guards: confirmed-only /
  no transfer leg / no goal flow / not archived / "already that kind") reused by
  both the chat proposal builder and the commit; `reclassify_transaction(db, txn,
  to_income)` flips the sign + **clears `envelope_id` when becoming income**
  (sobres are expense-only), FLUSHES. `PATCH /transactions/{id}` gained a guard
  forcing `envelope_id` NULL whenever the resulting `amount > 0`, so the native
  toggle can't leave a stale sobre. Category left untouched (informational, not
  kind-validated).
- **Native** (`TransactionEditModal`): a **Tipo: Gasto / Ingreso** segmented
  control sets the sign on save; switching to Ingreso hides + clears the Sobre
  field. (Removes the old "switching income↔expense is out of scope here" note.)
- **Chat — post-capture chip**: after a capture commits, an `open_screen` hint
  drives a one-tap chip — **"Era un ingreso"** on an expense (rides the existing
  `assign_envelope` hint) / **"Era un gasto"** on an income (new
  `screen="reclassify"`). The chip `PATCH`es directly (tap = confirmation);
  Telegram ignores `open_screen` (native-only). Mobile: `ReclassifyPrefill`,
  `reclassify` descriptor + chip + `reclassifyMutation` in `Chat.tsx`.
- **Chat — typed phrase (both channels)**: deterministic **LLM-free** recognizer
  `_reclassify_target` in `process_message`'s short-circuit (TIGHT full-match,
  digit-free, no bare-negation — won't hijack a capture "ingresé 5000" or a query
  "¿cuál fue mi último ingreso?"; strips a leading discourse "no," but not a bare
  "no era…") proposes flipping the **last** committed movement (`last_action`
  handle, same as `/undo`) with a **Sí/No** confirm. New `reclassify`
  `PendingAction` action_type committed by `bot/commit.py::_commit_reclassify`
  (re-fetches + re-guards; updates `last_action` to the new kind so `/undo` still
  works). **The LLM never decides a reclassification** — reinforces
  [[Decision - LLM Extracts Rules Decide]].
- **Operator-approved scope (AskUserQuestion):** chat reclassifies the **last
  movement only** (arbitrary older movements → native app); trigger = **phrase +
  post-capture chip**.
- **Verification:** `tests/test_chat_reclassify.py` (4: recognizer table,
  expense→income flow, already-that-kind + no-recent-movement refusals);
  `tests/test_envelopes.py::test_patch_reclassify_to_income_clears_envelope`;
  `tests/test_phase_6f_chat_assign_envelope.py` updated (income now carries a
  `reclassify` hint, never `assign_envelope`); chat-write/dispatch/transfer/SINPE/
  menu + cashflow byte-lock regression green; mobile `tsc --noEmit` clean;
  production files ruff-clean. No migration. **Deferred:** reclassifying an
  arbitrary (non-last) movement from chat; auto-clearing the category on a flip;
  to/from transfer (separate primitive).

## Balance Anchor & Reconciliation (2026-06-19)

Operator ask: bottom-up balance reconstruction (`initial_balance + Σ every
captured txn`) **drifts** because ingestion never captures every transaction —
onboarding is painful and the shown balance ends up higher/lower than the bank.
Inverted to **standard bank reconciliation**: the real bank balance is the
**anchor** (source of truth), transactions are explanatory. **On `dev`; operator
on-device sign-off pending.** Migration `0035` (live on dev). No
`committed_outflows`/cashflow change (byte-lock green). Canonical:
`docs/balance-anchor-reconciliation-decisions.md`; vault `Decision - Balance
Anchor & Reconciliation`.

**Invariant (the only path that turns rows into an account balance —
`api/services/accounts.py::compute_account_balances`):**
`balance = latest_anchor.value + Σ(confirmed, non-archived txns with
transaction_date > anchor.effective_date)`. Strict `>` is **day-level** (rule
#6): a txn dated ON the anchor day is pre-anchor (already in the stated
balance). An account with **no anchor** falls back to `initial_balance + Σ(all)`
— byte-identical to the pre-anchor behavior, so nothing changed until a user
re-anchors.

- **`account_anchors` (migration `0035`, append-only):** `(value, currency,
  effective_date DATE, source [onboarding|reanchor|migrated], note, created_at)`
  on `account_id` (CASCADE) + index `(account_id, effective_date DESC,
  created_at DESC)`. Latest anchor = `DISTINCT ON (account_id)` (never a
  correlated subquery → no N+1). A re-anchor **appends** a row; never mutates.
  **Backfill** seeded one `migrated` anchor per existing account at
  `value=initial_balance`, `effective_date = MIN(transaction_date) − 1 day`
  (fallback `created_at::date` when no txns) — so the cutover reproduces every
  current balance **byte-for-byte**.
- **Single balance invariant (the multiple-sources-of-truth cure):** the four
  pre-existing balance paths now all read `compute_account_balances` —
  `dashboard/summary.py::_balance_split`/`_balance_total` and the chat tool
  `app/queries/tools/accounts.py::get_account_balance` (which had been computing
  `Σ amount` **without `initial_balance`** and counting shadow/archived rows — it
  disagreed with the home screen; fixed).
- **Re-anchor / heal-drift (B1):** state the real balance → a new `reanchor`
  anchor + a labeled **"ajuste de reconciliación"** transaction for the delta,
  **dated on the anchor's `effective_date`** so the strict-`>` formula excludes
  it from the balance (no `is_summed` flag). The ajuste is **also excluded from
  income/expense reports** (it is a balance correction, not P&L) via the
  reserved `AJUSTE_CATEGORY` marker, NULL-safe `is_distinct_from` in the four
  `summary.py` aggregators. Backend: `api/services/anchors.py::apply_anchor` +
  `POST /accounts/{id}/anchor`; `AccountResponse.needs_balance_confirmation`
  (true only for the `migrated` backfill anchor → the "confirmá tu saldo" nudge).
- **Native (A6/A7):** account create stays anchorless ("Saldo inicial" =
  reconstruction; same-day captures count) — **operator chose NOT to anchor at
  create** (an anchor at `eff=today` would exclude same-day captures). Migrated
  accounts show a **"Confirmá tu saldo real"** nudge on `AccountDetail`; a
  **"Corregí mi saldo"** action + `ReanchorModal` heal in one step (fund
  accounts only; credit is movement-driven). `mobile/src/components/ReanchorModal.tsx`,
  `mobile/src/api/accounts.ts::setAccountAnchor`.
- **Chat (A7):** `Intent.SET_BALANCE` — the LLM extracts the value (`amount`) +
  account (`account_hint`); deterministic `_dispatch_set_balance` proposes (account
  clarification with options; credit → redirect); on "Sí",
  `bot/commit.py::_commit_set_balance` → `apply_anchor`. The LLM never decides the
  balance ([[Decision - LLM Extracts Rules Decide]]). Append-only — **not in the
  /undo chain** (re-anchor again to correct).
- **Ingestion correctness (prerequisites, A1/A2):** the Gmail reconciler no longer
  invents a date — an undated email is `skipped (reason=no_date)`, never stamped
  `date.today()` (would land post-anchor + double-count); a cross-email duplicate
  (one payment, two emails) **flags** the newer shadow row `is_duplicate=True`
  (never silently skips — the user resolves it in review). The card-payment-received
  → transfer-leg recognition is **deferred to the SINPE/counterparty workstream**
  (the email extractor lacks structured parties; the shadow gate already prevents
  un-reviewed pollution).

**Verification:** `tests/test_balance_anchor.py` (10) + `tests/test_chat_set_balance.py`
(5) + `tests/test_gmail_reconciler.py` (22, incl. no-date + sibling-flag) + the full
balance-consumer regression (accounts/dashboard/transfers/cards/goals/envelopes/chat
routing) + byte-locked cashflow — 104 in the consolidated slice, green; mobile
`tsc --noEmit` clean; `alembic current → 0035 (head)`. **Deferred:** card-payment
transfer reconstruction (SINPE/counterparty workstream); credit-account re-anchor in
chat/native; chat set_balance in the /undo chain; the live BCCR fx rate for the
CRC-led roll-up (USD shown apart, never ₡+$ on the ₡500 placeholder).

## Statement Reconciliation — PDF → Balance Anchor (2026-06-20)

Operator ask: process bank-statement PDFs (e.g. a BAC "Estado de Cuenta" bundling
4 deposit accounts + 1 loan, or a Promerica dual-currency card) so the app can do
**balance reconciliation**. Extends [[Decision - Balance Anchor & Reconciliation]]:
a statement is the authoritative source for the anchor. **Branch
`feature/statement-reconciliation`; operator on-device sign-off pending.** Migration
`0037` (CHECK widen only). `committed_outflows`/cashflow byte-lock untouched.
Canonical: vault `Decision - Statement Reconciliation (PDF To Balance Anchor)`.

- **Scope (operator):** v1 = **balance anchor only** (no movement import);
  **deposit + credit + loan**; Telegram/WhatsApp = send PDF in chat → propose →
  confirm; native = a form listing every product with a per-line target dropdown.
- **Extraction** `api/services/llm_extractor/document.py::extract_statement` —
  reuses the `_extract_document` helper (Claude `document` block, Haiku→Sonnet
  0.65, logged to `llm_extractions` `intent="parse_statement"`, `pdf_b64` inline).
  Schemas in `api/schemas/statements.py` (`StatementProduct{kind,last4,currency,
  closing_balance,suggested_*}`, `StatementExtraction`, reconcile request/response).
- **Reconcile (the only writer)** `api/services/statements.py::reconcile_products`:
  **deposit** → `apply_anchor(value=closing_balance, source="statement",
  today=corte_date, write_ajuste=False)`; **credit** → same but **value negated**
  (card balance is NEGATIVE when owed — `owed = -current`, see `credit_cards.py`);
  **loan** → set `Debt.current_balance` + audit note (the ONE sanctioned write to
  an otherwise-immutable debt financial field; not on the PATCH whitelist).
  `write_ajuste=False` because the corte balance IS the truth at corte (pre-corte
  txns absorbed by strict-`>`, post-corte ride on top) — a corte-dated ajuste of
  `S − balance_now` would be a confusing wrong line; the antes→después delta is
  computed from a pre-reconcile snapshot. `suggest_targets` fuzzy-matches
  (`match_account_hint`, 0.85; no IBAN column). New `source="statement"` anchor
  provenance → migration `0037` widens `ck_account_anchors_source`.
- **Endpoints** (`api/routers/accounts.py`): `POST /accounts/parse-statement`
  (multipart PDF, 4MB/PDF-only 415/413, read-only, returns suggestions);
  `POST /accounts/reconcile-statement` (applies the batch; 400 on a bad target).
- **Native** (`mobile/`): `screens/StatementReconcileScreen.tsx` (copies the
  `DebtCreateScreen` PDF scaffold; per-product rows with `AccountPickerModal` /
  new `DebtPickerModal`, toggle + "Reconciliar (N)", low-confidence note),
  `api/statements.ts`, registered in `AccountsNavigator`, entry on `AccountsScreen`
  ("Reconciliar con estado de cuenta").
- **Chat (Telegram now; WhatsApp inherits)** `bot/pipeline.py::handle_statement_document`
  (channel-agnostic — caller supplies bytes): extract → `suggest_targets` →
  propose confident matches as ONE Sí/No batch (unmatched → "usá la app"); confirm
  via the existing `pending:` flow → `bot/commit.py::_commit_reconcile_statement` →
  `reconcile_products`. New `PendingAction` action_type `reconcile_statement` (not in
  /undo, like `set_balance`). aiogram `Message(F.document)` handler in
  `bot/handlers.py` downloads the PDF. New voseo copy in `bot/messages_es.py`.
- **Verification:** `tests/test_statement_reconcile.py` (10: extraction+audit,
  deposit-at-corte, credit-negative, loan-balance, multi-account, foreign-reject,
  suggest, both endpoints, chat propose→confirm) + chat/balance/cashflow-byte-lock/
  gmail-reconciler regression (43) green; mobile `tsc --noEmit` clean; `alembic →
  0037`. **Deferred:** transaction-level movement import; native text-trigger intent
  (no extractor change); per-product "Es otra cuenta" chat remap; `accounts.iban`
  for exact matching.

### Generalized — Semantic Primitives + Policy Table (2026-06-25, merged to `dev` `1ad6f95`)

The flat `StatementProduct{kind,last4,currency,closing_balance}` model above is
**superseded** so reconciliation works for **any bank, any account type** with no
bespoke parser. **Operator on-device sign-off pending.** Migration `0038`
(identity columns). `committed_outflows`/cashflow byte-lock untouched. Canonical:
vault `Decision - Statement Reconciliation Generalized (Semantic Primitives)`.
Dissolves the two production bugs: **Bug 1** wrong-balance-field, **Bug 2**
account-duplication.

- **Two design moves.** (1) The LLM normalizes every statement into the SAME
  **semantic primitives** — `StatementExtractionV2{accounts[]{account_type,
  issuer, product_name, identifiers[], instruments[], currency_legs[]{currency,
  opening_balance, flows[]{amount, direction, contingent, label_raw},
  closing_candidates[]{amount, role}}}, period_end, …}`. The LLM tags
  `account_type`/`direction`/`role`/`contingent` and copies amounts verbatim; it
  **computes nothing**. (2) All bank/type behavior is deterministic code: a
  **policy table** + **one universal conservation check**.
- **Policy table** `api/services/statement_policy.py` — the ONLY type-specific
  code. `account_type → (target_role, fallback_roles, sign asset|liability,
  ledger_entity anchor|debt, reconcile_kind deposit|credit|loan)`:
  checking/savings→(`closing`, asset, anchor, deposit); credit_card→(`payoff`,
  liability, anchor, credit); loan/line_of_credit→(`principal_outstanding`,
  liability, debt, loan); investment→(`market_value`, asset, anchor, deposit;
  experimental). `reconcile_kind` maps the rich type back to the legacy 3-value
  `StatementKind` so the **writer is untouched**. `select_target(leg, policy)`
  returns `(magnitude quantized to cents, matched_role, ambiguous)`.
- **Pipeline** `api/services/statement_normalize.py::build_reconcile_plan(extraction,
  *, accounts, debts)` = NORMALIZE → DEDUP → VALIDATE → POLICY → RESOLVE → a
  `ReconcilePlan` of one `LegPlan` per (account × currency leg). **Conservation**
  (`check_conservation`): asset-orientation `expected = opening_signed + Σ(inflow −
  outflow)` over **non-contingent** flows (a LIABILITY's opening is negative, so a
  card payment reduces what's owed); compare **magnitudes** within 5 céntimos. It
  **validates the LLM's role tag** (the Bug-1 backstop) and NEVER fabricates — the
  role candidate is copied verbatim; mismatch → `needs_review`, not a write.
  **Payoff correction:** `payoff = opening + Σ(non-contingent)`, the contingent
  current-period interest excluded — NOT the draft's `financed − Σ(contingent)`
  (which double-subtracts; ₡193 289,65 vs the wrong ₡187 849,09 on the Promerica
  fixture). **Dedup (Bug 2):** group by identity signature (IBAN→account_number→
  last4/PAN), identity-less accounts get a unique key so distinct same-named
  accounts never collapse; instruments are **attribution-only** (no balance);
  exactly one entry per (account × leg); dual-currency = two legs.
- **The collapse seam.** The rich schema is an internal extraction+validation
  representation that **collapses to the existing per-target
  `StatementReconcileItem`** via the single point `auto_includable_items` /
  `leg_to_item` (chat + REST/native both go through it). So `apply_anchor`, the
  loan `Debt.current_balance` path, the native confirm form, and the chat confirm
  stay essentially unchanged. The **parse response enriches**
  (`conservation_ok`/`needs_review`/reason/`attributed_instruments`/`match_
  confidence`); the **write request collapses**.
- **Identity matching (operator chose columns + migration over deferring).**
  Migration `0038` adds nullable `iban`/`account_number`/`last4` to `accounts` +
  `debts`. RESOLVE = identifier priority (IBAN → account_number → unique last4 in
  the policy+currency-filtered set) → issuer+product fuzzy (`match_account_hint`
  0.85). The writer **self-stamps** NULL identity columns (fill-if-null) so the
  NEXT statement matches deterministically — but ONLY for a **confident identity
  match (confidence ≥ 1.0) the user didn't override** (so a by-elimination/fuzzy
  match can't poison the wrong account). Resolves the old "`accounts.iban` for
  exact matching" deferral.
- **Writer guards** `api/services/statements.py::reconcile_products`: **currency
  mismatch** → `ReconcileError` (fixes the silent USD-onto-CRC bug); identity
  self-stamp; results echo `conservation_ok`/`needs_review`. **No silent drops** —
  `needs_review`/unresolved/empty-leg products are surfaced (chat "revisalos en la
  app"; native "Revisar" badge, default OFF, `closing_balance=null` →
  non-toggleable so it can't anchor a fabricated ₡0). Chat commit wrapped →
  `STATEMENT_RECONCILE_FAILED` Spanish copy (never a raw crash if a target went
  archived/foreign between propose and confirm).
- **LLM prompt** `document.py` — `_STATEMENT_TOOL`/`_SYSTEM`/`_PROMPT` rewritten to
  emit V2 (one account per account, instruments not accounts, ₡+$ = two legs, tag
  direction/role/contingent). `_extract_document`/`_run_one` (Haiku→Sonnet 0.65,
  `pdf_b64`, `intent="parse_statement"`) unchanged. **Hard rule reaffirmed:** the
  LLM never decides direction/role/dedup/the written number — [[Decision - LLM
  Extracts Rules Decide]].
- **Code-review fixes** (workflow-backed, high): a pre-merge multi-agent review
  caught + I fixed seven confirmed bugs — identity-aware grouping, empty-leg
  surfacing, null-balance projection, confidence-gated stamping, ambiguous-role
  flag, cents-quantization (no `decimal_places=2` crash), chat-commit error net.
- **Verification:** `tests/test_statement_plan.py` (13, DB-free) +
  `tests/test_statement_reconcile.py` (12, DB) + `scripts/test_phase_7b.sh` (48
  focused + 141 regression, cashflow byte-lock intact) + mobile `tsc --noEmit`
  clean; `alembic current → 0038`. **Deferred (unchanged):** movement-level import;
  live BCCR FX (₡500 placeholder); investment is experimental.

## Apple Pay → Zero-Touch Capture (iOS, 2026-06-25)

Operator ask: an iOS Shortcuts **Wallet/Transaction trigger** automation logs a
contactless (NFC) Apple Pay purchase into ledger_cr with zero touch, via a native
**App Intent** that POSTs merchant + amount to the backend. An additional **fast
signal, not a replacement** — the trigger fires on physical NFC taps only (not
web/in-app Apple Pay, not chip/swipe), so **Gmail ingestion stays the system of
record** and the same tap's later bank email reconciles into the same row. **No
LLM on this path** (iOS is the extractor; deterministic rules decide). **Merged to
`dev` (`feature/apple-pay-capture`); native build + operator on-device sign-off
pending** (no native CI). Migration `0039`. `committed_outflows`/cashflow
byte-lock untouched. Canonical: vault `Decision - Apple Pay Zero-Touch Capture`.

- **Capture endpoint** `POST /api/v1/transactions/apple-pay` (bearer auth,
  `current_user`): writes `source='apple_pay'`, `status='confirmed'` (counts in
  balance AND reports immediately — operator decision) with sign=expense. Amount
  is a STRING parsed to Decimal server-side (`api/services/money.py::
  parse_money_magnitude`, CR-locale-aware; never float); currency CRC/USD stored
  native (NO fx at capture). Best-effort card→account routing via
  `match_account_hint` (currency-scoped), else `account_id=NULL` (the "Sin cuenta"
  flow). `flag_and_notify` still runs (same-channel dupe detection).
- **Idempotency** (offline retry): a per-tap `client_event_id` → `source_ref=
  "apple_pay:{id}"` on the existing per-user UNIQUE partial index
  `uq_transactions_user_source_ref` (migration 0006, previously unused). Explicit
  SELECT-then-INSERT + `IntegrityError` re-select for the race. NOT Redis (a
  durable financial write, unlike the transient bill mark-paid replay).
- **Source CHECK** widened to add `apple_pay` (migration `0039`, the 0033 pattern).
  No status migration (the row is `confirmed`). Balance filter UNCHANGED
  (`compute_account_balances` already sums `status='confirmed'`).
- **Reconciliation merge (Gmail)** `api/services/gmail/reconciler.py::
  _find_apple_pay_provisional` — new branch in `reconcile()` BETWEEN
  `_check_duplicate_gmail` and `_find_existing_match`: matches the incoming bank
  email to a confirmed `apple_pay` row (same currency, signed amount ±
  `AMOUNT_TOLERANCE`, date within **±5 days** `APPLE_PAY_LOOKBACK_DAYS` — Apple Pay
  precedes the email; merchant a TIEBREAK not a gate) and **promotes it in place**
  (`source→reconciled`, attach `gmail_message_id`, stays confirmed) — **never a
  second row**, so the balance never double-counts. Ambiguity guard: with >1
  amount+window match, merge ONLY when exactly one is merchant-similar, else fall
  through to a shadow insert (user disambiguates). New
  `ReconcileOutcome.APPLE_PAY_MERGED` → `scanner.py` maps it to seen `'matched'`;
  `apple_pay` excluded from `_find_existing_match`.
- **Native (mobile/, B1 — on-device verification required, no native CI):** Expo
  config plugin `plugins/withApplePayIntent.js` injects the App Intent Swift
  (`plugins/ios/ApplePayCaptureIntent.swift` — an `AppIntent` +
  `AppShortcutsProvider`) into the **main app target** + `LedgerApiBaseUrl` into
  Info.plist. The Intent reads the 30-day bearer JWT from the app's OWN keychain
  (service `ledgercr.appintent`, written by `src/lib/appIntentToken.ts` at login,
  `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`; queried **by service alone** so it doesn't
  depend on expo-secure-store's account naming), parses the amount locale-aware,
  generates the idempotency UUID, POSTs `Authorization: Bearer`, queues+retries on
  offline failure, voseo dialogs on no-session/401. Reuses the existing
  device-code session JWT (operator decision).
- **Deployment (no Apple change):** the Intent is in the main app target reading
  its own keychain, so **NO new capability/entitlement** — no Keychain Sharing, no
  App Groups, no Apple Pay/PassKit. Same EAS Build → TestFlight flow. It is
  **native** code → needs a NEW build (not an OTA `eas update`) + a `buildNumber`
  bump. Setup doc `docs/apple-pay-setup.md` (es-CR voseo).
- **Hard rule added:** a contactless Apple Pay tap is captured via the dedicated
  `/transactions/apple-pay` endpoint (iOS extractor, deterministic write), never
  the LLM path; the bank email is authoritative and merges INTO the provisional
  row.
- **Verification:** `tests/test_apple_pay_capture.py` (10: BAC ₡ merge=one row,
  USD no fake FX, offline replay idempotent, unparseable→400 no row, ±5d
  false-merge guard, unique-merchant merge) + reconciler/dedup/balance/accounts/
  transactions/gmail-native regression (100) + cashflow byte-lock (28) green;
  mobile `tsc --noEmit` clean; `alembic → 0039 (head)`. **Deferred:**
  merchant→category classification (deterministic lookup later); multi-card
  disambiguation beyond tagging; a refresh-token flow (30-day expiry accepted);
  web/in-app Apple Pay + chip/swipe (Gmail covers those).

## CR Salary Calculator + Ingresos CRUD (post-7a, 2026-06-09)

Deterministic Costa Rican net-pay calculator + full Ingresos CRUD. Backend +
mobile **code-complete; operator on-device sign-off pending**. Decision note:
`~/Finance_project/30_Projects/Finance-Agent/05_Decisions/Decision - CR Salary
Calculator.md`.

**Rules layer (pure, no LLM/DB/network):** new `app/domain/payroll/` —
`cr_salary.py::compute_net_salary(gross_monthly, year=None, hijos=0,
conyuge=False, isr_base_mode="gross", solidarista_pct=0)` → typed
`SalaryBreakdown` dataclass (CCSS sem/ivm/banco_popular + ISR base/tax/per-tramo
+ solidarista + net + effective_rate). `rates.py` holds year-keyed tables
(`dict[int, YearRates]`; 2026 set: CCSS 10.83%, Decreto 45333-H brackets,
créditos ₡1.710/₡2.590); unconfigured year → `UnconfiguredYearError` (never a
silent stale year — adding 2027 is a data edit). Marginal (not flat) ISR;
créditos floored at 0; **solidarista** optional (% of gross, deducted from
take-home, NOT in the CCSS/ISR base); integer colones, components reconcile to
net exactly. ISR base defaults to gross (Hacienda); `gross_minus_ccss` mode
available (elempleo-style) but not default.

**Surfaces (single source of truth):** chat query tool `compute_net_salary`
(read-only, registered before the `compare_periods` cache anchor; LLM narrates
the breakdown verbatim, never recomputes) + `POST /api/v1/payroll/net-salary`
(`api/routers/payroll.py`, `current_user` auth, returns `asdict(breakdown)`).
The mobile app calls the endpoint — it **never** reimplements the brackets in TS
(they change yearly; a copy would drift). `api/payroll.ts` + reusable
`components/SalaryCalculator.tsx`.

**Income capture (gross → net):** for a **CRC salary** captured via chat or the
manual form, the entered amount is treated as **gross**; the calculator computes
the **net**, stored as `recurring_incomes.amount` (the take-home that drives
budgets/affordability); the gross is kept in `recurring_incomes.gross_monthly`
(**migration 0025**, nullable) for re-edit/recompute. USD salaries + non-salary
income stored untouched. Chat: `_dispatch_create_income` (salary+CRC) computes
net via `_net_from_gross_salary`, proposes "salario bruto X → neto Y",
`_commit_income` writes both. `RecurringIncomeCreate/Update` + the REST create
carry `gross_monthly`.

**Ingresos CRUD (mobile, structured-form exception):** the native Ingresos
screen now has full CRUD — `IncomeFormModal` (create + edit; salary leads with
the embedded `SalaryCalculator`, income_type/currency read-only on edit),
create entry ("+ Nuevo" + empty CTA), per-row Edit (hidden for derived CR
cycles), Restore for archived rows, plus a standalone "Calculadora de salario
neto". This is a **deliberate exception** to [[Decision - Conversational Creation
Over Forms]], justified by the *same field-complexity rationale as debt* (the
gross→net calculator). Chat creation stays the default entry. **Modal keyboard
note (fix 2026-06-09):** every bottom-sheet modal with a text input
(`SalaryCalculator` host, `IncomeFormModal`, standalone calc modal) wraps in
`KeyboardAvoidingView` + a `ScrollView` (`keyboardShouldPersistTaps="handled"`)
— without both, the sheet renders behind the keyboard. Copy the
`TransactionEditModal` scaffold for any new input modal.

**Gastos fijos CRUD (mobile, structured-form exception, 2026-06-09):** the
`BillsScreen` gained a "+ Nuevo" entry + a "Todos / Próximos pagos" tab switch
(the "Todos" tab lists every recurring bill incl. paused via a "Ver pausados"
toggle, tapping → `BillDetailScreen` with `occurrence=null`); `BillDetailScreen`
gained an "Editar gasto fijo" action. New `components/BillFormModal.tsx` (create
+ edit) posts to the existing `POST/PATCH /recurring-bills` — category pills come
from `GET /onboarding/categories` (single source of truth, no hardcoded list),
`custom`/RRULE cadence excluded, variable-amount toggle, PATCH regenerates
occurrences. `api/bills.ts` grew `createRecurringBill`/`updateRecurringBill`/
`fetchBillCategories`. Same field-complexity exception to
[[Decision - Conversational Creation Over Forms]] as debt/income (chat stays the
default); no backend change. Mobile `tsc` clean.

**Related change — editable debt cuota (2026-06):** `DebtUpdate` whitelist gained
`minimum_payment` (the only editable financial field; the router validates it:
positive, below the balance, covers the monthly interest). See the Phase 6e B7
note above.

**Verification:** `tests/test_cr_salary.py` (22, incl. golden ₡1.5M = net
₡1.271.700 on gross base; elempleo's ₡1.295.595 = `gross_minus_ccss`) +
`tests/test_payroll_tool_and_endpoint.py` + the income chat gross→net suite +
debt cuota tests. `alembic current → 0025 (head)`. Mobile `npx tsc --noEmit`
clean.

---

## Closed phases — hard rules to preserve

These are extracted from the closed-phase notes in `11_Phases/`. **Do not relax without an explicit decision in `05_Decisions/`.**

### From Phase 5a (auth model)

- **`X-Shortcut-Token`** (server-resolved against `users.shortcut_token`) is required by `POST /transactions/shortcut` and every `POST /jobs/*`. The dev `X-User-Id` shim is rejected here.
- **`X-User-Id`** dev shim is well-formed UUID matching a `users.id`. Comment-flagged in `api/dependencies.py::current_user`. Do NOT build features that depend on it.
- Resolution order in `current_user`: `X-Shortcut-Token` → bearer JWT (Phase 6f) → `X-User-Id` dev shim. (The SPA `fa_session` cookie branch was removed at 6f B16.) The strict `current_user_via_token` ignores the shim entirely.
- `users.status = 'suspended'` returns 403, not 401, on every authenticated route.
- `shortcut_token` is opaque (≥48 bytes `secrets.token_urlsafe`), returned **only once** at register/rotate. Rotation invalidates the previous token instantly.

### From Phase 5b (Telegram + LLM)

- The LLM extracts; deterministic routing decides write/query/control. **The write dispatcher never asks an LLM what to do.** The query dispatcher is the explicit read-only LLM/tool-use path after routing already selected `dispatcher="query"`.
- **Redis is the source of truth for durable bot state.** aiogram FSM is permitted ONLY for transient in-handler dialog bookkeeping. Key conventions in `bot/redis_keys.py`.
- Prompt caching ON from day 1. Don't ship uncached.
- **No synonym/normalization maps** for `category_hint`, `merchant`, etc. Pass LLM output through; address drift with real examples.
- `ExtractionResult.dispatcher` is required: `write | query | control`. Queries use `intent="query"` + `dispatcher="query"`; legacy `query_recent`/`query_balance` were removed in 6a.

### From Phase 5d (nudges)

- The LLM writes the final Spanish text; it **NEVER** decides whether or when to nudge.
- Four anti-saturation rules are **hard-coded, not user-configurable** (rate limit, per-type silencing, quiet hours, dedup). Constants in `api/services/nudges/policy.py`.
- Quiet hours (21:00–07:00 in user's timezone) apply to HIGH priority too — no emergency delivery at 3am.
- Three buttons per nudge max. WhatsApp Cloud API portability constraint.

### From Phase 6a (query dispatcher)

- Read-only. The query dispatcher cannot mutate state.
- Telegram delivery always runs `sanitize_telegram_html → split_for_telegram → sequential sends`. Inline buttons attach only to the last chunk.
- `/clear` clears only query history. Never touches pending writes, clarification state, pairing, or rate limit keys.

### From Phase 6b (Gmail ingestion)

- **No pre-loaded sender lists keyed off the bank label.** Banks rotate senders without warning. `api/data/bank_directory_cr.py` is a visual directory with optional hint regexes, NOT a source of truth.
- **No auto-confirm of shadow rows.** The 7-day window is a feature; the user decides per-batch with `/aprobar_shadow` or `/rechazar_shadow`.
- Discovery uses Gmail metadata only (`format='metadata'`). It never fetches bodies and never invokes the transaction extractor.

### From Phase 6c (user memory)

- Two writers, never crossed. Computed never calls an LLM; the extractor never queries aggregates.
- LLM-extracted confidence capped at 0.85 base, 0.95 after reinforcement. Computed reaches 1.00 with full evidence. `user_locked=true` rows hold 1.0 forever.
- User-locked rows are sacred. Persister, lifecycle, and computed worker all skip them.
- Insights NEVER inline in the system prompt. They flow through the `get_user_context` tool only.
- `stated_preference.raw_quote` is wiped at 30 days; deletion audit payloads redact it too.
- Validation failure in the extractor returns `[]`; no partial salvage.

---

## Phase 4 Endpoints (reference — still active)

All under the `/api/v1` prefix. Auth:
- Resource read/write: `X-Shortcut-Token` (real) or `X-User-Id` (dev shim).
- `/jobs/*` and `POST /transactions/shortcut`: `X-Shortcut-Token` only.

| Method | Path | Purpose |
|---|---|---|
| POST/GET/GET/PATCH/DELETE | `/recurring-bills[/{id}]` | CRUD. POST auto-generates next ~6 months of occurrences. PATCH regenerates future pending occurrences when schedule fields change. DELETE is soft. |
| GET | `/bill-occurrences[?status&from_date&to_date&recurring_bill_id&category]` | Filterable list. |
| POST | `/bill-occurrences/{id}/mark-paid` | Body `{transaction_id?, amount_paid?, paid_at?, notes?}`. >20% divergence returns a non-blocking warning. |
| POST | `/bill-occurrences/{id}/skip` | Marks as `skipped`. |
| POST/GET/GET/PATCH/DELETE | `/custom-events[/{id}]` | CRUD, soft delete. |
| POST/GET/GET/PATCH/DELETE | `/notification-rules[/{id}]` | CRUD. Scope-specific fields enforced by Pydantic + DB CHECK. |
| GET | `/calendar/upcoming?from=&to=&include_overdue=true` | Unified feed of bill occurrences + custom events. |
| GET | `/notifications/pending[?channel]` | `notification_events` with `status=pending` and `trigger_date <= today` (CR). |
| POST | `/notifications/{id}/acknowledge`, `/dismiss` | Status transitions. |
| POST | `/jobs/generate-occurrences?horizon_months=6` | Idempotent. |
| POST | `/jobs/mark-overdue` | Flips `pending` + past-due → `overdue`. |
| POST | `/jobs/compute-notifications` | Materializes `notification_events` idempotently. |

---

## Architecture Principles

1. **Data before AI** — Conversational features are downstream of accurate data pipelines.
2. **Deterministic pushback** — The affordability engine uses math, not LLM generation. The LLM never calculates.
3. **Personal before product** — Use the system yourself 4+ weeks before adding multi-tenancy.
4. **No premature complexity** — Rule-based categorization before ML. Single currency before multi. Telegram before WhatsApp. API LLM before self-hosted.
5. **Every phase ships something usable** — Each phase produces a feature you can use that day.

---

## The Pushback Engine (Phase 6 — Critical Design)

This is the hardest feature. The engine is **deterministic**, not LLM-generated.

> **Superseded 2026-06-09 (Phase 7 — Unified Monthly Cashflow).** The `disposable
> = income − fixed − commitments` denominator below is historical. The live
> verdict is judged against the **envelope-aware surplus** —
> `surplus = income − committed_outflows` where `committed_outflows = envelope
> allocations` (Model A) — and is **gated** (`no_income`/`no_budget`/
> `under_coverage`) when the budget isn't trustworthy. One source of truth:
> `api/services/finance/cashflow.py::compute_monthly_cashflow`. The 80% margin +
> "LLM explains, rules decide" are unchanged. See the vault note *Decision -
> Unified Monthly Cashflow*. The pseudocode below is kept for historical context.

```python
def assess_affordability(monthly_income, fixed_expenses, existing_commitments, desired_amount, desired_timeline_months):
    monthly_disposable = monthly_income - fixed_expenses - existing_commitments
    monthly_needed = desired_amount / desired_timeline_months
    feasible = monthly_needed <= monthly_disposable * 0.8  # 80% safety margin
    return {
        "feasible": feasible,
        "monthly_disposable": monthly_disposable,
        "monthly_needed": monthly_needed,
        "shortfall": max(0, monthly_needed - monthly_disposable),
        "suggestion": generate_suggestion(...)
    }
```

The LLM's system prompt enforces this separation:
- NEVER invent numbers. Always call a tool to get real data.
- When asked "can I afford X", call the affordability tool and report its findings honestly.
- If not feasible, say so clearly and offer alternatives (extend timeline, reduce target).
- Don't sugarcoat. Don't be harsh. Be direct and constructive.

---

## Coding Conventions

- **Python**: 3.12+, type hints everywhere, `async/await` for all DB and HTTP operations
- **Imports**: stdlib → third-party → local, separated by blank lines
- **Models**: SQLAlchemy 2.x `Mapped` + `mapped_column` syntax (not legacy `Column()`)
- **Schemas**: Pydantic v2 with `model_validate`, not v1 `.from_orm()`
- **Routes**: All under `/api/v1/` prefix. Use `APIRouter` with `prefix` and `tags`.
- **Auth**: iPhone Shortcut uses `X-Shortcut-Token` resolved against `users.shortcut_token`. The native app sends `Authorization: Bearer <jwt>` (from magic-link/device-code exchange). `current_user` resolves in order `X-Shortcut-Token` → bearer JWT → dev `X-User-Id` shim. (The Phase 6d SPA `fa_session` cookie path was removed at 6f B16.)
- **Errors**: Raise `HTTPException` with appropriate status codes. Don't return error dicts.
- **Database**: Always use `get_db` dependency. Never create sessions manually in routes.
- **Migrations**: Hand-written Alembic. Every schema change gets a migration file.
- **Env vars**: All config via `.env` → `config.py`. Never hardcode secrets.
- **Testing**: Write tests as you go. Each phase's "done when" should be verifiable.
- **Currency**: Default CRC (Costa Rican Colón). `America/Costa_Rica` timezone. Single currency for now.

---

## What NOT to Build (Until Phase 8 Is Done)

- ❌ WhatsApp channel (use Telegram)
- ❌ Web UI. The Phase 6e SPA was retired at 6f B16 (`web/` deleted 2026-06-01); the native iOS app is the only structured surface. Do not re-add a web client without an explicit decision.
- ❌ Investment portfolio analysis beyond manual tracking
- ❌ ML-based categorization (rule-based first)
- ❌ Family/household support
- ❌ PDF bank statement import (email parsing first)
- ❌ Vector database / RAG / semantic search
- ❌ Fine-tuned models
- ❌ Android app (iOS-only in Phase 6f; revisit at P8)
- ❌ Multi-currency support
- ❌ Self-hosted LLM infrastructure
- ❌ LLM inside the write dispatcher — write dispatch stays deterministic forever
- ❌ Synonym/normalization maps for `category_hint`, `merchant`, etc.
- ❌ aiogram FSM for durable state — Redis is the source of truth

---

## Environment Variables (Reference)

Full per-phase env vars live in the corresponding `11_Phases/Phase-*.md`. Cross-cutting:

```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/finance
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=...
ENVIRONMENT=development         # development | production

# Telegram (Phase 5b/6a) — see Phase-5b-Telegram-LLM.md
TELEGRAM_MODE=disabled          # disabled | polling | webhook
TELEGRAM_BOT_TOKEN=...
LLM_EXTRACTION_MODEL=claude-haiku-4-5
LLM_QUERY_MODEL=claude-sonnet-4-5
LLM_DAILY_TOKEN_BUDGET_PER_USER=100000

# Gmail (Phase 6b) — see Phase-6b-Gmail-Ingestion.md
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_OAUTH_STATE_SECRET=...
# env|file are DEV-ONLY (os.environ / plaintext .dev_secrets.json). Production
# MUST use azure_kv (+ AZURE_KEY_VAULT_URL) — enforced by a Settings validator
# that refuses to boot if ENVIRONMENT=production and the backend isn't azure_kv,
# so a misconfigured deploy can't silently store Gmail OAuth refresh tokens on disk.
SECRET_STORE_BACKEND=env|file|azure_kv

# Insights (Phase 6c) — see Phase-6c-User-Memory.md
INSIGHTS_EXTRACTOR_ENABLED=false
INSIGHTS_DISPATCHER_ENABLED=false

# Magic-link / bearer-token session signing (Phase 6d B3 / 6f B2 + B3)
# HMAC secret used by issue_session_jwt + decode_session_jwt for the
# Authorization: Bearer JWT body returned from magic-link + device-code
# exchange. (6f B16 removed the fa_session cookie + SESSION_COOKIE_* +
# SPA_BASE_URL/SPA_CORS_ORIGINS with the SPA.)
MAGIC_LINK_SESSION_SECRET=<strong random>
SESSION_TTL_S=2592000           # 30 days (bearer JWT lifetime; the native biometric lock gates access)

# Native app deep links (Phase 6f B15) — custom URL scheme in mobile/app.json.
# Bot mints `<scheme>://exchange?token=...` so a tap opens the native app.
NATIVE_APP_SCHEME=ledgercr

# Migration 0006 only — read by Alembic, NOT by the running app:
LEGACY_USER_EMAIL=...
LEGACY_USER_NAME=...
LEGACY_SHORTCUT_TOKEN=...
DEFAULT_USER_ID=...             # optional, only to preserve a pre-5a row
```

**Mobile env (Phase 6f) — lives in `mobile/.env.local`, NOT in the
backend `.env`:**

```
EXPO_PUBLIC_API_BASE_URL=http://<dev-LAN-IP>:8000   # the Expo client appends /api/v1
EXPO_PUBLIC_SENTRY_DSN=                              # Phase 6f B15 scaffold; empty = console no-op in Expo Go
```

Pre-Phase-5a vars `WEBHOOK_SECRET` and `SHORTCUT_TOKEN` were removed; tokens now live on `users.shortcut_token`.

**Local testing reference:** `docs/LOCAL_DEV.md` is the canonical cookbook
for running the stack locally — including §8 which walks through the
iPhone + Expo flow end-to-end.

---

## Technical Debt (tracked, not blocking)

- **No real scheduler yet.** The Phase 4 batch jobs (`generate-occurrences`, `mark-overdue`, `compute-notifications`) are exposed as `POST /api/v1/jobs/*` protected by `X-Shortcut-Token`. Triggered by external cron / iPhone Shortcut / Container Apps Jobs. Phase 5+ may wire arq or Celery on top of Redis.
- **No `POST /jobs/*` idempotency key.** Safe to re-run because operations are idempotent, but we don't throttle concurrent calls.
- **Legacy `events` table removed in migration 0005** (replaced by `custom_events`).
- **`recurring_bills.linked_loan_id` FKs to `debts.id`** (no separate `loans` table).
- **RRULE validation deferred until generation time** — a bad rule surfaces as exception during `generate_occurrences`, not at write time.
- **`X-User-Id` dev shim shipped to production code.** Remove when magic-link auth (Phase 6d B3) is fully adopted by all callers.
- **Phase 5d: no `pending_confirmations` retention policy.** Acceptable until ~10k rows/year.
- **Phase 5d: "Más tarde" / "Recordame mañana" don't schedule deferred re-nudges** — they mark `dismissed` and count toward silence. Needs a scheduler.
- **Phase 5d: `upcoming_bill` can't re-nudge while status=dismissed.** By design — user said no.
- **Phase 5d: multi-tier notifications per bill produce N distinct nudges.** Rate limit caps delivery; a future fix collapses them before insert.
- **Phase 6a splitter is wired but mostly no-op** — `app/queries/llm_client` uses a low `max_tokens` cap. Add a >3900-char real-response e2e before raising the cap.
- **Phase 6e B4: `accounts.is_active` and `accounts.archived` are mirrored.** Per Phase 6e §5.4 `archived` is canonical, but the bot's `services/accounts.py::list_active` and the `accounts(user_id, name) WHERE is_active` partial unique index still read `is_active`. The `accounts` router writes both on archive/restore to keep them in sync. Cleanup target: drop `is_active`, point all readers + the partial index at `archived`. Defer until B5 or a dedicated cleanup block.
- **Phase 6e B5: materialized dashboard views don't yet exclude `transactions.archived=true`.** Live current-month dashboard queries and `compute_account_balances` correctly exclude archived rows in-code, so the user-visible balances stay right. But `mv_monthly_summary_by_user` / `mv_yearly_summary_by_user` (migration 0017) were defined before the archive column existed and will count archived rows after the nightly refresh. Impact is bounded: archived rows are rare and these views only feed historical-month summaries. Cleanup target: a small migration that drops + recreates both views with `archived = false` in the WHERE clause.
- **Phase 6e B13: SPA build needs `NODE_OPTIONS=--experimental-global-webcrypto` on Node 18** because `workbox-build` (via `serialize-javascript`) reads `globalThis.crypto`. The npm `build` script wraps with `cross-env` so it's transparent locally and in CI; the flag becomes a no-op on Node 20+. Cleanup target: drop the `cross-env` wrapper once CI guarantees Node 20+. Also: PWA icons in `web/public/icons/` are placeholder PNGs generated by `scripts/generate_pwa_icons.py` (a navy disc with a blue dot). Swap for real brand assets before any public-facing release.
- **Phase 6f: dual auth path during cutover — RESOLVED at B16 (2026-06-01).** The `fa_session` cookie path was removed: no cookie-set in `exchange_magic_link()`, no cookie branch in `current_user`, `session_cookie_*` settings dropped (`session_cookie_ttl_s` → `session_ttl_s`). `current_user` now resolves `X-Shortcut-Token` → bearer JWT → dev `X-User-Id` shim. (`withCredentials` lived only in the deleted `web/` client.)
- **Phase 6f: Redis state keys keep the `telegram:` prefix.** All durable bot state (`telegram:pending:{user_id}`, `telegram:clarification:{user_id}`, `telegram:account_creation:{user_id}`, `telegram:last_action:{user_id}`, etc.) is reused by the native chat without renaming because the prefix is historical, not semantic, and renaming would invalidate every Telegram user's in-flight state. Cleanup target: rename to `bot:` or `chat:` only when there is a separate migration window and a one-time copy step. Not before Phase 8.
- **Phase 6f: receipt images stored base64-inline in `llm_extractions.raw_data`** (`raw_data->image_b64`, 4MB cap pre-base64). At 10 receipts/day per user that's a few MB/year per user — fine for the personal MVP. Cleanup target: move image bytes to Azure Blob with a signed-URL reference in `raw_data` during P8 hardening. Same migration should also redact base64 from any nightly logs or exports.
- **Phase 6f B1: `mobile/` is pinned to Expo SDK 54** (`expo@~54.0.34`, `react@19.1.0`, `react-native@0.81.5`). Pinned because Apple's App Store ships only the latest Expo Go and Expo Go for SDK 54 is the version the operator's iPhone runs. When the App Store ships Expo Go for a newer SDK and the operator's device updates, bump `mobile/` to that SDK (or move to a custom dev build via EAS Build, which makes us SDK-independent — that's P8 prep). Node 20+ is required by SDK 53+; the host machine is on Node 20.20.2 via NodeSource apt.
- **Phase 6f B3: two session-issuance endpoints coexist** (`POST /auth/magic-link/exchange` and `POST /auth/device-code/exchange`). Both terminate in `issue_session_jwt`, return a bearer JWT in the body, and set no cookie (since 6f B16). Magic-link exchange is kept because the native `ledgercr://exchange` deep link (`/setup`, `/start`/`/help`, `/login` B15) consumes magic-link tokens through it; device-code is the primary `/login` path. Cleanup target: collapse to one path only if the deep-link flow is dropped.
- **Phase 6f B15: `users.expo_push_token` column exists (migration 0021) but no worker reads it.** Schema-only prep — nudges + shadow approvals continue to deliver only to Telegram during Phase 6f even when the operator uses the native app for everything else. No Expo push token is written by the app yet, and there is no APNs delivery worker. P8 prerequisites: Apple Developer Program enrollment for APNs certificates + an Expo push token registration call in the app + a delivery worker.
- **Phase 6f: `ExtractionResult` is accreting per-intent flat fields** (`goal_*`, `income_*`, `bill_*`, `debt_*`, and `amount`/`currency`/`category_hint` reused across intents). It's explicit and validator-guarded, but wide. As of the debt slice (D1, 2026-06-01) all four conversational creators have landed, so the "wait until the pattern is proven" threshold is now reached: a future cleanup may consolidate to a per-intent nested payload (e.g. a discriminated `create: {...}` block) in the tool schema + dispatch if the flat shape proves unwieldy in practice. Deferred (not blocking) — the flat shape is still readable and each field is validator-guarded; revisit only if a 5th creator or noticeable drift appears.
- **Phase 6f: no test catches mobile-API-helper ↔ backend request-body drift.** Backend tests use the correct schema field names; mobile `api/*.ts` bodies are untyped at the `axios` call and `tsc` can't see a wrong JSON key. This let the B9 `archiveTransaction`/`restoreTransaction` `{ ids }`-vs-`{ transaction_ids }` bug ship undetected (fixed 2026-05-30). There is no native CI (decision §3.8), so the only current guard is operator on-device testing. Cleanup target: when EAS/CI lands at P8, add a thin contract test (or generate mobile request types from the OpenAPI schema) so body-field drift fails in CI.

- **Gmail OAuth consent screen is in GCP "Testing" mode → refresh tokens expire ~7 days.** Google revokes `gmail.readonly` refresh tokens after a week for unverified/testing apps, so the operator gets a recurring "se desconectó tu Gmail" and must reconnect weekly. The native app + bot now handle this gracefully (reconnect anytime; revoked scans still record a run; the scan-status endpoint surfaces it; transiently-failed messages retry on the next scan). The actual cure is publishing the OAuth consent screen to **Production** (publishing status), which for the sensitive `gmail.readonly` scope likely requires Google verification (privacy policy, app review). **P8 work** — tied to the existing Gmail-verification gate (the GCP project is also capped at 100 testers in Testing mode).

- **Native Gmail connect is poll-based (no `ledgercr://` callback).** The OAuth callback redirects to a static success page, so the app opens the consent URL in a browser and then polls `GET /gmail/scan/status` for `connected`. Cleanup target: redirect the callback to `ledgercr://gmail-connected` so `expo-web-browser`'s auth session auto-closes — deferred until the universal-link/deep-link hostname work (B15-adjacent / P8).

- **Secret store: production is enforced to Key Vault (resolved 2026-06-05, commit `fe8db18`).** A `Settings` validator (`api/config.py::_enforce_prod_secret_store`) refuses to boot when `ENVIRONMENT=production` and `SECRET_STORE_BACKEND != azure_kv` (or `AZURE_KEY_VAULT_URL` unset), so a misconfigured prod deploy can't silently keep Gmail OAuth refresh tokens in `env`/`file` (plaintext / process env / ephemeral disk). The DB never stores the token — only the `gmail-refresh-{user_id}` Key Vault reference. `env`/`file` remain dev-only. Decision note: vault `Decision - Secrets in Key Vault (Prod-Enforced)`.

- **FX rate is a hardcoded ₡500/US$ placeholder — wire the BCCR API.** `api/services/fx.py::convert` (used by envelope spend to count a USD expense against a CRC envelope) uses `FALLBACK_USD_TO_CRC = Decimal("500")`, a round placeholder, NOT a market rate. Cleanup target: a small daily worker that pulls the Banco Central de Costa Rica "Indicadores Económicos" SOAP service (`GetIndicadoresEconomicos`; indicador 317 = compra, 318 = venta), persists the rate into the existing `currency_rates` table (`base_currency`/`quote_currency`/`rate`/`as_of`, migration 0017 — already defined, currently unused), and has `convert` read the latest row (falling back to the constant when the table is empty/stale). Until then all CRC↔USD conversion in the app is at the fixed 500 rate. The `currency_rates` table has existed since Phase 6e but nothing reads or writes it yet.

- **Balance anchor — follow-ups (2026-06-19).** (a) The reconciliation **ajuste** is excluded from income/expense reports via the reserved `AJUSTE_CATEGORY` string marker (`api/services/anchors.py`), filtered NULL-safely (`is_distinct_from`) in the four `summary.py` aggregators. Cleanup target: a dedicated `transactions.is_adjustment` boolean (a migration) so the exclusion isn't string-based (a user category literally named "ajuste de reconciliación" would currently be excluded too). (b) Chat `set_balance` re-anchor is **not in the /undo chain** (no `save_last_action`) — a wrong re-anchor is corrected by re-anchoring again (append-only model). (c) **Credit accounts can't be re-anchored** from chat or native (their balance is movement/payment-driven); the nudge + "Corregí mi saldo" are fund-account only. (d) The cross-account "Disponible" roll-up is **CRC-led with USD shown apart** (`DashboardSummary.other_currency_balances`) — it never adds ₡+$ on the ₡500 placeholder; single-number conversion waits for the live BCCR rate (above). (e) The card-payment-received → transfer-leg recognition is deferred to the SINPE/counterparty workstream (the Gmail email extractor lacks structured parties); the shadow gate prevents un-reviewed pollution meanwhile.

---

## Phase 5a — Auth model (current operational reference)

Phase 5a retrospective lives in `~/Finance_project/30_Projects/Finance-Agent/11_Phases/Phase-5a-...`. The pieces that stay operational here:

### `users` schema

`id`, `email` (UNIQUE NOT NULL), `full_name`, `phone_number` (E.164), `country` (default `CR`), `timezone`, `currency`, `locale`, `shortcut_token` (UNIQUE NOT NULL), `telegram_user_id` (UNIQUE, BIGINT), `whatsapp_phone` (UNIQUE), `status` (`active|suspended`), `created_at`, `updated_at`. Pre-5a `name` column was renamed to `full_name`.

### Per-user UNIQUE constraints (migration 0006)

- `transactions(user_id, source_ref) WHERE source_ref IS NOT NULL` — Gmail Message-ID dedup, scoped per user.
- `accounts(user_id, name) WHERE is_active` — a user cannot have two active accounts with the same name.

### Endpoints (all under `/api/v1`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/users/register` | none | Body `{email, full_name, phone_number?, country?, timezone?, currency?, locale?}`. Returns user + fresh `shortcut_token` (one-shot). 409 on duplicate email. Seeds a `scope=global_default` notification rule. |
| GET | `/users/me` | shim or token | Returns the resolved caller. |
| POST | `/users/me/rotate-shortcut-token` | shim or token | Issues a new token, invalidates the old. |

### Onboarding the first real user post-migration

1. Set `LEGACY_USER_EMAIL`, `LEGACY_USER_NAME`, `LEGACY_SHORTCUT_TOKEN` (and optionally `DEFAULT_USER_ID`) in `.env`.
2. `alembic upgrade head` — migration 0006 updates or inserts the legacy user, then backfills `user_id` everywhere.
3. To onboard a second person: `POST /api/v1/users/register` → save the returned `shortcut_token` → paste it into the iPhone Shortcut as `X-Shortcut-Token`.

---

## Product Roadmap (Post-Phase 7)

- **Auth**: Email + magic link (no passwords) — Phase 6d covers self-onboarding for the personal MVP; multi-tenant version is Phase 8.
- **Onboarding**: Telegram bot collects currency, categories, goals, events → Gmail OAuth → parser calibration.
- **Tenant isolation**: `tenant_id` UUID on every table + middleware enforcement.
- **Pricing**: Free 14-day trial → Personal $9/mo → Couple $15/mo.
- **Compliance**: ToS, privacy policy, encrypted credential storage, audit logging, GDPR export/delete, Sentry, Postgres backups.
- **Channel**: Telegram at launch. WhatsApp only via Meta's official Cloud API.
- **LLM**: API-based until ~10k+ DAU.
