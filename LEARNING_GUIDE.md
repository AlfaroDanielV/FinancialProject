# Learning Guide — Personal Finance Agent

> A self-study path from "I can read Python" to "I can build and maintain this project alone."
> Pair this with `CLAUDE.md` (canonical architectural reference, kept current).
> Last refresh: 2026-05-15 (Phase 6e B1-B13 implemented; B7 + B10 sign-offs received; B13 install/Lighthouse gate pending; B14 privacy + E2E next).

---

## 0. How to use this guide

This is a 3-part document:

1. **Part A — Codebase tour.** What every directory does, in dependency order. Read top-to-bottom on day one.
2. **Part B — Navigation playbook.** Repeatable recipes for "I want to add a feature / debug X / understand Y." Use this when working.
3. **Part C — Learning path.** Books + video resources, ordered beginner → pro. Each topic maps to where in *this* codebase you'll see it.

**Don't try to learn everything before touching the code.** The codebase is the textbook. Run it locally, break it on purpose, and use Part C to fill the gaps you actually hit.

---

# PART A — Codebase Tour

## A.1 The big picture in 60 seconds

You're looking at a **FastAPI + Postgres + Redis** backend with a **Telegram bot** as the primary interface and a **Vite + React SPA** as the read/edit "Centro Financiero" surface. The product captures financial transactions from four sources (manual API, iPhone Shortcut, Telegram bot, Gmail parser for BAC/Davivienda/Promerica), stores them in Postgres, and exposes:

1. A chat interface where Claude (Haiku for extraction, Sonnet for queries) extracts intent from Spanish messages and answers via tool-use.
2. A web SPA at `web/` for inspecting balances, editing transactions, paying recurring bills via a calendar, and reviewing user memory insights.

Five top-level packages matter:

| Package | Role | When you'll touch it |
|---|---|---|
| `api/` | FastAPI HTTP layer + ORM models + business services | Adding endpoints, schema changes, business logic |
| `app/queries/` | Read-only conversational query layer (LLM + tools) | Adding a new "answerable question" the bot can handle |
| `bot/` | Telegram pipeline (aiogram) — extractor → router → dispatcher → delivery | Bot behavior, Spanish messages, callback flows |
| `web/` | Vite + React 18 + TypeScript + Tailwind + TanStack Query SPA | Centro Financiero screens (Phase 6d/6e) |
| `workers/` | Background job entrypoints (Gmail daily, insights nightly + lifecycle) | Container Apps Jobs |

Plus three support directories:

| Directory | Role |
|---|---|
| `migrations/versions/` | Hand-written Alembic migrations, numbered `0001_…` through `0020_…` |
| `tests/` | Pytest async suite (~85 test files; per-block focused tests under `tests/test_phase_*.py`) |
| `docs/` | Per-phase decisions (`docs/phase-*-decisions.md`) and curl smokes (`docs/curl/`); `scripts/` holds bash phase-gate runners |

Old empty placeholders (`agent/`, `jobs/`, `parsers/`) have been replaced by real packages — `workers/` is the live one for scheduled work, and the bot owns what `agent/` was meant to do.

---

## A.2 Read-order for new contributors

Follow this exact sequence. Each file builds on the previous.

### Step 1 — Boot path (understand startup)

1. `pyproject.toml` — pinned dependency versions. Note: SQLAlchemy 2.x async, Pydantic v2, anthropic SDK, aiogram v3.
2. `docker-compose.yml` — three services: `db` (Postgres 16), `redis` (Redis 7), `api` (Uvicorn).
3. `.env.example` — every config knob the app reads.
4. `api/config.py` — Pydantic `BaseSettings` that consumes `.env`.
5. `api/database.py` — async SQLAlchemy engine + `get_db` FastAPI dependency.
6. `api/redis_client.py` — singleton async Redis connection.
7. `api/main.py` — `lifespan` context manager, router registration, `/health` and `/health/ready`.

Stop here and run it: `docker compose up -d`, then `curl localhost:8000/health`.

### Step 2 — Domain model (understand the data)

8. `api/models/base.py` — `DeclarativeBase` for all ORM classes.
9. `api/models/__init__.py` — imports every model; this is what Alembic sees.
10. Read models in this order to understand relationships:
    - `user.py` → `account.py` → `transaction.py` (the spine; `transaction.py` carries `transfer_id` + `category_id` FKs from 6e and `archived` from 6e B5)
    - `recurring_bill.py` → `bill_occurrence.py` → `notification_rule.py` → `notification_event.py` (Phase 4)
    - `pending_confirmation.py` → `user_nudge.py` (Phase 5d)
    - `llm_extraction.py` → `llm_query_dispatch.py` (Phase 5b/6a observability)
    - `gmail_credential.py`, `gmail_sender_whitelist.py`, `bank_notification_sample.py`, `gmail_message_seen.py`, `gmail_ingestion_run.py`, `gmail_discovery_run.py` (Phase 6b)
    - `user_insight.py` (Phase 6c — typed user memory)
    - `magic_link_token.py`, `recurring_income.py`, `lazy_detection_event.py` (Phase 6d onboarding + magic-link auth)
    - `goal.py`, `goal_contribution.py`, `transfer.py`, `user_category.py`, `currency_rate.py` (Phase 6e Centro Financiero)
11. `migrations/versions/0001_initial_schema.py` through `0020_phase6e_b8_recurring_incomes_archived.py` — read in numeric order. This is the schema's history. Phase landmarks: `0006` (multi-tenant `users`), `0011` (Phase 6b Gmail + status CHECK), `0013/0014` (Phase 6c insights), `0016` (Phase 6d magic links + recurring incomes), `0017` (Phase 6e foundation), `0018` (Phase 6e B5 `transactions.archived`), `0019` (Phase 6e B7 `debts.archived`), `0020` (Phase 6e B8 `recurring_incomes.archived`).

### Step 3 — HTTP surface (understand the API)

12. `api/dependencies.py` — `current_user` resolves in order: `X-Shortcut-Token` → Phase 6d `fa_session` cookie → `X-User-Id` dev shim. The strict `current_user_via_token` is what `POST /transactions/shortcut` and every `POST /jobs/*` use.
13. `api/schemas/transaction.py` — Pydantic v2 request/response models. Notice the dual `ShortcutTransactionCreate` schema and the Phase 6e additions: `TransactionUpdate`, `TransactionListResponse.next_cursor`, and the bulk request bodies.
14. `api/routers/transactions.py` — the most-developed router. Pattern to copy. Phase 6e B4/B5 grew it with filters, cursor pagination, CSV export, and bulk archive/categorize endpoints.
15. `api/routers/users.py` — registration + token rotation.
16. `api/routers/jobs.py` — the three Phase 4 batch jobs + Phase 5d evaluator/delivery jobs + Phase 6c insights compute trigger.
17. `api/routers/auth.py` — Phase 6d magic-link exchange that issues the `fa_session` cookie. Uses opaque `<selector>.<verifier>` tokens, bcrypt-hashed verifiers, single-use atomic consumption.
18. `api/routers/onboarding.py`, `api/routers/recurring_incomes.py` — Phase 6d hybrid SPA flow.
19. `api/routers/dashboard.py` — Phase 6e B2/B3 dashboard summary, daily cash-flow, category breakdown.
20. `api/routers/goals.py`, `api/routers/transfers.py`, `api/routers/categories.py` — Phase 6e B2 entities.
21. `api/routers/recurring_bills.py` — Phase 6e B6 added `POST /{id}/mark-paid` with Redis-backed idempotency on top of the Phase 4 CRUD.
22. Then skim the rest of `api/routers/`. They follow the same shape.

### Step 4 — Services (understand the business logic)

The `api/services/` tree has grown into clear subsystems. Read in this order:

18. `api/services/recurrence.py` — RRULE + frequency expansion → `bill_occurrences`. The hardest pure-Python file. Phase 6e B6 reused `link_transaction_to_occurrence` for the new bill-level mark-paid.
19. `api/services/transactions.py` — narrow telegram dispatcher helpers: `create_transaction`, `delete_telegram_transaction` (the /undo guard), `recent_for_user`, `sum_in_window`, `window_bounds` for natural-language windows.
20. `api/services/accounts.py` — `resolve_account` (fuzzy match for the bot) plus Phase 6e B4's `compute_account_balances` (one-pass per-account current + month-start balance; excludes archived rows).
21. `api/services/transfers.py` — Phase 6e B2 atomic transfer creation that emits the two linked `transactions` rows.
22. `api/services/categories.py` — Phase 6e B2 user-category CRUD + archival rules (`(user_id, lower(name)) WHERE archived=false` uniqueness).
23. `api/services/dashboard/` — `summary.py` (live current-month aggregator + daily/cash-flow series) and `materialized.py` (helper that refreshes `mv_monthly_summary_by_user` / `mv_yearly_summary_by_user`).
24. `api/services/auth/` — Phase 6d magic-link issuance + exchange + `fa_session` JWT minting.
25. `api/services/finance/` — Phase 6d backend-owned finance derivations (e.g. aguinaldo / salario_escolar dates, French amortization input validation).
26. `api/services/insights/` — Phase 6c user-memory pipeline: `computed.py` (deterministic SQL-driven insights), `extractor.py` (Haiku → typed `InsightContent`), `lifecycle.py` (TTL/dedup/redaction), `persister.py`.
27. `api/services/gmail/` — Phase 6b OAuth + scanner + reconciler + parsers (BAC, Davivienda, Promerica) + sender discovery.
28. `api/services/nudges/` — read in this order:
    - `policy.py` (constants — rate limit, silence threshold, quiet hours)
    - `evaluators/` (3 pure functions that emit candidate nudges)
    - `orchestrator.py` (dedup + silence filter + insert)
    - `delivery.py` (the 4 anti-saturation rules in code)
    - `actions.py` (state machine — dismiss/act + auto-silence)
    - `phrasing.py` (LLM call that writes the Spanish copy)
29. `api/services/llm_extractor/` — the Anthropic tool-use call that powers the bot:
    - `schema.py` (`ExtractionResult` Pydantic contract)
    - `prompt.py` (system prompt + cache_control blocks)
    - `client.py` (Anthropic SDK wrapper)
    - `runner.py` (glue + persistence to `llm_extractions`)
30. `api/services/dispatch/`, `api/services/extraction/` — Phase 6c-era reorganization that split the dispatcher seams from the raw extractor; mostly thin glue.

### Step 5 — Bot pipeline (understand the chat layer)

The bot package keeps growing as new flows land — count it as ~25 modules now (`ls bot/`).

31. `bot/redis_keys.py` — every Redis key the bot uses, with TTL contracts.
32. `bot/app.py` — aiogram `Bot` + `Dispatcher` + start/stop hooks for `lifespan`.
33. `bot/handlers.py` — aiogram routes (text, callbacks, commands).
34. `bot/pipeline.py` — **the brain.** ~655 lines. Resolve user → rate limit → command short-circuit → LLM → route → dispatch → reply. Read top-to-bottom twice.
35. `bot/pending.py` + `bot/pending_db.py` — the two-tier proposal store (Redis 5 min + Postgres 48 h).
36. `bot/delivery_send.py` — sanitize HTML → split for Telegram's 4096-char limit → send.
37. `bot/clarification.py` + `bot/commit.py` — Phase 5b/5d clarification + write-commit seams shared across the dispatcher.
38. `bot/gmail_handlers.py`, `bot/gmail_listener.py`, `bot/gmail_onboarding.py`, `bot/gmail_pubsub.py` — Phase 6b Gmail flow (OAuth init → discovery → daily worker hand-off).
39. `bot/memory_handlers.py` — Phase 6c `/memoria`, `/olvidar`, `/editar_memoria`, `/recalcular_memoria`.
40. `bot/onboarding_handlers.py`, `bot/onboarding_welcome.py`, `bot/account_creation.py` — Phase 6d hybrid SPA + lazy-account-creation Redis flow.

### Step 5b — SPA (understand the web surface)

The Centro Financiero SPA lives entirely under `web/`. It's React 18 + TypeScript + Tailwind + TanStack Query, lazy-loaded route chunks, no Redux. Read in this order:

41. `web/package.json` — locked deps. Notable: `@tanstack/react-query`, `react-hook-form`, `zod`, `recharts` (lazy), `react-day-picker` (added in 6e B6).
42. `web/src/main.tsx` — `QueryClient` defaults (30s stale time, retry=1) and the `BrowserRouter` + `AuthProvider` mount.
43. `web/src/App.tsx` — route table. Eager: `Dashboard`, `Expired`, the four 6d creation forms (`AccountsNew`, `IncomesNew`, `DebtsNew`, `BillsNew`). Lazy via `Suspense`: `AccountsIndex`, `AccountDetail`, `TransactionsIndex`, `BillsIndex`.
44. `web/src/lib/auth.tsx` — cookie-session resolver (`/users/me` ping). 401 → `/expired`.
45. `web/src/api/client.ts` — axios instance with `withCredentials: true` and a 401 interceptor.
46. `web/src/api/dashboard.ts`, `web/src/api/accounts.ts`, `web/src/api/transactions.ts`, `web/src/api/bills.ts` — the per-resource fetchers + Zod parsers. TanStack queries always go through these wrappers, never raw `axios`.
47. `web/src/schemas/entities.ts` + `web/src/schemas/dashboard.ts` — single source of truth for the wire types (Zod).
48. `web/src/routes/Dashboard.tsx` — Phase 6e B3 home. Read alongside `web/src/components/dashboard/DashboardCharts.tsx` (lazy Recharts chunk).
49. `web/src/routes/AccountsIndex.tsx` + `web/src/routes/AccountDetail.tsx` — Phase 6e B4 read/edit surface. Detail reuses `web/src/components/transactions/TransactionEditModal.tsx` (extracted in B5).
50. `web/src/routes/TransactionsIndex.tsx` — Phase 6e B5 with cursor pagination, multi filters, bulk actions, CSV export trigger.
51. `web/src/routes/BillsIndex.tsx` + `web/src/components/bills/BillActionsModal.tsx` — Phase 6e B6 calendar + actions modal with Redis-backed idempotency on the mark-paid call.
52. `web/src/routes/DebtsIndex.tsx` + `web/src/routes/DebtDetail.tsx` — Phase 6e B7 debts index (top metrics + DTI) and detail with three tabs (Amortización, Calculadora cancelación browser-side, Escenarios Ley 7472 server-side). `web/src/lib/amortization.ts` extends with `earlyPayoffLumpSum` / `earlyPayoffExtraMonthly` / `calculatePrepaymentPenalty` mirroring `api/services/amortization.py`.
53. `web/src/routes/IncomesIndex.tsx` — Phase 6e B8 recurring incomes index. CR-cycle nudge banner triggers the new atomic `POST /recurring-incomes/{salary_id}/derive-cycles` to create both `aguinaldo` and `salario_escolar` from a single salary. Inline-edit per row (with derived rows' amount input disabled), Pausar/Reanudar/Archivar/Restaurar actions. The existing `IncomesNew.tsx` at `/incomes/new` still serves the creation form; its slimming is logged as cleanup.
54. `web/src/routes/GoalsIndex.tsx` + `web/src/routes/GoalDetail.tsx` + `web/src/routes/GoalsNew.tsx` — Phase 6e B9 goals module. Index has status + currency filters. Detail shows progress, linked-account saldo as a read-only sanity snapshot (NOT auto-overwrite — contributions still drive `current_amount`), full contribution history, server-computed forecast at the last 3 complete months' average pace, and status actions. New backend endpoints: `GET /goals/{id}/contributions` and `GET /goals/{id}/forecast`.
55. `web/src/routes/MemoryIndex.tsx` — Phase 6e B10 memoria SPA. Mirrors the bot's `/memoria` groups (`metas` / `conozco` / `patrones` / `banderas`) via the new `GET /api/v1/users/me/insights`. Per-row edit modal dispatches by `insight_type` to enum radios or text inputs; saves through `PATCH /api/v1/users/me/insights/{id}` which sets `source='user_override'`, `user_locked=true`, `confidence=1.00` and emits a `locked` audit. Per-row + per-group + two-step "Borrar todo" delete flows. "Descargar mi memoria" reuses the Phase 6c export endpoint. Backend: `api/routers/privacy_insights.py` carries the new endpoints alongside the existing 6c delete-all + export.
56. `web/src/routes/CategoriesIndex.tsx` — Phase 6e B11 categories management. No backend changes — Phase 6e B2 (`api/routers/categories.py`) already shipped the CRUD with auto-seeding, transaction counts, and the "default cannot be archived" 400 guard. The SPA renders an inline create form on top of the list and per-row inline edit (name / kind / color via native `<input type="color">` / icon text). Defaults render Archivar as disabled.
57. `bot/deep_link.py` + `web/src/lib/auth.tsx` (`?path=` handling) — Phase 6e B12 bot ↔ SPA deep linking. The bot helper wraps `api.services.auth.magic_link.generate_link` (which already supported `purpose='edit_session'` + `target_path` from Phase 6d B3) with swallow-on-fail. `BotReply` and `NudgeMessage` both grew URL-button support, so any bot or nudge reply can attach a single-use SPA deep link. Two callsites wired: post-commit ("Ver en Centro Financiero") and `/memoria` end ("Editar en SPA"). The SPA's `AuthProvider` validates `?path=` client-side via `isSafeRelativePath` and `navigate(safePath, { replace: true })` after exchange.
58. `web/vite.config.ts` (VitePWA) + `web/public/icons/` + `web/src/components/shell/` — Phase 6e B13 PWA + mobile polish. The build emits `dist/sw.js`, `dist/workbox-*.js`, and `dist/manifest.webmanifest` with the three locked caching strategies (shell precache, API `NetworkFirst` 4s/24h, images `CacheFirst` 7d). Icons are placeholder PNGs synthesized by `scripts/generate_pwa_icons.py` (stdlib-only — no Pillow). Shell components: `OfflineBanner` (`useOnlineState` hook, banner-only — mutations are NOT blocked), `InstallBanner` (`useInstallPrompt` hook, gated to ≥ 3rd visit via localStorage), `BottomNav` (mobile-only, hidden at `sm:` and above, safe-area-inset padding for the iPhone home indicator). On Node 18 the build needs `NODE_OPTIONS=--experimental-global-webcrypto` for `workbox-build`; the npm script wraps with `cross-env` so it's transparent.

### Step 6 — Query layer (understand how questions are answered)

52. `app/queries/prompts/system.py` — system prompt for the query LLM.
53. `app/queries/tools/base.py` — the `Tool` abstraction.
54. `app/queries/tools/transactions.py` — the most-used tool. Read it carefully.
55. The full tool roster (registered in `app/queries/tools/__init__.py`): `transactions`, `accounts`, `compare_periods`, `debts`, `pending`, `recurring_bills`, `user_context` (Phase 6c), plus `_common` and `_test_only` infra. Phase 6c added `user_context` as the *only* surface the LLM uses to read user memory — insights never inline in the system prompt.
56. `app/queries/llm_client.py` — Anthropic tool-use loop (iteration cap 4, cache_control on the last tool only, token accounting).
57. `app/queries/dispatcher.py` — orchestrates the loop + history + audit row.
58. `app/queries/history.py` — Redis-backed conversation history (24h TTL).
59. `app/queries/delivery.py` — error→Spanish-message mapping.

### Step 7 — Workers + scheduled jobs

60. `workers/gmail_daily.py` — Phase 6b scanner; runs as an Azure Container Apps Job (cron 9am UTC).
61. `workers/insights_nightly.py` — Phase 6c computed-insight refresh + Phase 6e B2 dashboard materialized-view refresh (same job).
62. `workers/insights_lifecycle.py` — Phase 6c TTL/dedup/`raw_quote` redaction sweep.

### Step 8 — Tests (understand what "correct" means)

63. `tests/conftest.py` — the per-test NullPool engine pattern. **Critical.** Async tests share an event loop — connection pools that span loops cause flaky failures. Note the `_insights_dispatcher_flag_on` autouse fixture (defaults the 6c production flag to True for tests) and the `_reset_redis_singleton` fixture (avoids cached event-loop bindings).
64. Pick one passing test from each phase and trace the assertions back to the code:
    - `tests/test_telegram_dispatcher.py` (write dispatch)
    - `tests/test_nudges_evaluators.py` (Phase 5d)
    - `tests/test_phase_6a_block5b_e2e.py` (full query loop)
    - `tests/test_phase_6c_*.py` (insights pipeline; dozens of files)
    - `tests/test_phase_6d_b11_e2e.py` (hybrid onboarding E2E)
    - `tests/test_phase_6e_b2_backend.py` → `tests/test_phase_6e_b6_bills.py` (Centro Financiero per block)
65. `scripts/test_phase_6d.sh`, `scripts/phase5b_smoke.sh`, `docs/curl/phase-5d.sh`, `docs/curl/phase-6a.sh` — end-to-end manual smokes. Run these locally to see the system breathe.

---

## A.3 Mental model cheat sheet

Pin these in your head — they show up everywhere:

- **Money is `NUMERIC(12,2)` or `NUMERIC(14,2)`.** Negative = expense, positive = income. Never use `float`.
- **All timestamps are `TIMESTAMPTZ` in UTC.** The user's local timezone (`users.timezone`, default `America/Costa_Rica`) is applied at display/quiet-hours time.
- **All PKs are UUIDv4** via `gen_random_uuid()`.
- **Multi-tenancy = `user_id` FK on every domain table.** `ON DELETE RESTRICT`. The `current_user` dependency resolves the tenant.
- **The LLM never decides whether to act.** Extractor produces structured JSON; deterministic Python code routes and commits. The query dispatcher is the *only* LLM-on-the-hot-path component.
- **Redis is the source of truth for durable bot state.** aiogram FSM is for transient in-handler bookkeeping only.
- **Migrations are hand-written.** No `--autogenerate`. Every schema change → new numbered file.
- **Auth resolves in this order: `X-Shortcut-Token` → `fa_session` cookie (Phase 6d) → `X-User-Id` dev shim** (`api/dependencies.py::current_user`). The strict variant ignores the dev shim.
- **Two dimensions of "this row is gone": `status` and `archived`.** `status` (confirmed/shadow/pending_review) is the Phase 6b ingestion lifecycle; `archived` is the Phase 6e user-driven soft-delete. Balance + dashboard exclude both `status != 'confirmed'` and `archived = true`. The materialized views currently miss the `archived` predicate (logged in CLAUDE.md tech debt).
- **Bot is the primary input; SPA is the read/edit surface.** Don't put a "log a transaction" form on the SPA homepage. Manual entry is a fallback with a bot advisory.
- **Insights never inline in the system prompt.** Sonnet reads user memory through the `get_user_context` tool and nothing else (Phase 6c).
- **Cache breakpoint limit is 4.** Apply `cache_control` only on the last tool, not all of them. Exceeding silently breaks caching.
- **The Centro Financiero SPA is a separate deploy** to Azure Static Web Apps (workflow under `.github/workflows/`). Its dev URL is `http://localhost:5173`; Telegram inline-button URLs reject `localhost` so local SPA testing needs an HTTPS tunnel.

---

# PART B — Navigation Playbook

Recipes you'll repeat dozens of times. Bookmark this section.

## B.1 "I want to add a new HTTP endpoint"

1. **Schema first.** Add Pydantic v2 request/response classes in `api/schemas/<resource>.py`. Use `model_config = ConfigDict(from_attributes=True)` for ORM read models.
2. **Model.** If a new table: create `api/models/<name>.py`, register it in `api/models/__init__.py`.
3. **Migration.** Copy the latest `migrations/versions/00XX_…py` as a template. Bump the prefix. Hand-write `upgrade()` and `downgrade()`.
4. **Router.** Create or edit `api/routers/<resource>.py`. Use `APIRouter(prefix="/api/v1/<resource>", tags=["<resource>"])`. Inject `db: AsyncSession = Depends(get_db)` and `user = Depends(current_user)` (or `current_user_via_token` if it must reject the dev shim).
5. **Mount.** Add `app.include_router(<resource>.router)` in `api/main.py`.
6. **Test.** New file in `tests/test_<resource>.py`. Follow the NullPool pattern from `conftest.py`.
7. **Smoke.** Add a curl example to `docs/curl/` if it's a phase-gate feature.

## B.2 "I want to add a new query the bot can answer"

1. **Tool definition.** New file in `app/queries/tools/<name>.py`. Subclass `Tool`. Define `input_schema` (JSON Schema dict), `name`, `description`, and `async def run(ctx, args)`.
2. **Register.** Add to `app/queries/tools/__init__.py`.
3. **Update system prompt.** `app/queries/prompts/system.py` — Claude needs to know the tool exists and when to call it.
4. **Test.** Write a unit test in `tests/test_tool_<name>.py` (no LLM). Then add an e2e to one of the `tests/test_phase_6a_block*_e2e.py` patterns (uses real Anthropic — gate behind an env var).
5. **No new dispatcher logic needed.** The loop in `llm_client.py` picks up registered tools automatically.

## B.3 "I want to add a Telegram command (e.g. `/foo`)"

1. **Handler.** Add an aiogram handler in `bot/handlers.py` decorated with `@router.message(Command("foo"))`.
2. **If it's a new pipeline branch:** edit `bot/pipeline.py`. Add the command short-circuit *before* the LLM extractor block — commands must never burn tokens.
3. **Spanish copy.** Add the user-facing strings to `bot/messages_es.py`. Don't inline them.
4. **Test.** Drive it via `POST /api/v1/telegram/_simulate` in dev.

## B.4 "I want to change a Pydantic schema"

- **Backwards compat.** Pydantic v2 `model_config = ConfigDict(extra="forbid")` is used in some places (e.g. `ExtractionResult`). Adding a field there breaks deserialization of stored rows. Use `extra="allow"` or migrate the data.
- **Schema is also part of the LLM contract.** If the field is in `ExtractionResult` or a query-tool input schema, the system prompt and fixture tests must be updated too. Re-record extractor fixtures: see notes in `tests/test_llm_extractor.py`.

## B.5 "I want to add a new migration"

```bash
# 1. Copy the latest migration as a template (current head: 0020)
cp migrations/versions/0020_phase6e_b8_recurring_incomes_archived.py \
   migrations/versions/0021_<your_change>.py

# 2. Edit revision/down_revision. Write upgrade()/downgrade() by hand.

# 3. Apply it
alembic upgrade head

# 4. Verify it can roll back cleanly
alembic downgrade -1 && alembic upgrade head
```

**Never run `alembic revision --autogenerate`.** Project policy.

The latest head shifts every block — don't hardcode a number into your script. Read it with `alembic current` or `ls migrations/versions/ | tail -1`.

## B.5b "I want to add a new SPA route"

1. **Schema first.** Add or extend the Zod model in `web/src/schemas/entities.ts` (or `web/src/schemas/dashboard.ts` for dashboard-scoped contracts). The Zod object IS the wire contract; never `as any`-coerce.
2. **API helper.** Add a fetch + parse helper to the matching `web/src/api/<resource>.ts` (or create one). Always go through the shared `web/src/api/client.ts` axios instance — it carries `withCredentials: true` and the 401 interceptor.
3. **Route component.** New file in `web/src/routes/<Name>.tsx`. Default export. Use TanStack Query (`useQuery` / `useMutation`); never `useEffect + fetch`.
4. **Mount.** Add `const X = lazy(() => import("./routes/X"));` at the top of `web/src/App.tsx`, then a `<Route>` inside the `<Suspense>` block. Lazy-loading is the only way to keep the initial bundle under the 200KB gzip budget.
5. **Verify.** `npm --prefix web run lint` and `npm --prefix web run build`. Watch the printed `index` chunk size — if it jumped, you forgot to lazy-load something.
6. **Mobile-first.** Design first at 375px width, verify nothing breaks at 320px (decision 3.10 in `docs/phase-6e-decisions.md`). Touch targets ≥ 44×44 px.

## B.5c "I want to share a SPA component across routes"

If two routes need the same modal/widget, extract it to `web/src/components/<area>/<Component>.tsx` BEFORE the second route is finished — Phase 6e B5 did this with `TransactionEditModal` (used by `AccountDetail` and `TransactionsIndex`). Vite's chunk splitter rewards shared components with their own chunk, so the initial bundle gets smaller, not bigger, when you extract.

## B.6 "Tests are flaky — what do I check first?"

In order of likelihood:

1. **Cross-event-loop asyncpg connection.** The pattern in `tests/conftest.py` creates a `NullPool` engine *per test* so connections never escape their loop. New test files must follow it.
2. **Redis state leaking between tests.** Use a fixture that flushes the test DB index, or scope keys to a unique test-run UUID.
3. **Tool-loop tests calling the real Anthropic API.** Mark them `@pytest.mark.skipif(not ANTHROPIC_KEY)` or use the `FixtureLLMClient` pattern from `tests/test_llm_extractor.py`.
4. **`datetime.utcnow()` deprecation warnings.** Tracked tech debt — not a real failure but pytest may surface them.

## B.7 "I want to run the system end-to-end locally"

```bash
# 1. Boot infra + API
docker compose up -d

# 2. Apply migrations (head should print 0020 or higher)
alembic upgrade head
alembic current

# 3. Register a user (returns shortcut_token ONCE — save it)
curl -X POST localhost:8000/api/v1/users/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@test.local","full_name":"You"}'

# 4. Run the phase smokes
bash scripts/phase5a_smoke.sh
bash scripts/phase5b_smoke.sh
bash docs/curl/phase-5d.sh
bash docs/curl/phase-6a.sh
bash scripts/test_phase_6d.sh   # focused 6d verification (pytest + npm)
```

If `phase5b_smoke.sh` passes against `_simulate`, the entire bot pipeline (minus a real Telegram connection) is healthy.

To poke the SPA locally:

```bash
# 5. Boot the SPA (separate terminal)
npm --prefix web install        # first time only
npm --prefix web run dev        # http://localhost:5173, Vite proxies /api to :8000
```

Telegram inline-button URLs reject `localhost`, so testing the magic-link → SPA flow against a real bot needs an HTTPS tunnel (cloudflared / ngrok) for `SPA_BASE_URL`.

## B.8 "I want to change the LLM prompt"

- **Extractor prompt:** `api/services/llm_extractor/prompt.py`. Has `cache_control={"type":"ephemeral"}` on the system + tool-schema blocks — keep those when editing.
- **Query prompt:** `app/queries/prompts/system.py`.
- **After any prompt change:** re-record the relevant fixtures (`tests/fixtures/`). Drift in test assertions is a *signal*, not a nuisance — investigate before relaxing them.
- **Cost watch.** Phase 6a uses Sonnet for queries, Haiku for extraction. Don't swap to Opus in production code without checking `LLM_DAILY_TOKEN_BUDGET_PER_USER`.

## B.9 "How do I find where X is implemented?"

| Looking for | Search this |
|---|---|
| An endpoint by URL | `grep -r '@router' api/routers/ \| grep -i <path-fragment>` |
| A Spanish error message | `grep -r '<text>' bot/messages_es.py bot/onboarding_welcome.py` |
| A DB column | `grep -r '<column_name>' api/models/ migrations/versions/` |
| A Redis key | `grep -r '<prefix>' bot/redis_keys.py app/queries/history.py api/services/auth/ api/routers/recurring_bills.py` |
| A tool the LLM calls | `app/queries/tools/__init__.py` — single registry |
| A migration that touched a table | `grep -l '<table>' migrations/versions/` |
| A SPA route component | `web/src/App.tsx` — single route table |
| A SPA fetch call | `grep -rn '<endpoint-fragment>' web/src/api/` |
| A locked decision | `docs/phase-*-decisions.md` — phase-by-phase canonical contract |
| Phase status / what's open | `CLAUDE.md` Phase tables + `~/Finance_project/30_Projects/Finance-Agent/00_Project-Brain.md` |

---

# PART C — Learning Path

> Resources are listed by **canonical title and author/creator** so you can find the current edition. Avoid pirated or out-of-date copies — these technologies move fast (especially the LLM tooling).

Each section has:
- **Why this matters here** — the line in *this* codebase the topic shows up
- **Resources** — books and video creators worth your time, ordered beginner → pro

---

## C.1 Foundations: Modern Python (3.10+)

**Why this matters here:** Type hints everywhere, `async/await` on every DB and HTTP call, Pydantic models, `match` statements, dataclasses. If you can't read `async def get_user(db: AsyncSession) -> User | None:` instantly, start here.

### Books

1. **"Python Crash Course" — Eric Matthes** *(no Python at all? start here)*
2. **"Fluent Python" (2nd ed) — Luciano Ramalho** ⭐ *the* book for going from "I write Python" to "I understand Python." Chapters on iterators, decorators, async, and typing are directly applicable.
3. **"Robust Python" — Patrick Viafore** — type hints, protocols, structural patterns. Maps onto every `Mapped[...]` and `Annotated[...]` in this repo.
4. **"Python Concurrency with asyncio" — Matthew Fowler** — once you've seen `async`, this is where you understand it.

### Video creators (search by name on YouTube)

- **mCoding (James Murphy)** — short, dense, correct. "Async fundamentals" and "type hints" playlists.
- **ArjanCodes** — design + clean code in modern Python. Beginner-friendly.
- **Real Python** (free articles, paid videos at realpython.com) — solid reference quality.

---

## C.2 Web APIs: FastAPI

**Why this matters here:** The entire HTTP layer. Every router under `api/routers/` is a FastAPI `APIRouter`. Dependencies (`Depends(get_db)`, `Depends(current_user)`) are the auth + persistence injection model.

### Official docs (treat as a textbook)

- **fastapi.tiangolo.com** — the official tutorial is exceptional. Read "Tutorial - User Guide" cover to cover, then "Advanced User Guide."

### Books

1. **"FastAPI" — Bill Lubanovic** (O'Reilly, 2024) — the only mature print book on the topic.
2. **"Building Python Web APIs with FastAPI" — Abdulazeez Abdulazeez Adeshina** — project-driven, accessible.

### Videos

- **ArjanCodes** — multiple FastAPI architecture videos. Good for "how do I structure a real app."
- **Tiangolo's own talks** (search "Sebastián Ramírez FastAPI") — design rationale from the author.
- **TestDriven.io** — paid, but their FastAPI + async + Postgres course is the closest thing to "build this exact stack" in tutorial form.

---

## C.3 Databases: PostgreSQL + SQL

**Why this matters here:** Postgres-only project. UUIDs, JSONB, partial indexes, CHECK constraints, FKs with `ON DELETE` semantics, composite indexes. See `migrations/versions/0006_phase5a_users_multitenant.py` for a tour.

### Books

1. **"Learning SQL" (3rd ed) — Alan Beaulieu** *(ground floor)*
2. **"PostgreSQL: Up and Running" — Regina Obe & Leo Hsu** — Postgres-specific features. JSONB chapter is gold.
3. **"The Art of PostgreSQL" — Dimitri Fontaine** ⭐ Postgres as a *design tool*, not just a store. Window functions, CTEs, `LATERAL`, advanced JSONB. Read this when you stop being scared of writing SQL by hand.
4. **"Database Internals" — Alex Petrov** *(pro level)* — how the engine actually works. Read once you've hit a real performance problem.

### Videos

- **Hussein Nasser** (YouTube) — networking and database fundamentals. Pragmatic and excellent on indexes and replication.
- **PGCon / PostgresOpen recorded talks** — the conferences are on YouTube. "Postgres Indexing Internals" by Bruce Momjian is a classic.

---

## C.4 ORMs and Migrations: SQLAlchemy 2.x + Alembic

**Why this matters here:** Every model in `api/models/`, every `select(…).where(…)` in services and routers. SQLAlchemy 2.x's `Mapped` / `mapped_column` typed style is what this project uses — old tutorials show the legacy `Column()` syntax, **skip those**.

### Documentation (primary source)

- **docs.sqlalchemy.org** — the "Unified Tutorial" for 2.0 is the right starting point. The legacy 1.x tutorial is *still* indexed — make sure the URL says `2.0/` or `latest/`.
- **alembic.sqlalchemy.org** — official Alembic tutorial.

### Books / longform

1. **"Essential SQLAlchemy" (2nd ed) — Jason Myers & Rick Copeland** — pre-2.0 syntax but the conceptual model is unchanged.
2. **"Architecture Patterns with Python" — Harry Percival & Bob Gregory** ⭐ uses SQLAlchemy as the persistence layer of a clean-architecture app. The repository + unit-of-work patterns illuminate why this codebase keeps services and routers thin.

### Videos

- **Mike Bayer's PyCon talks** (the SQLAlchemy maintainer) — search "Mike Bayer SQLAlchemy 2.0." Authoritative.

---

## C.5 Validation: Pydantic v2

**Why this matters here:** Every request/response in `api/schemas/` and the LLM contract in `ExtractionResult`. v2 is a near-rewrite of v1 — `model_validate`, `model_config`, `Field(...)`, `Annotated[...]`, validators. Old StackOverflow answers will mislead you.

### Resources

- **docs.pydantic.dev** — official migration guide v1→v2 is essential if you've used v1 before.
- **Tiangolo + Pydantic talks on YouTube** — short, focused.

No book yet matches v2 in depth — the docs are the primary source.

---

## C.6 Async Python and Concurrency

**Why this matters here:** Every IO operation in this app is `async`. The bot's `typing_action()` background task, the query LLM loop, asyncpg connections, Redis. Mistakes here look like flaky tests (see B.6) or 30-second hangs.

### Resources

1. **"Python Concurrency with asyncio" — Matthew Fowler** ⭐ best single resource.
2. **"Using Asyncio in Python" — Caleb Hattingh** — short, opinionated, excellent.
3. **mCoding YouTube** — "Asyncio is hard but really good" and similar.
4. **Łukasz Langa's PyCon keynotes** on asyncio internals (search his name).

---

## C.7 Caching, Queues, Sessions: Redis

**Why this matters here:** Every line in `bot/redis_keys.py` and `app/queries/history.py`. Pairing codes, pending proposals, rate limits, query history all live in Redis.

### Books

1. **"Redis in Action" — Josiah Carlson** — older but the data-model chapters age well.
2. **"Redis: The Definitive Guide" — by O'Reilly** — newer reference.

### Videos

- **Hussein Nasser's Redis playlist.**
- **Redis University** (free courses at university.redis.com) — official, well-paced.

---

## C.8 LLMs and Tool-Use: Anthropic Claude API

**Why this matters here:** `api/services/llm_extractor/`, `app/queries/llm_client.py`, the system prompts, the cache-control blocks, the tool-use loop. This is the *core differentiator* of the project.

### Primary sources (treat as required reading)

- **docs.anthropic.com** — read in this order:
  1. "Messages API" reference
  2. "Tool use" guide (the entire flow this project implements)
  3. "Prompt caching" guide (`cache_control={"type":"ephemeral"}` is how Phase 5b/6a survives token costs)
  4. "Vision" and "Extended thinking" — useful background even if unused here
- **Anthropic Cookbook** (github.com/anthropics/anthropic-cookbook) — runnable notebooks for tool-use, structured output, RAG.

### Books and longform

LLM-engineering books age in months, not years. Treat any book older than ~12 months as stale on tool-use, but durable on principles.

1. **"Building LLM Apps" — Valentina Alto** — broad introduction.
2. **"AI Engineering" — Chip Huyen** — production patterns. The cost/eval/observability chapters apply directly to this codebase's `llm_extractions` and `llm_query_dispatches` tables.
3. **"Designing Machine Learning Systems" — Chip Huyen** *(adjacent but useful — production ML thinking)*.

### Videos

- **Anthropic's official YouTube channel** — short, frequent, and matched to current API features.
- **Hamel Husain** (blog + talks) — practical eval and prompt engineering.
- **Jason Liu** (Instructor library author) — structured output. The patterns he teaches are exactly what `ExtractionResult` does.

---

## C.9 Telegram Bots: aiogram v3

**Why this matters here:** The entire `bot/` package. v3 is a rewrite from v2 — old tutorials and StackOverflow answers reference incompatible APIs.

### Primary sources

- **docs.aiogram.dev** — official docs. The "Migration FAQ" v2→v3 is critical context.
- **core.telegram.org/bots** — Telegram's own bot platform docs. You need to understand webhooks, inline keyboards, callback data, and message limits (4096 chars — see `bot/delivery_send.py`).

### Videos

- aiogram doesn't have a flagship YouTube creator. Search the official docs first; community videos are often v2.

---

## C.9b SPA: React 18 + Vite + TanStack Query + Tailwind

**Why this matters here:** The entire `web/` package — Phase 6d onboarding forms and Phase 6e Centro Financiero. TypeScript-strict, function components only, Tailwind for styling, Zod for wire validation, TanStack Query for server state, react-hook-form for the create flows, react-day-picker for the bills calendar, Recharts for the dashboard.

### Primary sources

- **react.dev** — the new React docs (post-2023 rewrite). The "Learn React" path is excellent; the "Reference" path is what you'll hit daily.
- **vite.dev** — config + build pipeline. Read the chunking + lazy import sections for context on why the SPA's bundle stays under 200KB.
- **tanstack.com/query/v5** — official TanStack Query docs. The "Important Defaults" page is mandatory.
- **react-hook-form.com** + **zod.dev** — both well-documented; the integration via `@hookform/resolvers/zod` is the pattern the project standardizes on.
- **tailwindcss.com** — utility-first; the docs are searchable and most class names are intuitive.

### Books

1. **"Learning React" (3rd ed) — Alex Banks & Eve Porcello** — modern hooks-first React.
2. **"React Key Concepts" — Maximilian Schwarzmüller** — denser; covers Suspense, transitions, and the new compiler context.
3. **"Effective TypeScript" — Dan Vanderkam** — the TS rules the SPA enforces (strict null checks, discriminated unions, branded types). Chapters 4 and 6 are gold.

### Videos

- **Theo (t3.gg) on YouTube** — pragmatic React + TypeScript + Tailwind explainers.
- **Jack Herrington** — patterns videos. His Suspense + lazy-loading walkthroughs map directly onto the `<Suspense>` blocks in `web/src/App.tsx`.
- **TanStack channel on YouTube** — short videos by Tanner Linsley on Query semantics.

### Things to *not* learn yet

- **Next.js / Remix** — different runtime + routing model. The SPA is plain Vite SPA + React Router; learning Next would conflate concerns.
- **CSS-in-JS (Emotion, styled-components)** — rejected; Tailwind is the standard.
- **Redux** — rejected; TanStack Query owns server state, Zustand is allowed for lightweight client state but isn't used yet.

---

## C.10 Containers and Deployment

**Why this matters here:** `Dockerfile`, `Dockerfile.prod`, `docker-compose.yml`. Production target is a single container with Uvicorn + the bot in webhook mode.

### Resources

1. **"Docker Deep Dive" — Nigel Poulton** *(beginner)*
2. **"The Docker Book" — James Turnbull** *(reference)*
3. **NetworkChuck on YouTube** — Docker fundamentals, very approachable.
4. **Bret Fisher's Docker Mastery** (Udemy) — paid, comprehensive.

---

## C.11 Testing: Pytest + pytest-asyncio

**Why this matters here:** ~85 async test files (per-block focused tests live under `tests/test_phase_*.py`). The NullPool-engine-per-test pattern in `conftest.py` is the *only* way the suite stays stable.

### Resources

1. **"Python Testing with pytest" (2nd ed) — Brian Okken** ⭐ canonical.
2. **Brian Okken's "Test & Code" podcast** — short episodes, applied.
3. **pytest-asyncio docs** — read for the `mode=auto` and event-loop scoping rules used here.

---

## C.12 Software Architecture (the glue)

**Why this matters here:** The "deterministic dispatcher + LLM extractor" split, the "data before AI" gating, the "single migration per change" rule — these are *architectural decisions*. Reading the books below will help you make analogous decisions for new features without breaking the project's principles.

### Books

1. **"The Pragmatic Programmer" (20th anniv ed) — Hunt & Thomas** — universal.
2. **"Architecture Patterns with Python" — Percival & Gregory** ⭐ already mentioned. The single most relevant book.
3. **"Designing Data-Intensive Applications" — Martin Kleppmann** — durable, multi-year reference.
4. **"A Philosophy of Software Design" — John Ousterhout** — short, sharp, opinionated. "Modules should be deep" is exactly the discipline this codebase tries to maintain.
5. **"Refactoring" (2nd ed) — Martin Fowler** *(JavaScript edition, but principles apply).*

---

## C.13 Recommended order if you're starting from scratch

If you can read Python loops and functions but everything in this repo looks like static, work in this order:

| Week(s) | Focus | Resource |
|---|---|---|
| 1–2 | Modern Python + types | "Fluent Python" ch. 1–8 + "Robust Python" |
| 2–3 | SQL fundamentals | "Learning SQL" + run queries against the local Postgres |
| 3–4 | FastAPI tutorial | tiangolo.com/tutorial — build their toy app from scratch |
| 4–5 | SQLAlchemy 2.0 | Official 2.0 Unified Tutorial + read 5 models in `api/models/` |
| 5–6 | Async Python | "Python Concurrency with asyncio" |
| 6 | Pydantic v2 | Official docs migration guide |
| 7 | Pytest async | "Python Testing with pytest" + read `tests/conftest.py` |
| 7–8 | Anthropic API + tool use | docs.anthropic.com + Cookbook notebooks |
| 8 | aiogram v3 | Official docs + read `bot/handlers.py` and `bot/pipeline.py` |
| 9 | Architecture | "Architecture Patterns with Python" |
| 9–10 | React + TanStack Query | react.dev + TanStack Query docs + read `web/src/routes/Dashboard.tsx` |
| 10+ | Postgres mastery | "The Art of PostgreSQL" + run `EXPLAIN ANALYZE` on this app's slow queries |

After week 4 you can add a CRUD endpoint solo. After week 8, you can extend the bot. After week 10, you can ship a SPA route end-to-end (schema → API helper → route → tests). Beyond that, you can confidently rework the architecture.

---

## C.14 What to skip (for now)

These are dead ends for *this* project. Save them for later or never:

- **Django, Flask tutorials** — wrong framework. Skills don't transfer cleanly to FastAPI's dependency-injection model.
- **SQLAlchemy 1.x material** — the syntax is incompatible. Verify everything you read targets 2.0+.
- **LangChain / LlamaIndex courses** — this project deliberately uses the raw Anthropic SDK. Frameworks would hide the cache-control discipline that keeps token cost under control.
- **Vector databases / RAG** — explicitly excluded by `CLAUDE.md` ("What NOT to Build").
- **Self-hosted LLM tutorials** — same reason.
- **WhatsApp Baileys tutorials** — banned by Meta; this project uses Telegram + (eventually) the official WhatsApp Cloud API.

---

## C.15 Two-hour weekly maintenance routine

Once you're up to speed, this keeps the project healthy:

1. `git log --since="1 week"` — review what changed.
2. `pytest -q` — confirm the suite is green. Use the focused phase slices (e.g. the B5/B6 verification command in `docs/phase-6e-decisions.md`) when you only changed one phase's code.
3. Run `scripts/phase5b_smoke.sh`, `docs/curl/phase-5d.sh`, and `scripts/test_phase_6d.sh` against a clean DB.
4. `npm --prefix web run lint && npm --prefix web run build` — watch the `index` chunk; it should stay around 119–120 KB gzip.
5. Skim Anthropic's changelog (docs.anthropic.com → release notes) — model deprecations and new tool-use features land regularly.
6. Skim aiogram + react-day-picker GitHub releases for breaking changes.
7. Review the **Technical Debt** section of `CLAUDE.md`. Pick one item if you have spare cycles.

---

## Final note

The codebase rewards readers who follow the phase order. If something doesn't make sense, the answer is almost always in the migration that introduced the table, the corresponding `docs/phase-*-decisions.md`, or the phase section of `CLAUDE.md`. When in doubt, `git log -p <file>` tells you why.

The decisions docs (`docs/phase-Nx-decisions.md`) are *load-bearing*. They're the canonical contract for everything in that phase — including what we explicitly chose NOT to do. If you're about to "improve" something that feels weird, check the decisions doc first.
