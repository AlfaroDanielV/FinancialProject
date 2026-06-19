# Learning Guide — Personal Finance Agent

> A self-study path from "I can read Python" to "I can build and maintain this project alone."
> Pair this with `CLAUDE.md` (canonical architectural reference, kept current).
> Last refresh: 2026-06-05 (Phase 6f closed B0–B16 — native iOS app is now the **only** structured UI surface; the Phase 6e SPA `web/` was deleted at B16, 2026-06-01. Post-6f work landed: conversational creation (goals/income/bills/debt), Native Gmail (connect + senders + shadow review), and Envelope budgeting "Sobres". Migration head is `0022`.)

---

## 0. How to use this guide

This is a 3-part document:

1. **Part A — Codebase tour.** What every directory does, in dependency order. Read top-to-bottom on day one.
2. **Part B — Navigation playbook.** Repeatable recipes for "I want to add a feature / debug X / understand Y." Use this when working.
3. **Part C — Learning path.** Books + video resources, ordered beginner → pro. Each topic maps to where in *this* codebase you'll see it.

**Don't try to learn everything before touching the code.** The codebase is the textbook. Run it locally, break it on purpose, and use Part C to fill the gaps you actually hit.

### Where this project is headed (the end goal)

Everything in this guide serves one arc, stated in `CLAUDE.md`:

1. **Personal MVP** — a system you trust enough to check *instead of* your bank app. (Largely reached: the native app + chat is the daily driver.)
2. **Stabilization** — 4+ weeks of reliable daily use with accurate reports.
3. **Product** — a multi-tenant SaaS financial assistant that lives in a chat thread + native app.

The **core thesis** gates the whole thing: *if the ledger is wrong, the agent is useless.* Data accuracy is a prerequisite to any AI layer. That is why the write path is deterministic and the LLM only ever *extracts* and *explains* — never decides or calculates. Keep this in mind; it explains almost every "why is it built this way" question.

The two big features still ahead — and therefore the highest-value things to understand deeply — are:

- **P7, the affordability / pushback engine** (`CLAUDE.md` → "The Pushback Engine"). Deterministic math decides feasibility; the LLM only wraps the result in Spanish. This is called out as "the hardest feature."
- **P8/P9, multi-tenancy + SaaS hardening.** Every domain table already carries `user_id`; the work is auth, billing, isolation, compliance, observability.

---

# PART A — Codebase Tour

## A.1 The big picture in 60 seconds

You're looking at a **FastAPI + Postgres + Redis** backend with **two primary input surfaces** and **one structured UI surface**:

- **Input:** a **Telegram bot** (aiogram) and an **in-app chat** in the native app — both run the *same* server-side pipeline (`bot/pipeline.py::process_message()`), so there is exactly one extractor and one write dispatcher. Capture also arrives from the iPhone Shortcut webhook and the Gmail parser (BAC / Davivienda / Promerica).
- **Structured UI:** a **React Native (Expo SDK 54) iOS app** under `mobile/`. This is the *only* structured read/edit surface. The Phase 6e Vite SPA (`web/`) was retired and **deleted** at Phase 6f B16 (2026-06-01); do not look for it and do not re-add a web client without an explicit decision.

What the system does: captures transactions from those sources, stores them in Postgres as the source-of-truth ledger, and layers on:

1. **Conversational capture + queries.** Claude (Haiku for extraction, Sonnet for queries) turns Spanish messages into structured intents; deterministic Python routes and commits. Reachable from Telegram and from `POST /api/v1/chat/message`.
2. **Conversational *creation*.** Goals, recurring incomes, and recurring bills are created *in chat* (the LLM proposes structured fields, deterministic code writes). Debt is chat-initiated then finished in a pre-filled native form (amortization fields are too complex for free text).
3. **Structured native screens** for accounts, transactions, bills, debts, incomes, goals, categories, memoria, Gmail, and **envelope budgeting ("Sobres")**.

Five top-level Python packages matter, plus the native app:

| Package | Role | When you'll touch it |
|---|---|---|
| `api/` | FastAPI HTTP layer + ORM models + business services | Adding endpoints, schema changes, business logic |
| `app/queries/` | Read-only conversational query layer (LLM + tools) | Adding a new "answerable question" the chat can handle |
| `bot/` | Pipeline shared by Telegram **and** the native chat — extractor → router → dispatcher → delivery | Capture behavior, conversational creation, Spanish copy, callback flows |
| `mobile/` | **Expo SDK 54 + React Native + TypeScript native iOS app — the only structured UI** | Every UI feature: screens, in-app chat, receipt upload, envelopes |
| `workers/` | Background job entrypoints (Gmail daily, insights nightly + lifecycle) | Container Apps Jobs |

Plus three support directories:

| Directory | Role |
|---|---|
| `migrations/versions/` | Hand-written Alembic migrations, numbered `0001_…` through `0022_envelopes.py` |
| `tests/` | Pytest async suite (~109 test files; per-block focused tests under `tests/test_phase_*.py`) |
| `docs/` | Per-phase decisions (`docs/phase-*-decisions.md`) and curl smokes (`docs/curl/`); `scripts/` holds bash phase-gate runners (`scripts/test_phase_6f.sh` is the current gate) |

> **`web/` is gone.** If you find a reference to a Vite SPA, a `fa_session` cookie, Static Web Apps, or `localhost:5173`, it is historical. `git log -- web/` shows the retired code; nothing in the live tree depends on it.

---

## A.2 Read-order for new contributors

Follow this exact sequence. Each file builds on the previous.

### Step 1 — Boot path (understand startup)

1. `pyproject.toml` — pinned dependency versions. Note: SQLAlchemy 2.x async, Pydantic v2, anthropic SDK, aiogram v3, `python-multipart` (receipt/PDF upload).
2. `docker-compose.yml` — three services: `db` (Postgres 16), `redis` (Redis 7), `api` (Uvicorn).
3. `.env.example` — every config knob the app reads.
4. `api/config.py` — Pydantic `BaseSettings` that consumes `.env`. Note the `_enforce_prod_secret_store` validator: it refuses to boot if `ENVIRONMENT=production` and Gmail OAuth tokens aren't in Azure Key Vault.
5. `api/database.py` — async SQLAlchemy engine + `get_db` FastAPI dependency.
6. `api/redis_client.py` — singleton async Redis connection.
7. `api/main.py` — `lifespan` context manager, router registration, `/health` and `/health/ready`. (CORS middleware was removed at 6f B16 — native + Shortcut are non-browser clients.)

Stop here and run it: `docker compose up -d`, then `curl localhost:8000/health`.

### Step 2 — Domain model (understand the data)

8. `api/models/base.py` — `DeclarativeBase` for all ORM classes. `api/models/enums.py` — shared enums.
9. `api/models/__init__.py` — imports every model; this is what Alembic sees.
10. Read models in this order to understand relationships:
    - `user.py` → `account.py` → `transaction.py` (the spine; `transaction.py` carries `transfer_id` + `category_id` (6e), `archived` (6e B5), and **`envelope_id`** (Sobres) FKs)
    - `recurring_bill.py` → `bill_occurrence.py` → `notification_rule.py` → `notification_event.py` (Phase 4)
    - `budget.py`, `debt.py`, `custom_event.py`, `weekly_report.py`
    - `pending_confirmation.py` → `user_nudge.py` (Phase 5d)
    - `llm_extraction.py` → `llm_query_dispatch.py` (Phase 5b/6a observability)
    - `gmail_credential.py`, `gmail_sender_whitelist.py`, `bank_notification_sample.py`, `gmail_message_seen.py`, `gmail_ingestion_run.py`, `gmail_discovery_run.py` (Phase 6b)
    - `user_insight.py` (Phase 6c — typed user memory)
    - `magic_link_token.py`, `recurring_income.py`, `lazy_detection_event.py` (Phase 6d onboarding + magic-link auth)
    - `goal.py`, `goal_contribution.py`, `transfer.py`, `user_category.py`, `currency_rate.py` (Phase 6e Centro Financiero)
    - `envelope.py` (Sobres — spending-cap envelopes)
11. `migrations/versions/0001_initial_schema.py` through `0022_envelopes.py` — read in numeric order; this is the schema's history. Phase landmarks: `0006` (multi-tenant `users`), `0011` (Phase 6b Gmail + status CHECK), `0013/0014` (Phase 6c insights), `0016` (Phase 6d magic links + recurring incomes), `0017` (Phase 6e foundation + `currency_rates` + materialized views), `0018/0019/0020` (the three `archived` columns), `0021` (`users.expo_push_token`, schema-only P8 prep), `0022` (`envelopes` table + `transactions.envelope_id`).

### Step 3 — HTTP surface (understand the API)

12. `api/dependencies.py` — `current_user` resolves in order: **`X-Shortcut-Token` → bearer JWT (`Authorization: Bearer <jwt>`) → `X-User-Id` dev shim.** (The SPA `fa_session` cookie branch was removed at 6f B16 — there is no cookie path anymore.) The strict `current_user_via_token` is what `POST /transactions/shortcut` and every `POST /jobs/*` use; it ignores the dev shim.
13. `api/schemas/transaction.py` — Pydantic v2 request/response models. Notice `ShortcutTransactionCreate`, the 6e additions (`TransactionUpdate`, `TransactionListResponse.next_cursor`, bulk bodies), and that `TransactionResponse` now carries `envelope_id` and `PATCH` accepts `envelope_id`.
14. `api/routers/transactions.py` — the most-developed router; the pattern to copy. Filters, cursor pagination, CSV export, bulk archive/categorize, and the `envelope_id` assignment validation all live here.
15. `api/routers/users.py` — registration + token rotation.
16. `api/routers/jobs.py` — the three Phase 4 batch jobs + Phase 5d evaluator/delivery jobs + Phase 6c insights compute trigger.
17. `api/routers/auth.py` — `POST /auth/magic-link/exchange` and Phase 6f `POST /auth/device-code/exchange`. **Both are bearer-only** (no cookie since B16); both terminate in `issue_session_jwt` and return `{token, expires_at, user_id, email, full_name}`. Device-code codes are minted/consumed by `api/services/auth/device_code.py` (Redis, TTL 5 min, alphabet `[A-HJ-NP-Z2-9]`).
18. `api/routers/chat.py` — Phase 6f B2/B6. `POST /chat/message` calls `bot/pipeline.py::process_message()` directly and returns a serialized `BotReply`; `POST /chat/image` (multipart) routes a receipt photo through `api/services/llm_extractor/vision.py`. This is how the native chat reuses the entire bot pipeline without duplication.
19. `api/routers/onboarding.py`, `api/routers/recurring_incomes.py` — Phase 6d onboarding + incomes (incl. the `derive-cycles` aguinaldo/salario_escolar action).
20. `api/routers/dashboard.py` — Phase 6e dashboard summary, daily cash-flow, category breakdown.
21. `api/routers/goals.py`, `api/routers/transfers.py`, `api/routers/categories.py`, `api/routers/debts.py` — Phase 6e entities (debts incl. `/schedule` and `/payoff-scenarios`).
22. `api/routers/recurring_bills.py` — Phase 6e B6 `POST /{id}/mark-paid` with Redis-backed idempotency on top of the Phase 4 CRUD.
23. `api/routers/envelopes.py` — **Sobres.** `POST/GET /envelopes`, `GET /envelopes/summary` (declared *before* `/{id}`), `GET/PATCH/DELETE /envelopes/{id}` (soft archive by default; `?hard=true` removes the row — the `transactions.envelope_id` FK is `ON DELETE SET NULL`, so tagged transactions are unlinked, never deleted).
24. `api/routers/gmail.py` — the native Gmail surface: `POST /gmail/scan` (+ `/scan/status`), `GET/POST/DELETE /gmail/senders`, `GET /gmail/shadow`, `POST /gmail/shadow/{confirm,discard}`. `current_user`-authed; OAuth `/oauth/start` + `/status` predate it.
25. `api/routers/privacy_insights.py` — the memoria read/edit/delete/export endpoints (`GET/PATCH/DELETE /users/me/insights`, group + all-delete, export).
26. Then skim the rest of `api/routers/`. They follow the same shape.

### Step 4 — Services (understand the business logic)

The `api/services/` tree is the heart of the deterministic layer. Read in this order:

27. `api/services/recurrence.py` — RRULE + frequency expansion → `bill_occurrences`. The hardest pure-Python file. `link_transaction_to_occurrence` backs the bill-level mark-paid.
28. `api/services/transactions.py` — narrow dispatcher helpers: `create_transaction`, `delete_telegram_transaction` (the /undo guard), `recent_for_user`, `sum_in_window`, `window_bounds`.
29. `api/services/accounts.py` — `resolve_account` (fuzzy match for the chat) plus `compute_account_balances` (one-pass per-account current + month-start balance; excludes archived rows).
30. `api/services/transfers.py` — atomic transfer creation that emits the two linked `transactions` rows.
31. `api/services/categories.py` — user-category CRUD + archival rules (`(user_id, lower(name)) WHERE archived=false` uniqueness).
32. `api/services/envelopes.py` — **`compute_envelope_summary`.** One grouped query over confirmed, non-archived, non-transfer, `amount < 0` rows in the current month, grouped by `envelope_id` + currency; per-class subtotals; a best-effort `monthly_income` line. **Spend is computed live from transactions** — there is no stored running balance, so a bar can never drift from the ledger.
33. `api/services/fx.py` — **`convert`.** Cross-currency spend (a US$ expense tagged to a CRC envelope) is converted at `FALLBACK_USD_TO_CRC = ₡500/US$`. **₡500 is a placeholder** pending the BCCR API (tech-debt in `CLAUDE.md`); `currency_rates` (migration 0017) exists but nothing reads it yet.
34. `api/services/dashboard/` — `summary.py` (live current-month aggregator + daily/cash-flow series) and `materialized.py` (refreshes `mv_monthly_summary_by_user` / `mv_yearly_summary_by_user`).
35. `api/services/auth/` — magic-link issuance + exchange, device-code mint/consume, and the bearer JWT codec (`issue_session_jwt` / `decode_session_jwt`).
36. `api/services/finance/` — backend-owned finance derivations (aguinaldo / salario_escolar dates, French amortization input validation).
37. `api/services/insights/` — Phase 6c user-memory pipeline: `computed.py` (deterministic SQL-driven), `extractor.py` (Haiku → typed `InsightContent`), `lifecycle.py` (TTL/dedup/redaction), `persister.py`. **Two writers, never crossed** — computed never calls an LLM; the extractor never queries aggregates.
38. `api/services/gmail/` — Phase 6b OAuth + scanner (`scan_user_inbox`) + reconciler + parsers (BAC, Davivienda, Promerica) + sender discovery, plus `shadow_review.py` (`list_shadow` / `confirm_shadow` / `discard_shadow`, shared by the bot's `/aprobar_shadow` and the native review screen — shadow rows can't be PATCHed, so per-row edits apply atomically inside confirm).
39. `api/services/nudges/` — read in this order:
    - `policy.py` (constants — rate limit, silence threshold, quiet hours)
    - `evaluators/` (pure functions that emit candidate nudges)
    - `orchestrator.py` (dedup + silence filter + insert)
    - `delivery.py` (the 4 anti-saturation rules in code)
    - `actions.py` (state machine — dismiss/act + auto-silence)
    - `phrasing.py` (LLM call that writes the Spanish copy — it NEVER decides whether to nudge)
40. `api/services/llm_extractor/` — the Anthropic tool-use call that powers capture *and* conversational creation:
    - `schema.py` (`ExtractionResult` + the `Intent` enum, incl. `CREATE_GOAL/INCOME/BILL/DEBT`)
    - `prompt.py` (system prompt + `cache_control` blocks — keep caching ON)
    - `client.py` (Anthropic SDK wrapper; `LLMClient` protocol)
    - `runner.py` (glue + persistence to `llm_extractions`)
    - `vision.py` (receipt photo → same `ExtractionResult`, Haiku→Sonnet retry < 0.65)
    - `document.py` (loan-contract PDF → `DebtTermsExtraction`, same retry pattern)
41. `api/services/telegram_dispatcher.py` — **the deterministic write/route brain.** `_dispatch_create_goal/income/bill/debt` validate the LLM-proposed fields and either clarify, propose, or (for debt) return an `OpenScreenAction`. This is the "LLM extracts; rules decide" rule in code — read it to understand why the write dispatcher never asks an LLM what to do.
42. `api/services/dispatch/`, `api/services/extraction/`, `api/services/secrets.py` — thin glue + the Key-Vault-backed secret store.

### Step 5 — Bot pipeline (understand the shared chat layer)

The bot package is ~26 modules (`ls bot/`). **Everything here is shared by Telegram and the native chat** — `process_message()` is channel-agnostic.

43. `bot/redis_keys.py` — every Redis key, with TTL contracts. Keys keep the historical `telegram:` prefix even though the native chat reuses them (renaming would break in-flight Telegram state — tracked tech debt).
44. `bot/app.py` — aiogram `Bot` + `Dispatcher` + start/stop hooks for `lifespan`.
45. `bot/handlers.py` — aiogram routes (text, callbacks, commands).
46. `bot/pipeline.py` — **the brain.** Resolve user → rate limit → command short-circuit → LLM → route → dispatch → reply. `BotReply` now carries `open_screen` (e.g. `screen="assign_envelope"` after an expense commits, `screen="debt_create"` for the debt flow) and `url_buttons`. Read it top-to-bottom twice.
47. `bot/pending.py` + `bot/pending_db.py` — the two-tier proposal store (Redis 5 min + Postgres 48 h).
48. `bot/commit.py` — the write-commit seam. `_commit_goal` / `_commit_income` / `_commit_bill` write the rows for conversational creation (mirroring the matching routers). Debt has no chat commit — it commits via the native form's `POST /debts`.
49. `bot/clarification.py` — the clarify loop shared across dispatchers (`merge_reply`, the Spanish field-by-field prompts).
50. `bot/delivery_send.py` — sanitize HTML → split for Telegram's 4096-char limit → sequential send. (The native chat renders `BotReply` directly; only Telegram needs the split.)
51. `bot/gmail_handlers.py`, `bot/gmail_listener.py`, `bot/gmail_onboarding.py`, `bot/gmail_pubsub.py` — Phase 6b Gmail flow (OAuth init → discovery → daily worker hand-off).
52. `bot/memory_handlers.py` — Phase 6c `/memoria`, `/olvidar`, `/editar_memoria`, `/recalcular_memoria`.
53. `bot/onboarding_handlers.py`, `bot/onboarding_welcome.py`, `bot/account_creation.py` — onboarding + the lazy-account-creation Redis flow (the pattern conversational creation extends).
54. `bot/deep_link.py` — `mint_native_deep_link` only (the SPA `mint_edit_session_url` was removed at B16). Mints `ledgercr://exchange?token=...` so a tap in Telegram signs into the native app.

### Step 6 — Native iOS app (the only structured UI surface)

The native app lives entirely under `mobile/`. Expo SDK 54 (managed workflow), TypeScript, React Navigation 7, TanStack Query 5, React Hook Form + Zod, Axios with a bearer-token interceptor, `expo-secure-store`. iOS-only. Read in this order:

55. `mobile/package.json` — pinned deps. Critical: `expo@~54.0.34`, `react@19.1.0`, `react-native@0.81.5`, `expo-secure-store` (JWT storage), `expo-web-browser` + `expo-linking` (Gmail connect + magic-link fallback), `expo-image-picker` (receipts, B6), `expo-document-picker` (loan PDF, debt D3), `@react-navigation/native` + `bottom-tabs` + `native-stack`.
56. `mobile/.env.local` — **not checked in.** `EXPO_PUBLIC_API_BASE_URL=http://<LAN-IP>:8000` (the client appends `/api/v1`) and `EXPO_PUBLIC_SENTRY_DSN` (empty = console no-op in Expo Go). See `docs/LOCAL_DEV.md §8`.
57. `mobile/src/lib/` — the plumbing: `client.ts`-equivalent lives here too (`env.ts`, `queryClient.ts`), plus `auth.ts` + `AuthContext.tsx` (JWT lifecycle), `exchange.ts` (device-code/magic-link exchange), `deepLink.ts`, `format.ts` (shared `formatMoney`), `amortization.ts` (lifted from the SPA — French amortization + early-payoff, mirrors `api/services/amortization.py`), `observability.ts` (Sentry scaffold, deliberately not importing the native module under Expo Go).
58. `mobile/src/api/client.ts` — Axios instance with the bearer-token interceptor. Reads the JWT from `expo-secure-store`; 401 clears the token and bounces to Login.
59. `mobile/src/screens/Login.tsx` — device-code login. User gets a 6-char code from `/login` in Telegram, types it; the screen auto-submits at 6 valid chars → `POST /auth/device-code/exchange` → JWT into Secure Store.
60. `mobile/src/hooks/useMagicLinkListener.ts` — silent fallback mounted in `App.tsx`. Listens for `ledgercr://exchange?token=...` so a bot-sent deep link signs in without typing the code.
61. `mobile/src/navigation/` — the 5-tab bottom navigator (`AppNavigator.tsx`: Home / Chat / Accounts / Transactions / Más). Per-tab stacks: `ChatNavigator` (Chat → DebtCreate modal), `AccountsNavigator`, `TransactionsNavigator`, `MasNavigator` (the "Más" hub → bills, debts, incomes, goals, categories, memoria, Gmail).
62. `mobile/src/theme.ts` — the design system (warm parchment palette, Feather icons, red reserved for expense/overdue). There is **no Tailwind** — styling is React Native `StyleSheet`.
63. `mobile/src/screens/` — work through the screen set against its backend:
    - `Dashboard.tsx` (+ `components/SobresSection.tsx`) — period picker, balance, income/expense/net, expandable category + upcoming-bills sections, and the **Sobres** money-left bars.
    - `Chat.tsx` — inverted FlatList, confirm/cancel + URL chips, receipt camera, and the in-chat **"Asignar a un sobre"** chip after an expense commit.
    - Accounts / Transactions / Bills / Debts / Incomes / Goals / Categories / Memory screens — each pairs with a `mobile/src/api/<resource>.ts` helper.
    - `DebtCreateScreen.tsx` — the chat-handoff debt form: prefill from `open_screen`, live cuota preview, Ley 7472 warning, loan-PDF upload, and the no-rate "llamá a tu entidad" fallback.
    - `GmailScreen.tsx` / `GmailSendersScreen.tsx` / `GmailReviewScreen.tsx` — connect (poll-based), sender whitelist, and per-row keep/discard shadow review.
64. `mobile/src/components/` — reused widgets: `TransactionEditModal.tsx` (amount/merchant/desc/category/date + a **Sobre** field for expenses), `EnvelopePickerModal.tsx`, `EnvelopeEditModal.tsx`, `EnvelopeDetailModal.tsx`, `ErrorBoundary.tsx`. Envelope bar direction (drain vs fill) + the 5%-red threshold live in `api/envelopes.ts::envelopeProgress`.

**Auth flow to understand (final shape):**
1. Operator runs `/login` (or `/iniciar`) in Telegram.
2. Bot replies with a 6-char code (and a tappable `ledgercr://` deep link).
3. Operator types the code in Login; app auto-submits at 6 valid chars.
4. `POST /auth/device-code/exchange` returns `{token, expires_at, user_id, email, full_name}`.
5. JWT stored in `expo-secure-store`; every call sends `Authorization: Bearer <jwt>`.
6. Backend resolves the caller via the **bearer branch** in `api/dependencies.py::current_user`.

**Hard rules for native/chat code:**
- The chat calls `POST /chat/message` — it does NOT replicate the extractor or dispatcher. `process_message()` runs on the server.
- The bearer JWT is decoded by the *same* `decode_session_jwt` everything else uses. No second secret, no second token shape.
- Receipt/PDF extraction emits the *same* `ExtractionResult` / typed payload as text, so the deterministic write dispatcher consumes it identically.
- `open_screen` is native-only; Telegram ignores it. That's how envelopes and the debt form stay native without forking the pipeline.
- All user-facing copy stays Spanish (voseo, CR).

### Step 7 — Query layer (understand how questions are answered)

65. `app/queries/prompts/system.py` — system prompt for the query LLM. **Insights never inline here** — Sonnet reads user memory only through the `user_context` tool.
66. `app/queries/tools/base.py` — the `Tool` abstraction.
67. `app/queries/tools/transactions.py` — the most-used tool. Read it carefully.
68. The full tool roster (registered in `app/queries/tools/__init__.py`): `transactions`, `accounts`, `compare_periods`, `debts`, `pending`, `recurring_bills`, `user_context`, plus `_common` and `_test_only` infra.
69. `app/queries/llm_client.py` — Anthropic tool-use loop (iteration cap 4, `cache_control` on the last tool only, token accounting).
70. `app/queries/dispatcher.py` — orchestrates the loop + history + audit row. **Read-only; it cannot mutate state.**
71. `app/queries/history.py` — Redis-backed conversation history (24h TTL). `app/queries/delivery.py` — error→Spanish-message mapping.

### Step 8 — Workers + scheduled jobs

72. `workers/gmail_daily.py` — Phase 6b scanner; runs as an Azure Container Apps Job.
73. `workers/insights_nightly.py` — Phase 6c computed-insight refresh + dashboard materialized-view refresh (same job).
74. `workers/insights_lifecycle.py` — Phase 6c TTL/dedup/`raw_quote` redaction sweep.

### Step 9 — Tests (understand what "correct" means)

75. `tests/conftest.py` — the per-test NullPool engine pattern. **Critical.** Async tests share an event loop; pools that span loops cause flaky failures. Note the `_insights_dispatcher_flag_on` autouse fixture and `_reset_redis_singleton`.
76. Pick one passing test from each era and trace assertions back to the code:
    - `tests/test_telegram_dispatcher.py` (write dispatch)
    - `tests/test_nudges_evaluators.py` (Phase 5d)
    - `tests/test_phase_6a_block5b_e2e.py` (full query loop)
    - `tests/test_phase_6c_*.py` (insights pipeline)
    - `tests/test_phase_6f_chat_create_goal.py` / `_income` / `_bill` / `_debt` (conversational creation, FixtureLLMClient + direct dispatch)
    - `tests/test_phase_6f_b6_vision.py` (receipt path), `tests/test_phase_6f_debt_parse_document.py` (PDF path)
    - `tests/test_gmail_native.py` (native Gmail REST + shadow review)
    - `tests/test_envelopes.py` + `tests/test_phase_6f_chat_assign_envelope.py` (Sobres)
77. `scripts/test_phase_6f.sh` — the current end-to-end gate: mobile `tsc --noEmit` + the focused backend slices + regression. Run it before and after any change. Older smokes (`scripts/phase5b_smoke.sh`, `docs/curl/phase-6a.sh`) still exercise the pipeline.

---

## A.3 Mental model cheat sheet

Pin these — they show up everywhere:

- **Money is `NUMERIC(12,2)` or `NUMERIC(14,2)`.** Negative = expense, positive = income. Never use `float`.
- **All timestamps are `TIMESTAMPTZ` in UTC.** The user's local timezone (`users.timezone`, default `America/Costa_Rica`) is applied at display/quiet-hours/month-reset time.
- **All PKs are UUIDv4** via `gen_random_uuid()`.
- **Multi-tenancy = `user_id` FK on every domain table.** The `current_user` dependency resolves the tenant. This is the seam P8 multi-tenancy builds on — it's already there.
- **The LLM never decides whether to act, and never calculates.** The extractor/vision/document calls produce structured payloads; deterministic Python routes, validates, and commits. The query dispatcher is the *only* LLM-on-the-hot-path component, and it's read-only. The P7 pushback engine will keep this discipline: math decides feasibility, the LLM only phrases it.
- **One pipeline, two channels.** Telegram and the native chat both call `process_message()`. There is no second extractor or dispatcher.
- **Redis is the source of truth for durable bot/chat state.** aiogram FSM is for transient in-handler bookkeeping only. Keys keep the `telegram:` prefix for historical reasons.
- **Migrations are hand-written.** No `--autogenerate`. Every schema change → new numbered file. Head is `0022`.
- **Auth resolves in this order: `X-Shortcut-Token` → bearer JWT → `X-User-Id` dev shim** (`api/dependencies.py::current_user`). No cookie path (removed at 6f B16). The strict variant ignores the dev shim. Magic-link and device-code exchange both issue identical bearer JWTs.
- **Three dimensions of "this row is special": `status`, `archived`, and `envelope_id`.** `status` (confirmed/shadow/pending_review) is the ingestion lifecycle; `archived` is user soft-delete; `envelope_id` tags an expense to one spending-cap envelope. Balance/dashboard/envelope math all exclude non-`confirmed` and `archived=true`. (The materialized views still miss the `archived` predicate — tech debt.)
- **Envelope spend is computed live, never stored.** A money-left bar is derived from transactions each render, so it can't drift from the ledger. Cross-currency spend converts via `api/services/fx.py` at the ₡500/US$ placeholder.
- **`open_screen` is the native-only escape hatch.** The pipeline returns it to hand off to a native screen (debt form, envelope assignment); Telegram ignores it. This keeps native-only features out of the shared pipeline.
- **Insights never inline in the system prompt.** Sonnet reads user memory through `get_user_context` only (Phase 6c).
- **Cache breakpoint limit is 4.** Apply `cache_control` only on the last tool/block, not all of them. Exceeding silently breaks caching.
- **The native iOS app runs via Expo Go** on the operator's iPhone during development. Set `EXPO_PUBLIC_API_BASE_URL` to the machine's LAN IP in `mobile/.env.local`. See `docs/LOCAL_DEV.md §8`.

---

# PART B — Navigation Playbook

Recipes you'll repeat dozens of times. Bookmark this section.

## B.1 "I want to add a new HTTP endpoint"

1. **Schema first.** Add Pydantic v2 request/response classes in `api/schemas/<resource>.py`. Use `model_config = ConfigDict(from_attributes=True)` for ORM read models; `extra="forbid"` for PATCH bodies that must reject immutable fields (see `EnvelopeUpdate`, the debts/incomes PATCH schemas).
2. **Model.** If a new table: create `api/models/<name>.py`, register it in `api/models/__init__.py`.
3. **Migration.** Copy the latest `migrations/versions/00XX_…py` as a template. Bump the prefix. Hand-write `upgrade()` and `downgrade()`.
4. **Router.** Create or edit `api/routers/<resource>.py`. Use `APIRouter(prefix="/api/v1/<resource>", tags=["<resource>"])`. Inject `db: AsyncSession = Depends(get_db)` and `user = Depends(current_user)` (or `current_user_via_token` if it must reject the dev shim). **Declare specific paths before `/{id}`** (see `GET /envelopes/summary`).
5. **Mount.** Add `app.include_router(<resource>.router)` in `api/main.py`.
6. **Test.** New file in `tests/test_<resource>.py`. Follow the NullPool pattern from `conftest.py`.
7. **Native helper.** If the app consumes it, add a typed fetcher to `mobile/src/api/<resource>.ts` — and double-check the JSON body field names match the schema (see B.6, drift bug #4).

## B.2 "I want to add a new query the chat can answer"

1. **Tool definition.** New file in `app/queries/tools/<name>.py`. Subclass `Tool`. Define `input_schema`, `name`, `description`, and `async def run(ctx, args)`.
2. **Register.** Add to `app/queries/tools/__init__.py`.
3. **Update system prompt.** `app/queries/prompts/system.py` — Claude needs to know the tool exists and when to call it.
4. **Test.** Unit test in `tests/` (no LLM). Then an e2e against the `tests/test_phase_6a_block*_e2e.py` pattern (real Anthropic — gate behind an env var).
5. **No new dispatcher logic.** The loop in `llm_client.py` picks up registered tools automatically.

## B.3 "I want to add a conversational *creation* flow (chat creates a row)"

This is the post-6f pattern for goals/income/bills (debt is the hybrid variant). Follow the existing four as templates.

1. **Intent + fields.** Add to the `Intent` enum and the flat `<thing>_*` fields on `ExtractionResult` in `api/services/llm_extractor/schema.py`. Add prompt guidance + examples in `prompt.py`.
2. **Dispatcher.** Add `_dispatch_create_<thing>` in `api/services/telegram_dispatcher.py`. Validate each NOT-NULL field; clarify field-by-field via `bot/clarification.py`; resolve date hints server-side; then **propose** (don't write).
3. **Commit.** Add `_commit_<thing>` in `bot/commit.py`, mirroring the matching router's create logic. The commit is deterministic Python — never an LLM call.
4. **Native-only finish (optional).** If the form is too complex for chat (like debt's amortization fields), return an `OpenScreenAction` instead of committing, and build/extend the native screen to consume `open_screen.prefill`.
5. **Test.** Drive it with `FixtureLLMClient` + direct dispatch (see `tests/test_phase_6f_chat_create_goal.py`). No real LLM needed for the deterministic path.
6. **Preserve the rule:** the LLM proposes structured fields; deterministic code decides and writes. Never let the dispatcher ask an LLM what to do.

## B.4 "I want to add a Telegram/chat command (e.g. `/foo`)"

1. **Handler.** aiogram handler in `bot/handlers.py` decorated with `@router.message(Command("foo"))`.
2. **New pipeline branch?** Edit `bot/pipeline.py`. Add the command short-circuit *before* the LLM extractor block — commands must never burn tokens.
3. **Spanish copy.** Add user-facing strings to the Spanish copy module; don't inline them.
4. **Test.** Drive it via `POST /api/v1/telegram/_simulate` or `POST /chat/message` in dev.

## B.5 "I want to add a native screen"

1. **API helper first.** Add a typed fetch/mutate helper to `mobile/src/api/<resource>.ts` (or create one). All network goes through `mobile/src/api/client.ts` (bearer interceptor + 401 handling); never raw `axios`.
2. **Screen.** New file in `mobile/src/screens/<Name>Screen.tsx`. Use TanStack Query (`useQuery`/`useMutation`/`useInfiniteQuery`); never `useEffect + fetch`. Style with `StyleSheet` + `mobile/src/theme.ts` tokens — no Tailwind.
3. **Navigation.** Register it in the right stack under `mobile/src/navigation/` (a tab stack, or the `MasNavigator` hub for secondary modules).
4. **Spanish + voseo.** All copy in Spanish, consistent with the bot.
5. **Verify.** `cd mobile && npx tsc --noEmit` (the only automated native guard — there is no native CI yet). Then on-device via Expo Go.
6. **Watch for body-field drift.** `tsc` can't see a wrong JSON key in an axios body (the B9 `{ids}` vs `{transaction_ids}` bug). Cross-check the request body against the backend schema by hand.

## B.6 "I want to add a new migration"

```bash
# 1. Copy the latest migration as a template (current head: 0022_envelopes.py)
cp migrations/versions/0022_envelopes.py \
   migrations/versions/0023_<your_change>.py

# 2. Edit revision/down_revision. Write upgrade()/downgrade() by hand.

# 3. Apply it
alembic upgrade head

# 4. Verify it can roll back cleanly
alembic downgrade -1 && alembic upgrade head
```

**Never run `alembic revision --autogenerate`.** Project policy. The head shifts every feature — don't hardcode a number into a script; read it with `alembic current` or `ls migrations/versions/ | tail -1`.

## B.7 "I want to change a Pydantic / LLM schema"

- **Backwards compat.** `ExtractionResult` is `extra`-guarded and validator-heavy. It's accreting per-intent flat fields (`goal_*`, `income_*`, `bill_*`, `debt_*`); a future cleanup may nest them, but for now add fields explicitly and guard them with a validator.
- **Schema is part of the LLM contract.** If the field is in `ExtractionResult` or a query-tool `input_schema`, update the system prompt and the fixture tests too. Re-record extractor fixtures — drift in assertions is a *signal*, investigate before relaxing them.

## B.8 "Tests are flaky — what do I check first?"

In order of likelihood:

1. **Cross-event-loop asyncpg connection.** The `tests/conftest.py` pattern creates a `NullPool` engine *per test* so connections never escape their loop. New test files must follow it.
2. **Redis state leaking between tests.** Flush the test DB index or scope keys to a unique test-run UUID.
3. **Tool-loop tests calling the real Anthropic API.** Use the `FixtureLLMClient` pattern (or `@pytest.mark.skipif(not ANTHROPIC_KEY)`).
4. **Mobile-API ↔ backend body drift.** `tsc` won't catch a wrong JSON key; only operator on-device testing or a hand cross-check does. No native CI yet (tech debt).

## B.9 "I want to run the system end-to-end locally"

```bash
# 1. Boot infra + API
docker compose up -d

# 2. Apply migrations (head should print 0022 or higher)
alembic upgrade head
alembic current

# 3. Register a user (returns shortcut_token ONCE — save it)
curl -X POST localhost:8000/api/v1/users/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@test.local","full_name":"You"}'

# 4. Run the gate + smokes
bash scripts/test_phase_6f.sh         # current end-to-end gate (mobile tsc + backend)
bash scripts/phase5b_smoke.sh         # bot pipeline against _simulate
bash docs/curl/phase-6a.sh            # query loop
```

If `phase5b_smoke.sh` passes against `_simulate`, the entire chat pipeline (minus a real Telegram connection) is healthy.

To run the native iOS app locally:

```bash
# 5. Set the LAN IP so the phone can reach the API
echo "EXPO_PUBLIC_API_BASE_URL=http://$(hostname -I | awk '{print $1}'):8000" > mobile/.env.local

# 6. Boot Expo dev server (separate terminal)
cd mobile && npx expo start

# 7. Scan the QR with Expo Go on iPhone — or press 'i' for iOS Simulator.
#    Get a 6-char login code by sending /login to the Telegram bot.
```

Full walkthrough (Expo Go sideload, `/login` device-code test) is in `docs/LOCAL_DEV.md §8`.

## B.10 "I want to change the LLM prompt"

- **Extractor prompt:** `api/services/llm_extractor/prompt.py`. Keep the `cache_control={"type":"ephemeral"}` blocks on the system + tool-schema sections.
- **Query prompt:** `app/queries/prompts/system.py`.
- **After any prompt change:** re-record the relevant fixtures (`tests/fixtures/`). Investigate assertion drift before relaxing it.
- **Cost watch.** Sonnet for queries, Haiku for extraction/vision/document. Don't swap to Opus in production code without checking `LLM_DAILY_TOKEN_BUDGET_PER_USER`.

## B.11 "How do I find where X is implemented?"

| Looking for | Search this |
|---|---|
| An endpoint by URL | `grep -rn '@router' api/routers/ \| grep -i <path-fragment>` |
| A Spanish error message | `grep -rn '<text>' bot/ api/` |
| A DB column | `grep -rn '<column_name>' api/models/ migrations/versions/` |
| A Redis key | `grep -rn '<prefix>' bot/redis_keys.py app/queries/history.py api/services/auth/` |
| A query tool the LLM calls | `app/queries/tools/__init__.py` — single registry |
| A conversational create flow | `api/services/telegram_dispatcher.py::_dispatch_create_*` + `bot/commit.py::_commit_*` |
| An `open_screen` handoff (native-only feature) | `grep -rn 'open_screen\|OpenScreenAction' api/services/ bot/ mobile/src/` |
| Envelope spend math | `api/services/envelopes.py::compute_envelope_summary` |
| FX / currency conversion | `api/services/fx.py::convert` |
| A migration that touched a table | `grep -l '<table>' migrations/versions/` |
| A native screen | `mobile/src/navigation/AppNavigator.tsx` + `mobile/src/screens/` |
| A native API call | `grep -rn '<endpoint-fragment>' mobile/src/api/` |
| A locked decision | `docs/phase-*-decisions.md` + `~/Finance_project/.../05_Decisions/` |
| Phase status / what's open | `CLAUDE.md` Phase tables + `~/Finance_project/30_Projects/Finance-Agent/00_Project-Brain.md` |

---

# PART C — Learning Path

> Resources are listed by **canonical title and author/creator** so you can find the current edition. Avoid pirated or out-of-date copies — these technologies move fast (especially the LLM tooling).

Each section has:
- **Why this matters here** — where in *this* codebase the topic shows up
- **Resources** — books and video creators worth your time, ordered beginner → pro

---

## C.1 Foundations: Modern Python (3.12)

**Why this matters here:** Type hints everywhere, `async/await` on every DB and HTTP call, Pydantic models, `match` statements, enums, dataclasses. If you can't read `async def get_user(db: AsyncSession) -> User | None:` instantly, start here.

### Books

1. **"Python Crash Course" — Eric Matthes** *(no Python at all? start here)*
2. **"Fluent Python" (2nd ed) — Luciano Ramalho** ⭐ *the* book for going from "I write Python" to "I understand Python." Iterators, decorators, async, and typing are directly applicable.
3. **"Robust Python" — Patrick Viafore** — type hints, protocols, structural patterns. Maps onto every `Mapped[...]`, `Annotated[...]`, and the `LLMClient` protocol in `llm_extractor/`.
4. **"Python Concurrency with asyncio" — Matthew Fowler** — once you've seen `async`, this is where you understand it.

### Video creators (search by name on YouTube)

- **mCoding (James Murphy)** — short, dense, correct. "Async fundamentals" and "type hints" playlists.
- **ArjanCodes** — design + clean code in modern Python. Beginner-friendly.
- **Real Python** (free articles, paid videos at realpython.com) — solid reference quality.

---

## C.2 Web APIs: FastAPI

**Why this matters here:** The entire HTTP layer. Every router under `api/routers/` is a FastAPI `APIRouter`. Dependencies (`Depends(get_db)`, `Depends(current_user)`) are the auth + persistence injection model.

### Official docs (treat as a textbook)

- **fastapi.tiangolo.com** — the official tutorial is exceptional. Read "Tutorial - User Guide" cover to cover, then "Advanced User Guide" (esp. dependencies, `UploadFile` for the multipart chat-image/PDF endpoints).

### Books

1. **"FastAPI" — Bill Lubanovic** (O'Reilly, 2024) — the only mature print book on the topic.
2. **"Building Python Web APIs with FastAPI" — Abdulazeez Abdulazeez Adeshina** — project-driven, accessible.

### Videos

- **ArjanCodes** — multiple FastAPI architecture videos.
- **Tiangolo's own talks** (search "Sebastián Ramírez FastAPI") — design rationale from the author.
- **TestDriven.io** — paid, but their FastAPI + async + Postgres course is the closest thing to "build this exact stack."

---

## C.3 Databases: PostgreSQL + SQL

**Why this matters here:** Postgres-only project. UUIDs, JSONB, partial indexes, CHECK constraints, FKs with `ON DELETE` semantics (the `envelope_id … ON DELETE SET NULL` unlink-not-delete is a deliberate design choice), composite indexes, materialized views. See `migrations/versions/0006_…` and `0017_…` for tours.

### Books

1. **"Learning SQL" (3rd ed) — Alan Beaulieu** *(ground floor)*
2. **"PostgreSQL: Up and Running" — Regina Obe & Leo Hsu** — Postgres-specific features. JSONB chapter is gold.
3. **"The Art of PostgreSQL" — Dimitri Fontaine** ⭐ Postgres as a *design tool*. Window functions, CTEs, `LATERAL`, advanced JSONB. Read this when you stop being scared of writing SQL by hand — `compute_envelope_summary`'s single grouped query is exactly this skill.
4. **"Database Internals" — Alex Petrov** *(pro level)* — how the engine actually works. Read once you've hit a real performance problem.

### Videos

- **Hussein Nasser** (YouTube) — pragmatic, excellent on indexes and replication.
- **PGCon / PostgresOpen recorded talks** — "Postgres Indexing Internals" by Bruce Momjian is a classic.

---

## C.4 ORMs and Migrations: SQLAlchemy 2.x + Alembic

**Why this matters here:** Every model in `api/models/`, every `select(…).where(…)` in services and routers. SQLAlchemy 2.x's `Mapped` / `mapped_column` typed style is what this project uses — old tutorials show the legacy `Column()` syntax, **skip those**.

### Documentation (primary source)

- **docs.sqlalchemy.org** — the "Unified Tutorial" for 2.0. Make sure the URL says `2.0/` or `latest/`.
- **alembic.sqlalchemy.org** — official Alembic tutorial (note: this project hand-writes every migration; never autogenerate).

### Books / longform

1. **"Essential SQLAlchemy" (2nd ed) — Jason Myers & Rick Copeland** — pre-2.0 syntax, but the conceptual model is unchanged.
2. **"Architecture Patterns with Python" — Harry Percival & Bob Gregory** ⭐ uses SQLAlchemy in a clean-architecture app. The repository + unit-of-work patterns illuminate why this codebase keeps services and routers thin.

### Videos

- **Mike Bayer's PyCon talks** (the SQLAlchemy maintainer) — search "Mike Bayer SQLAlchemy 2.0."

---

## C.5 Validation: Pydantic v2

**Why this matters here:** Every request/response in `api/schemas/` and the LLM contract in `ExtractionResult`. v2 is a near-rewrite of v1 — `model_validate`, `model_config`, `Field(...)`, `Annotated[...]`, validators, discriminated unions (the insights `InsightContent`). Old StackOverflow answers will mislead you.

### Resources

- **docs.pydantic.dev** — official migration guide v1→v2 is essential if you've used v1.
- **Tiangolo + Pydantic talks on YouTube** — short, focused.

No book yet matches v2 in depth — the docs are the primary source.

---

## C.6 Async Python and Concurrency

**Why this matters here:** Every IO operation is `async` — asyncpg, Redis, the Anthropic SDK calls, the query LLM loop. Mistakes here look like flaky tests (B.8) or 30-second hangs.

### Resources

1. **"Python Concurrency with asyncio" — Matthew Fowler** ⭐ best single resource.
2. **"Using Asyncio in Python" — Caleb Hattingh** — short, opinionated, excellent.
3. **mCoding YouTube** — "Asyncio is hard but really good" and similar.
4. **Łukasz Langa's PyCon keynotes** on asyncio internals.

---

## C.7 Caching, Queues, Sessions: Redis

**Why this matters here:** Every line in `bot/redis_keys.py` and `app/queries/history.py`. Device-codes, pending proposals, clarification state, account-creation flows, rate limits, query history all live in Redis. Redis is the source of truth for durable chat state.

### Books

1. **"Redis in Action" — Josiah Carlson** — older but the data-model chapters age well.
2. **"Redis: The Definitive Guide"** (O'Reilly) — newer reference.

### Videos

- **Hussein Nasser's Redis playlist.**
- **Redis University** (free at university.redis.com) — official, well-paced.

---

## C.8 LLMs and Tool-Use: Anthropic Claude API

**Why this matters here:** `api/services/llm_extractor/` (text + `vision.py` + `document.py`), `app/queries/llm_client.py`, the system prompts, the cache-control blocks, the tool-use loop. This is the *core differentiator* — and the discipline (LLM extracts/explains, never decides/calculates) is what makes the ledger trustworthy.

### Primary sources (required reading)

- **docs.anthropic.com** — read in this order:
  1. "Messages API" reference
  2. "Tool use" guide (the entire flow this project implements)
  3. "Prompt caching" guide (`cache_control={"type":"ephemeral"}` is how this survives token costs)
  4. "Vision" and "PDF support" — now *used*, not just background: the receipt + loan-contract paths.
- **Anthropic Cookbook** (github.com/anthropics/anthropic-cookbook) — runnable notebooks for tool-use, structured output, vision.
- **The `claude-api` skill** in this environment — for building/optimizing Claude API code and migrating between model versions.

### Books and longform

LLM-engineering books age in months. Treat any book older than ~12 months as stale on tool-use, durable on principles.

1. **"AI Engineering" — Chip Huyen** ⭐ production patterns. The cost/eval/observability chapters map directly onto `llm_extractions` and `llm_query_dispatches`.
2. **"Building LLM Apps" — Valentina Alto** — broad introduction.
3. **"Designing Machine Learning Systems" — Chip Huyen** *(adjacent, useful production-ML thinking)*.

### Videos

- **Anthropic's official YouTube channel** — short, frequent, matched to current API features.
- **Hamel Husain** (blog + talks) — practical eval and prompt engineering.
- **Jason Liu** (Instructor author) — structured output; exactly what `ExtractionResult` does.

---

## C.9 Telegram Bots: aiogram v3

**Why this matters here:** The `bot/` package — and remember it's *shared* with the native chat via `process_message()`. v3 is a rewrite from v2; old tutorials reference incompatible APIs.

### Primary sources

- **docs.aiogram.dev** — official docs. The "Migration FAQ" v2→v3 is critical context.
- **core.telegram.org/bots** — webhooks, inline keyboards, callback data, the 4096-char limit (`bot/delivery_send.py`).

### Videos

- aiogram has no flagship YouTube creator; trust the official docs first (community videos are often v2).

---

## C.10 Native App: Expo + React Native + TanStack Query (the frontend you actually maintain)

**Why this matters here:** The entire `mobile/` package — the *only* structured UI surface. Expo managed workflow means no Xcode build steps until you need a custom native module (not planned until P8/EAS). The data-layer skills (TanStack Query, React Hook Form, Zod, strict TypeScript) are the durable core; they carried over from the retired SPA and are where most of your UI-bug time will go.

### Primary sources

- **docs.expo.dev** — the canonical reference. "Get Started" → "Develop" → "Deploy." The "Managed workflow" path is what this project uses.
- **reactnative.dev** — core primitives (`View`, `Text`, `ScrollView`, `FlatList`, `Pressable`, `StyleSheet`). Expo wraps but does not hide these.
- **reactnavigation.org** — React Navigation 7. The project uses `bottom-tabs` + `native-stack` (NOT Expo Router — don't conflate them).
- **tanstack.com/query/v5** — the server-state model for every screen. The "Important Defaults" page is mandatory; `useInfiniteQuery` powers the cursor-paginated lists.
- **react-hook-form.com** + **zod.dev** — form state + validation (the debt/create forms).
- **docs.expo.dev/versions/latest/sdk/secure-store** — `expo-secure-store` holds the JWT; the API is async even for reads.

### Books

There are no mature Expo SDK 54–specific books — the docs are the textbook.

1. **"Learning React" (3rd ed) — Alex Banks & Eve Porcello** — hooks-first React; the mental model transfers to React Native.
2. **"React Native in Action" — Nader Dabit** — foundational RN concepts; some APIs are dated, focus on the mental-model chapters.
3. **"Effective TypeScript" — Dan Vanderkam** — strict null checks, discriminated unions, branded types. Chapters 4 and 6 are gold; the whole app is TS-strict.

### Videos

- **Simon Grimm (galaxies.dev)** — the best Expo-specific channel. Navigation, auth patterns, TanStack Query integration.
- **Jack Herrington** — React/TS patterns; his Suspense + data-fetching videos transfer.
- **Theo (t3.gg)** — RN tooling decisions (managed vs bare, EAS Build); opinionated and current.
- **Expo's official YouTube channel** — short feature walkthroughs.

### Things specific to this project's stack

- **`expo-secure-store`** replaces `localStorage`/cookies for the JWT. Always await it.
- **`expo-linking`** handles the `ledgercr://` deep link (registered in `app.json` under `scheme`).
- **`expo-web-browser`** drives the Gmail OAuth connect (poll-based callback).
- **`expo-image-picker`** (receipts) and **`expo-document-picker`** (loan PDFs) — read each permissions model before touching it.
- **Metro bundler** (not Vite). `tsconfig.json` extends Expo's base config; `npx tsc --noEmit` is the only automated guard (no native CI yet).
- **No Tailwind.** Styling is React Native `StyleSheet` + the tokens in `mobile/src/theme.ts`.

> **Note on the retired SPA.** Phase 6e shipped a Vite + React web SPA under `web/`; it was deleted at 6f B16 (2026-06-01). The React/TanStack/Zod/RHF knowledge fully transfers to the native app, but **do not study Tailwind, Vite, PWA/Workbox, or React Router for this project** — none ship anymore. `git log -- web/` is the only place that code lives now.

---

## C.11 Containers and Deployment

**Why this matters here:** `Dockerfile`, `Dockerfile.prod`, `docker-compose.yml`. Production target is Azure Container Apps; scheduled work runs as Container Apps Jobs (`workers/`).

### Resources

1. **"Docker Deep Dive" — Nigel Poulton** *(beginner)*
2. **"The Docker Book" — James Turnbull** *(reference)*
3. **NetworkChuck on YouTube** — Docker fundamentals, very approachable.
4. **Bret Fisher's Docker Mastery** (Udemy) — paid, comprehensive.

---

## C.12 Testing: Pytest + pytest-asyncio

**Why this matters here:** ~109 async test files. The NullPool-engine-per-test pattern in `conftest.py` is the *only* way the suite stays stable. `scripts/test_phase_6f.sh` is the gate.

### Resources

1. **"Python Testing with pytest" (2nd ed) — Brian Okken** ⭐ canonical.
2. **Brian Okken's "Test & Code" podcast** — short, applied.
3. **pytest-asyncio docs** — the `mode=auto` and event-loop scoping rules used here.

---

## C.13 Software Architecture (the glue, and the end goal)

**Why this matters here:** The "deterministic dispatcher + LLM extractor" split, the "data before AI" gating, the "one pipeline / two channels" reuse, the `open_screen` escape hatch, the "single migration per change" rule — these are *architectural decisions* that protect the core thesis. The forthcoming P7 pushback engine (deterministic affordability math + LLM phrasing) is the next big test of this discipline. Reading the books below helps you make analogous decisions without breaking the project's principles.

### Books

1. **"The Pragmatic Programmer" (20th anniv ed) — Hunt & Thomas** — universal. (Already integrated into the project vault.)
2. **"A Philosophy of Software Design" — John Ousterhout** ⭐ short, sharp. "Modules should be deep" is exactly the discipline the services layer tries to keep. (In the vault.)
3. **"Architecture Patterns with Python" — Percival & Gregory** — the single most relevant book for this stack.
4. **"Designing Data-Intensive Applications" — Martin Kleppmann** — durable multi-year reference; the lens for P8/P9 multi-tenancy + isolation. (In the vault.)
5. **"Clean Code" — Robert C. Martin** — naming/function discipline. (In the vault.)
6. **"Refactoring" (2nd ed) — Martin Fowler** *(JS edition, principles apply)*.

> The project vault (`~/Finance_project/30_Projects/Finance-Agent/`) already integrates source notes for several of these — read `04_Architecture.md` and the `05_Decisions/` notes alongside them.

---

## C.14 Recommended order if you're starting from scratch

If you can read Python loops and functions but everything in this repo looks like static, work in this order:

| Week(s) | Focus | Resource |
|---|---|---|
| 1–2 | Modern Python + types | "Fluent Python" ch. 1–8 + "Robust Python" |
| 2–3 | SQL fundamentals | "Learning SQL" + run queries against the local Postgres |
| 3–4 | FastAPI tutorial | tiangolo.com/tutorial — build their toy app from scratch |
| 4–5 | SQLAlchemy 2.0 | Official 2.0 Unified Tutorial + read 5 models in `api/models/` |
| 5–6 | Async Python | "Python Concurrency with asyncio" |
| 6 | Pydantic v2 | Official docs migration guide + read `ExtractionResult` |
| 7 | Pytest async | "Python Testing with pytest" + read `tests/conftest.py` |
| 7–8 | Anthropic API + tool use | docs.anthropic.com + Cookbook + read `llm_extractor/` and `app/queries/` |
| 8 | aiogram v3 + the shared pipeline | Official docs + read `bot/handlers.py`, `bot/pipeline.py`, `bot/commit.py` |
| 9 | Architecture | "Architecture Patterns with Python" + the vault `04_Architecture.md` |
| 9–10 | React + TanStack Query mental model | react.dev + TanStack Query docs |
| 10–11 | React Native + Expo | docs.expo.dev + Simon Grimm + read `mobile/src/screens/Login.tsx`, `mobile/src/navigation/`, one full screen+API pair |
| 11+ | Postgres mastery + the pushback engine | "The Art of PostgreSQL" + `EXPLAIN ANALYZE` on slow queries; study `CLAUDE.md` → "The Pushback Engine" for P7 |

After week 4 you can add a CRUD endpoint solo. After week 8 you can extend the chat (a query tool *or* a conversational-creation flow). After week 11 you can ship a native screen end-to-end (API helper → screen → navigation → on-device test). Beyond that, you're ready to take on the P7 pushback engine and P8 multi-tenancy — the two features that turn this from a personal tool into a product.

---

## C.15 What to skip (for now)

Dead ends for *this* project. Save them for later or never:

- **Django, Flask tutorials** — wrong framework; skills don't transfer to FastAPI's DI model.
- **SQLAlchemy 1.x material** — incompatible syntax. Verify everything targets 2.0+.
- **LangChain / LlamaIndex courses** — this project uses the raw Anthropic SDK deliberately; frameworks hide the cache-control discipline.
- **Vector databases / RAG / fine-tuning** — explicitly excluded by `CLAUDE.md` ("What NOT to Build").
- **Self-hosted LLM tutorials** — same reason; API LLM until ~10k+ DAU.
- **WhatsApp Baileys tutorials** — banned by Meta; Telegram now, official WhatsApp Cloud API only later.
- **The retired web stack — Vite, Tailwind, React Router, PWA/Workbox, Static Web Apps.** The SPA is deleted (6f B16). Learn the React *data layer* (TanStack Query, RHF, Zod, TS) for the native app, not the web shell.
- **Expo Router** — this project uses React Navigation, not file-based routing.
- **Expo EAS Build / TestFlight / Android** — not needed until P8. Dev distribution is Expo Go sideloaded onto the operator's iPhone; iOS-only for now.

---

## C.16 Two-hour weekly maintenance routine

Once you're up to speed, this keeps the project healthy:

1. `git log --since="1 week"` — review what changed.
2. `bash scripts/test_phase_6f.sh` — the gate (mobile `tsc --noEmit` + focused backend slices + regression). Use narrower `pytest -k` slices when you only touched one area.
3. `cd mobile && npx expo-doctor` — checks installed deps match the SDK 54 range. Run before upgrading any `mobile/` dependency. (Expo SDK bumps require the operator's iPhone Expo Go to update first.)
4. On-device sanity: capture an expense in the chat, assign it to a Sobre, check the Dashboard bar drained correctly. Several post-6f features (Native Gmail, Envelopes, the bulk B10–B14 commit) still list *operator on-device sign-off pending* in `CLAUDE.md` — that's the gap automated tests can't close.
5. Skim Anthropic's changelog (docs.anthropic.com → release notes) — model deprecations and new tool-use/vision features land regularly.
6. Skim aiogram + Expo SDK GitHub releases for breaking changes.
7. Review the **Technical Debt** section of `CLAUDE.md`. Pick one item if you have spare cycles — the BCCR FX API (replace the ₡500 placeholder) and the Gmail OAuth "Testing → Production" publish are the two with real user impact.

---

## Final note

The codebase rewards readers who follow the phase order. If something doesn't make sense, the answer is almost always in the migration that introduced the table, the corresponding `docs/phase-*-decisions.md`, or the phase section of `CLAUDE.md`. When in doubt, `git log -p <file>` tells you why.

The decisions docs (`docs/phase-Nx-decisions.md`) and the vault `05_Decisions/` notes are *load-bearing* — the canonical contract for everything in that phase, including what was explicitly chosen *not* to do (no web client, no LLM in the write path, no normalization maps, deterministic pushback). If you're about to "improve" something that feels weird, check the decisions doc first. The weirdness is usually protecting the core thesis: **if the ledger is wrong, the agent is useless.**
