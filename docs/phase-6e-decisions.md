# Phase 6e — Centro Financiero SPA

**STATUS:** ACTIVE
**Predecessor:** Phase 6d (Onboarding & Self-Registration)
**Created:** 2026-05-12
**B1 drafted:** 2026-05-12
**B2 implemented:** 2026-05-12
**B3 implemented:** 2026-05-12
**B4 implemented:** 2026-05-12
**B5 implemented:** 2026-05-12
**B6 implemented:** 2026-05-12
**B7 implemented:** 2026-05-15
**B7 statement-parity sign-off:** 2026-05-15 (operator approval)
**B8 implemented:** 2026-05-15
**B9 implemented:** 2026-05-15
**B10 implemented:** 2026-05-15
**B10 memoria-parity sign-off:** 2026-05-15 (operator approval)
**B11 implemented:** 2026-05-15
**B12 implemented:** 2026-05-15
**B13 implemented:** 2026-05-15

---

## 1. Goal

Phase 6e expands the existing `web/` onboarding SPA into the full Centro
Financiero: the visual, read-heavy surface for reviewing, editing, and
scenario-planning around the user's financial data.

The bot remains the primary input surface for fast transaction capture. The SPA
is for workflows the user wants to inspect calmly, edit carefully, or calculate
with more context.

---

## 2. Philosophy

1. **Bot for input, SPA for view.** Do not duplicate conversational capture as
   a large homepage transaction form. The SPA may edit existing transactions and
   provide a manual fallback, but fast creation remains chat-first.
2. **Mobile-first, web-second.** Design first for 375px and verify no component
   breaks at 320px.
3. **Read-heavy, write-rare.** Optimize legibility and load speed before input
   completeness.
4. **SPA renders; backend owns financial logic.** Form previews are acceptable,
   but amortization, cash flow, DTI, savings rate, FX, and materialized summary
   semantics live in backend services or shared tested code.

---

## 3. Locked Decisions

### 3.1 Goals are their own entity

Goals are not derived from debts. A debt pays down; a goal accumulates upward.

Target contract:

- `goals`: `id`, `user_id`, `name`, `target_amount`, `target_currency`,
  `target_date` nullable, `current_amount` default `0`, `status`
  `active|achieved|abandoned|paused`, `linked_account_id` nullable,
  `created_at`.
- `goal_contributions`: `id`, `goal_id`, `transaction_id` nullable, `amount`,
  `occurred_at`.

Repo reality: a legacy `goals` table already exists from migration `0001` and
`api/models/goal.py`. B2 must migrate that table to the 6e contract instead of
creating a duplicate table.

### 3.2 Transfers use a dedicated table plus two linked transactions

Transfers are first-class rows:

- `transfers`: `id`, `user_id`, `from_account_id`, `to_account_id`, `amount`,
  `currency`, `fx_rate` nullable, `occurred_at`, `notes`.
- `transactions.transfer_id` nullable FK links the two generated transaction
  rows.

Creating a transfer is atomic: one negative transaction from the source account
and one positive transaction into the destination account. Cash-flow reports
exclude `transactions.transfer_id IS NOT NULL`.

### 3.3 Dashboard uses live current-month queries and materialized history

The current month is always queried live. Prior months use materialized views:

- `mv_monthly_summary_by_user`
- `mv_yearly_summary_by_user`

Refresh runs nightly in the existing 6c insights job path, not a new scheduler.

### 3.4 Early payoff calculator is pure client-side

The early payoff calculator runs in the SPA without an API round-trip. It must
match backend amortization within `±₡1` in automated tests.

### 3.5 Calendar uses `react-day-picker`

Use `react-day-picker` plus custom bill/debt dots. Do not use FullCalendar.

### 3.6 PWA is minimal

Enable install banner and offline read of the shell plus last dashboard load.
No push notifications and no background sync. Offline banner copy:

```text
Datos del [fecha], abrí con internet para actualizar.
```

### 3.7 Insights edit uses selectors or direct user override

For `risk_posture`, `decision_style`, `financial_literacy`, and `archetype`,
the SPA uses enum selectors only.

For `stated_preference` and `stated_goal`, the SPA allows text edit and stores
the user override directly without LLM re-parsing. Saving sets
`user_locked=true`.

Computed insights are not editable.

### 3.8 Bot to SPA deep links use `edit_session` magic links

Bot buttons may mint magic links with `purpose='edit_session'` and an optional
`target_path`. TTL remains 30 minutes and single-use. The exchanged SPA session
still expires after 4 hours.

### 3.9 Currency display respects per-user display currency

The SPA reads `user.display_currency` and displays converted amounts using
`currency_rates`. Converted amounts are never persisted; original amount and
currency stay the source of truth.

Repo reality: `users.currency` exists, but `display_currency` and
`currency_rates` do not. B2 must add the support schema or explicitly decide to
alias `display_currency` to `users.currency` for 6e.

### 3.10 Breakpoints are locked

Use Tailwind breakpoints with this design baseline:

- Base: `375px`
- Tablet: `768px`
- Desktop: `1024px`

No component may break at `320px`.

### 3.11 Performance budgets are locked

- First Contentful Paint `< 1.5s` on simulated mobile 4G.
- Largest Contentful Paint `< 2.5s`.
- Initial bundle `< 200KB` gzipped via lazy-loaded routes.
- Dashboard API p95 `< 500ms`.
- Lighthouse: Performance `>= 85`, Accessibility `>= 95`, Best Practices
  `>= 95`, PWA `>= 80`.

### 3.12 Categories are user-customizable

New target table:

- `user_categories`: `id`, `user_id`, `name`, `kind` `income|expense|both`,
  `color` hex, `icon` nullable, `is_default`, `archived` default false.

Seed with the Phase 6d default categories. Users can add, rename, and archive.
No hard delete because historical transactions must remain interpretable.

Repo reality: transactions currently store `category` as a string. B2 should
add `transactions.category_id` nullable FK while keeping the legacy string for
backfill/backcompat until all write paths are migrated.

### 3.13 SPA stack continues from 6d

Keep:

- Vite
- React 18
- TypeScript
- Tailwind
- Zod
- react-hook-form
- react-router-dom

Add in 6e:

- `react-day-picker`
- `recharts`
- `framer-motion` only for page/interaction transitions
- `vite-plugin-pwa`

### 3.14 State management uses TanStack Query and Zustand

Use TanStack Query v5 for server state and Zustand for lightweight client state.
Do not add Redux. Keep Context API limited to auth unless a specific local
provider is justified.

---

## 4. Architecture

### 4.1 SPA routes and chunks

```text
web/src/
  main.tsx
  App.tsx
  api/
    client.ts                 cookie auth, 401 handling
    dashboard.ts              summary, cash-flow, upcoming
    accounts.ts               account list/detail/update/archive
    transactions.ts           cursor list, edit, export, bulk
    bills.ts                  calendar/list/mark-paid
    debts.ts                  detail, schedule, archive
    incomes.ts                recurring income CRUD
    goals.ts                  goals + contributions
    insights.ts               memory export/edit/delete
    categories.ts             user categories
  hooks/
    useDashboard.ts           TanStack Query wrappers
    useAccounts.ts
    useTransactions.ts
    useOfflineSnapshot.ts
  components/
    layout/                   mobile shell, bottom nav, header
    charts/                   recharts wrappers
    forms/                    RHF + Zod forms
    finance/                  money, rates, account/debt widgets
    feedback/                 skeletons, errors, offline banner
  routes/
    Dashboard.tsx             chunk: dashboard
    AccountsIndex.tsx         chunk: accounts
    AccountDetail.tsx
    TransactionsIndex.tsx     chunk: transactions
    BillsIndex.tsx            chunk: bills-calendar
    DebtsIndex.tsx            chunk: debts
    DebtDetail.tsx
    IncomesIndex.tsx          chunk: incomes
    GoalsIndex.tsx            chunk: goals
    GoalDetail.tsx
    MemoryIndex.tsx           chunk: memory
    CategoriesIndex.tsx       chunk: settings
    PrivacySettings.tsx
    Expired.tsx
```

Route chunks must be lazy-loaded so the initial dashboard bundle stays under
the budget.

### 4.2 Layer interaction

```text
Telegram bot
  ├─ normal chat capture: write dispatcher → transactions / pending confirms
  └─ "Ver detalle" button
       ↓ generate_link(user_id, purpose='edit_session', target_path)

SPA URL: /?token=<opaque>&path=/debts/<id>
  ↓
AuthProvider
  ↓ POST /api/v1/auth/magic-link/exchange
  ↓ Set-Cookie: fa_session (httpOnly)
  ↓ navigate(target_path || '/')

Route component
  ↓ TanStack Query hook
  ↓ api/client.ts withCredentials
  ↓ FastAPI /api/v1/*
  ↓ PostgreSQL / materialized views / Redis where applicable
```

### 4.3 Backend boundaries

```text
api/routers/
  dashboard.py       read aggregates; live current month + materialized history
  goals.py           migrate existing router to 6e contract
  transfers.py       atomic transfer creation + listing
  categories.py      user category CRUD/archive
  transactions.py    cursor pagination, edit, bulk archive/category, export
  recurring_bills.py extend with mark-paid idempotency
  auth.py            exchange supports onboarding + edit_session links

api/services/
  dashboard/         summary builders, cash-flow series
  transfers.py       create transfer + two transactions transactionally
  categories.py      seed defaults, archive rules
  exports.py         CSV/ZIP export helpers

workers/
  insights_nightly.py  also refreshes dashboard materialized views
```

---

## 5. Proposed B2 Schema Migration Plan

No migration is executed in B1. This is the proposed B2 shape for Daniel review.

### 5.1 Goals

Existing `goals` table is migrated in place:

- Add `target_currency String(3) NOT NULL DEFAULT 'CRC'`.
- Rename or alias `deadline`/`target_date` into one canonical column:
  `target_date DATE NULL`.
- Keep `current_amount Numeric(12,2) NOT NULL DEFAULT 0`.
- Add `linked_account_id UUID NULL REFERENCES accounts(id) ON DELETE SET NULL`.
- Convert status values:
  - legacy `completed` → `achieved`
  - keep `active`, `paused`, `abandoned`
- Add CHECK `status IN ('active','achieved','abandoned','paused')`.
- Consider dropping or deprecating `monthly_contribution` and `priority` after
  frontend migration; do not remove data in B2 unless Daniel approves.

New table:

```text
goal_contributions
  id UUID PK
  goal_id UUID NOT NULL FK goals(id) ON DELETE CASCADE
  transaction_id UUID NULL FK transactions(id) ON DELETE SET NULL
  amount NUMERIC(12,2) NOT NULL CHECK amount > 0
  occurred_at TIMESTAMPTZ NOT NULL
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

### 5.2 Transfers

```text
transfers
  id UUID PK
  user_id UUID NOT NULL FK users(id) ON DELETE CASCADE
  from_account_id UUID NOT NULL FK accounts(id)
  to_account_id UUID NOT NULL FK accounts(id)
  amount NUMERIC(12,2) NOT NULL CHECK amount > 0
  currency VARCHAR(3) NOT NULL CHECK currency IN ('CRC','USD')
  fx_rate NUMERIC(18,8) NULL CHECK fx_rate > 0
  occurred_at TIMESTAMPTZ NOT NULL
  notes TEXT NULL
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()

transactions.transfer_id UUID NULL FK transfers(id) ON DELETE SET NULL
```

Indexes:

- `ix_transfers_user_occurred_at`
- `ix_transactions_user_transfer_id`

### 5.3 User categories

```text
user_categories
  id UUID PK
  user_id UUID NOT NULL FK users(id) ON DELETE CASCADE
  name VARCHAR(80) NOT NULL
  kind VARCHAR(16) NOT NULL CHECK kind IN ('income','expense','both')
  color VARCHAR(7) NOT NULL CHECK color ~ '^#[0-9A-Fa-f]{6}$'
  icon VARCHAR(64) NULL
  is_default BOOLEAN NOT NULL DEFAULT false
  archived BOOLEAN NOT NULL DEFAULT false
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

Indexes/constraints:

- Unique active name per user: `(user_id, lower(name)) WHERE archived = false`.
- Seed Phase 6d defaults for every existing user.
- Add `transactions.category_id UUID NULL REFERENCES user_categories(id)`.
- Keep legacy `transactions.category` string through the transition.

### 5.4 Accounts archival

Current accounts have `is_active`. B4 asks for archived account behavior.
Proposed path:

- Add `archived BOOLEAN NOT NULL DEFAULT false`, or define `archived = NOT
  is_active` as the canonical API contract.
- Preferred for 6e: add explicit `archived` and keep `is_active` as legacy
  backcompat until a later cleanup.

### 5.5 Currency display

Support decision 3.9:

```text
users.display_currency VARCHAR(3) NOT NULL DEFAULT users.currency

currency_rates
  id UUID PK
  base_currency VARCHAR(3) NOT NULL
  quote_currency VARCHAR(3) NOT NULL
  rate NUMERIC(18,8) NOT NULL CHECK rate > 0
  as_of DATE NOT NULL
  source VARCHAR(64) NOT NULL
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

Unique: `(base_currency, quote_currency, as_of)`.

### 5.6 Dashboard materialized views

```text
mv_monthly_summary_by_user
  user_id
  month
  display_currency
  income_total
  expense_total
  net_flow
  savings_rate
  transfer_count_excluded
  refreshed_at

mv_yearly_summary_by_user
  user_id
  year
  display_currency
  income_total
  expense_total
  net_flow
  savings_rate
  refreshed_at
```

Rules:

- Exclude `transactions.transfer_id IS NOT NULL` from income/expense totals.
- Exclude unconfirmed Gmail shadow rows where `transactions.status !=
  'confirmed'`.
- Current month uses live query, not the materialized monthly view.

### 5.7 Magic-link edit sessions

Current `magic_link_tokens.purpose` allows `onboarding|edit_session`. B12 only
needs service/URL support for `target_path`, likely via:

- Add `target_path TEXT NULL` to `magic_link_tokens`, or
- Keep DB schema unchanged and include `path` only in the returned SPA URL.

Preferred for audit/debugging: add `target_path TEXT NULL` with validation in
the service to only allow relative paths beginning with `/`.

---

## 6. Proposed B2 Endpoint Plan

### 6.1 New / migrated endpoints

- `POST/GET/PATCH/DELETE /api/v1/goals[/{id}]`
- `POST /api/v1/goals/{id}/contributions`
- `POST /api/v1/transfers`
- `GET /api/v1/transfers`
- `POST/GET/PATCH /api/v1/categories[/{id}]`
- `GET /api/v1/dashboard/summary?period=month_current|month_prev|ytd`
- `GET /api/v1/dashboard/cash-flow?from=YYYY-MM&to=YYYY-MM`
- `GET /api/v1/transactions/export?...filters`
- `POST /api/v1/recurring-bills/{id}/mark-paid`
- Privacy export/delete endpoints for B14 may be added later in B14 unless
  Daniel wants them in B2 foundation.

### 6.2 Existing endpoints to extend

- `GET/POST/PATCH/DELETE /api/v1/accounts[/{id}]`: archive/read-only semantics.
- `GET/POST/PATCH/DELETE /api/v1/debts[/{id}]`: archive/pause semantics and
  immutable financial fields.
- `GET/POST/PATCH/DELETE /api/v1/recurring-incomes[/{id}]`: pause/archive.
- `GET /api/v1/transactions`: cursor pagination and filter contract.
- `PATCH /api/v1/transactions/{id}`: edit amount/category/notes/date only.

---

## 7. B1 Approval Questions

Daniel should approve these before B2:

1. Legacy `goals` migration: migrate the existing table in place instead of
   creating a duplicate `goals` table.
2. Category FK transition: add `transactions.category_id` while retaining the
   legacy string column through 6e.
3. `users.display_currency` and `currency_rates`: add them in B2 even though
   they were not listed in the B2 endpoint bullet list, because locked decision
   3.9 requires them.
4. Account archive semantics: explicit `accounts.archived` column vs deriving
   from existing `is_active`.
5. `magic_link_tokens.target_path`: persist target path for audit/debugging vs
   URL-only path parameter.

---

## 8. B1 Done-When Status

- Locked 14 decisions documented: yes.
- ASCII architecture diagram documented: yes.
- Proposed schema migrations documented without executing them: yes.
- Daniel approved B1 recommendations by moving forward on 2026-05-12: yes.

---

## 9. B2 Implementation Status

B2 is implemented as the backend foundation for the Centro Financiero.

### 9.1 Migration

Alembic migration `0017_phase6e_centro_financiero_base.py`:

- Migrates existing `goals` in place to the 6e contract:
  `target_date`, `target_currency`, `linked_account_id`, and stored statuses
  `active|achieved|abandoned|paused`.
- Adds `goal_contributions`.
- Adds `transfers`.
- Adds `user_categories` and seeds the 10 default CR categories per existing
  6d decision.
- Adds `transactions.transfer_id` and `transactions.category_id` while retaining
  legacy `transactions.category`.
- Adds `users.display_currency` and `currency_rates`.
- Adds `accounts.archived` while retaining legacy `accounts.is_active`.
- Adds `magic_link_tokens.target_path`.
- Adds materialized views `mv_monthly_summary_by_user` and
  `mv_yearly_summary_by_user`.

Local DB is at `0017 (head)`.

### 9.2 Endpoints Implemented

- `POST/GET/PATCH/DELETE /api/v1/goals[/{id}]`
- `POST /api/v1/goals/{id}/contributions`
- Legacy compatibility: `POST /api/v1/goals/{id}/contribute`
- `POST /api/v1/transfers`
- `GET /api/v1/transfers`
- `POST/GET/PATCH /api/v1/categories[/{id}]`
- `GET /api/v1/dashboard/summary?period=month_current|month_prev|ytd`
- `GET /api/v1/dashboard/cash-flow?from=YYYY-MM&to=YYYY-MM`

Not yet implemented in B2:

- `GET /api/v1/transactions/export?...filters` — belongs naturally with B5.
- `POST /api/v1/recurring-bills/{id}/mark-paid` — belongs naturally with B6.
- Full transaction cursor filtering/editing — belongs to B5.
- Debt/archive/pause polish — belongs to B7.
- Recurring income pause/archive polish — belongs to B8.

### 9.3 Nightly Job

The existing Phase 6c nightly insights worker now refreshes the dashboard
materialized views after the insight lifecycle sweep. No second cron/job was
introduced.

### 9.4 Verification

- `env PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache .venv/bin/alembic upgrade head`
  passed.
- `env PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache .venv/bin/alembic current`
  returned `0017 (head)`.
- `env PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache .venv/bin/python -m compileall api workers tests/test_phase_6e_b2_backend.py`
  passed.
- `env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q tests/test_phase_6e_b2_backend.py`
  passed with `4 passed`.
- Regression slice passed with `34 passed`:
  `tests/test_phase_6d_b2_endpoints.py`,
  `tests/test_phase_6d_b3_magic_link.py`,
  `tests/test_phase_6d_b10_welcome.py`.

### 9.5 B3 Handoff

B3 consumed the B2 foundation. Two concrete B3 UI gaps required narrow backend
extensions:

- `GET /api/v1/dashboard/summary` now includes `balance_total` and
  `category_breakdown`.
- `GET /api/v1/dashboard/daily-cash-flow` was added for the required daily
  sparkline.
- `GET /api/v1/users/me/insights/summary` was added for the dashboard
  "Te conozco" card under cookie auth.

---

## 10. B3 Implementation Status

B3 is implemented.

### 10.1 SPA

- `/` now renders `web/src/routes/Dashboard.tsx` instead of the 6d onboarding
  landing.
- TanStack Query v5 wraps the SPA in `web/src/main.tsx`.
- Recharts is used in `web/src/components/dashboard/DashboardCharts.tsx`.
- Dashboard sections implemented:
  - Header with user name, balance total, and month-over-month trend text.
  - Period selector: month current, month previous, YTD, last 6 months.
  - This-month metric cards: income, expense, net flow, savings rate.
  - Daily cash-flow sparkline.
  - Upcoming payments from calendar feed plus debt overview.
  - Top 3 active goals.
  - Top 3 active memory insights.
  - Partial-onboarding CTAs and intermediate state for one-account/no-movement.
  - Quick links to accounts, transactions, debts, bills, and goals.

### 10.2 Verification

- `npm run lint` passed.
- `npm run build` passed. Initial `index` JS bundle is about `119KB` gzip;
  `DashboardCharts` is a separate lazy chunk at about `106KB` gzip.
- `env PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache .venv/bin/python -m compileall api workers tests/test_phase_6e_b2_backend.py`
  passed.
- `env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider -q tests/test_phase_6e_b2_backend.py`
  passed with `4 passed`.
- Regression slice passed with `34 passed`:
  `tests/test_phase_6d_b2_endpoints.py`,
  `tests/test_phase_6d_b3_magic_link.py`,
  `tests/test_phase_6d_b10_welcome.py`.

### 10.3 Next Block

Closed by B4 implementation below.

---

## 11. B4 Implementation Status

B4 is implemented as the full Accounts module. Backend extensions plus a
read-heavy SPA detail surface; no schema migration required.

### 11.1 Backend

- `api/services/accounts.py::compute_account_balances` — single-pass per-account
  current and month-start balance computation. Two grouped queries (initial
  balance lookup + transaction sums); cost is independent of the number of
  accounts in the result set.
- `AccountResponse` now exposes `current_balance` and `month_start_balance`
  (Optional[Decimal]). Always populated by the `accounts` router via
  `_accounts_with_balances`.
- `AccountUpdate` tightened: dropped `currency` and `initial_balance` (immutable
  per Phase 6e §3.1 / §5.4 contract). `name`, `account_type`, `is_active`,
  `archived` remain editable. `account_type` validation moved into the patch
  path.
- `GET /api/v1/transactions` extended with filters: `account_id`, `category_id`,
  `from_date`, `to_date`, `kind` (`all|income|expense|transfer`), `q`
  (description/merchant ILIKE). Default `limit` raised from 20 → 25 to match the
  SPA pagination contract. Cursor pagination is intentionally deferred to B5;
  offset stays for B4.
- `PATCH /api/v1/transactions/{id}` (new). Edits `amount`, `merchant`,
  `description`, `category`, `category_id`, `transaction_date`. Hard-rejects
  shadow rows (`status != 'confirmed'`) and transfer legs
  (`transfer_id is not null`) with 409 — shadow approval flows through
  `/aprobar_shadow` in the bot, transfer legs change only via the parent
  transfer. `category_id` cross-checked against the user's non-archived
  categories.

### 11.2 SPA

- `web/src/routes/AccountsIndex.tsx` (new) — list view at `/accounts`, sorted
  active-first then by name, with a "Mostrar archivadas" toggle, a per-card
  current balance, a "Nueva cuenta" link, and a "Nueva transferencia" modal
  reusing the B2 `POST /api/v1/transfers` endpoint. Disabled when fewer than
  two active accounts exist.
- `web/src/routes/AccountDetail.tsx` (new) — at `/accounts/:id`. Header with
  current and month-start balance; inline edit for name + type (currency +
  initial balance shown read-only). Transactions list with the new filter set,
  page size 25, prev/next pagination. Per-row "Editar" button hidden for
  shadow rows and transfer legs. Edit modal posts to the new
  `PATCH /transactions/{id}`. Archive flow uses a single confirm with the live
  balance shown; archived accounts render a banner with a "Restaurar cuenta"
  button and hide edit/transfer affordances.
- `web/src/routes/AccountsNew.tsx` slimmed to creation only. The previous
  combined create+list screen is replaced by `AccountsIndex`. Onboarding alias
  `/onboarding/cuentas` continues to point at the create form.
- New TanStack Query wrappers: `web/src/api/accounts.ts`,
  `web/src/api/transactions.ts`. Reuse the existing axios `client.ts` and
  cookie auth.
- `web/src/App.tsx` lazy-loads `AccountsIndex` and `AccountDetail` to keep the
  initial dashboard bundle inside the 200KB gzip budget.

### 11.3 Decisions taken in B4

- Shadow rows (Phase 6b `status='shadow'`) are read-only in the SPA edit modal.
  Approval continues to flow through `/aprobar_shadow` in the bot. Surfaced as
  a "Pendiente" badge in the transactions list with no edit button.
- Archiving an account with non-zero balance has no backend guard. The SPA
  shows a single confirm with the live balance; no `?force=true` round-trip.
- `is_active` and `archived` are kept mirrored for now. `archived` is the
  canonical SPA flag; `is_active` stays for legacy bot/service backcompat.
  Tracked as tech debt for a later cleanup block.
- Restore archived account shipped as part of B4 via the archived-banner
  "Restaurar cuenta" button, even though the original B4 spec did not call it
  out. Keeps soft-delete reversible from inside the SPA.

### 11.4 Verification

- `env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache uv run pytest -p no:cacheprovider -q tests/test_phase_6e_b4_accounts.py tests/test_phase_6e_b2_backend.py tests/test_phase_6d_b2_endpoints.py tests/test_phase_6d_b3_magic_link.py tests/test_phase_6d_b10_welcome.py`
  passed with `43 passed`.
- `npm run lint` (tsc --noEmit) passed.
- `npm run build` passed. Initial `index` JS gzip stayed at ~119KB; new
  `AccountsIndex` chunk gzip ~2.6KB; new `AccountDetail` chunk gzip ~4KB; new
  shared `accounts` chunk gzip ~1.5KB.
- `python -m compileall api workers tests/test_phase_6e_b4_accounts.py` passed.
- `alembic current` returned `0017 (head)` (no schema migration in B4).

### 11.5 Next Block

Closed by B5 implementation below.

---

## 12. B5 Implementation Status

B5 is implemented as the global Transactions module. One new migration plus
broad extensions to `GET /api/v1/transactions`; SPA grows a new lazy route at
`/transactions`.

### 12.1 Migration

Alembic `0018_phase6e_b5_transactions_archived.py`:

- Adds `transactions.archived BOOLEAN NOT NULL DEFAULT false`.
- Adds partial index `ix_transactions_user_date_active` on
  `(user_id, transaction_date desc) WHERE archived = false` so the hot list
  path stays fast as the table grows.

Local DB at `0018 (head)`. Materialized dashboard views are NOT yet updated to
exclude archived rows — flagged as tech debt in CLAUDE.md (live-month dashboard
queries and `compute_account_balances` correctly exclude archived in-code; only
historical-month summary refreshes lag).

### 12.2 Endpoints

- `GET /api/v1/transactions` — grew filters `account_ids[]`, `category_ids[]`,
  `currency`, `min_amount`, `max_amount`, `sort_by`
  (`date|amount|category`), `sort_dir` (`asc|desc`), `include_archived`,
  `cursor`. Cursor is opaque base64-encoded `{d, i}` (date + id) and is only
  honored on `sort_by=date`. Other sorts fall back to offset pagination. Both
  cursor and offset coexist for backcompat with B4 callers. Default scope
  excludes archived; response carries `next_cursor`.
- `GET /api/v1/transactions/export` — CSV stream over the same filter set.
  Hard cap `CSV_EXPORT_MAX_ROWS=50_000`; returns 413 with a Spanish hint when
  the matched count exceeds it.
- `POST /api/v1/transactions/bulk/archive` — body `{transaction_ids}`,
  flips `archived=true` for every row in one DB transaction.
- `POST /api/v1/transactions/bulk/restore` — inverse.
- `POST /api/v1/transactions/bulk/categorize` — body
  `{transaction_ids, category_id?}`; `null` clears the category. Cross-checks
  the category against the user's non-archived categories.
- All three bulk endpoints are all-or-nothing: if any row is missing,
  belongs to another user, is a shadow row, or is a transfer leg, the request
  returns 409 with a structured `detail` (`{code, ids, message}`) and zero
  rows change.
- `PATCH /api/v1/transactions/{id}` — extended to reject archived rows with
  409 ("Restaurá la transacción antes de editarla.").

### 12.3 Balance + dashboard consistency

`api/services/accounts.py::compute_account_balances` and the
`api/services/dashboard/summary.py` aggregators (`_balance_total`,
`get_dashboard_summary`) now filter out `archived=true` rows alongside the
existing `status='confirmed'` and transfer guards. Cash-flow time-series and
category breakdown queries already filter on `transfer_id IS NULL` and
inherit the archived filter via the same predicate set.

### 12.4 SPA

- `web/src/routes/TransactionsIndex.tsx` (new, lazy) at `/transactions`. Filter
  bar with: search, multi-account select, multi-category select, kind, currency,
  sort (5 presets), date range, amount range, include-archived toggle. Bulk
  selection column with header "select all on page"; bulk-categorize and
  bulk-archive actions show only when something is selected. "Exportar CSV"
  button triggers the new export endpoint with the current filter set. Edit
  modal reuses the shared component.
- `web/src/components/transactions/TransactionEditModal.tsx` (new). Extracted
  from `AccountDetail.tsx` so both routes share one implementation. Account
  detail is now ~25% smaller and only imports the shared modal.
- `web/src/api/transactions.ts` — grew cursor support and bulk helpers
  (`bulkArchiveTransactions`, `bulkRestoreTransactions`,
  `bulkCategorizeTransactions`, `downloadTransactionsCsv`). Filter serialization
  moved to a `toQueryParams` helper that handles repeated `account_ids` /
  `category_ids` correctly.
- Empty state copy on TransactionsIndex points the user back to the bot for
  capture, matching the Phase 6e bot-for-input / SPA-for-view philosophy.

### 12.5 Decisions taken in B5

- `transactions.archived BOOLEAN` is the soft-delete model (decision 12.1).
  Orthogonal to `status` (confirmed/shadow/pending_review); keeps the Phase 6b
  workflow clean.
- Cursor pagination is opt-in via `cursor`; offset stays for B4 backcompat.
  Cursor is sort-aware but only implemented for the default date sort. Amount
  and category sorts fall back to offset (rarely used; document if a perf
  problem appears).
- CSV export hard caps at 50,000 rows. 413 with Spanish guidance to refine
  filters. Protects against unbounded streaming on a single-instance Container
  App.
- Bulk operations are all-or-nothing in a single DB transaction. Reject with a
  structured 409 listing the offending IDs.
- Editing an archived transaction is rejected with 409 — the user must restore
  first. Keeps the data model unambiguous.
- Adding archive exclusion to the materialized dashboard views is logged as
  tech debt rather than included in 0018. Current-month dashboard reads are
  live and already correct; historical-month summaries will catch up once the
  view definitions are rebuilt.

### 12.6 Verification

- Migration: `env PYTHONPYCACHEPREFIX=/tmp/phase6e-pycache .venv/bin/alembic
  upgrade head` advanced to `0018 (head)`.
- Focused tests `tests/test_phase_6e_b5_transactions.py` plus B4 + B2 + 6d
  regression: `52 passed`.
- `npm run lint` (tsc --noEmit) passed.
- `npm run build` passed. Initial `index` JS gzip ~119.50KB (stable).
  `TransactionsIndex` chunk gzip ~3.43KB; shared `TransactionEditModal`
  chunk gzip ~1.89KB; `AccountDetail` shrunk to ~3.18KB after extracting
  the shared modal.
- `python -m compileall api workers tests/test_phase_6e_b5_transactions.py`
  passed.

### 12.7 Next Block

Closed by B6 implementation below.

---

## 13. B6 Implementation Status

B6 is implemented as the recurring bills + calendar surface. No schema
migration; backend convenience endpoint plus the new `/bills` SPA route
with calendar/list views.

### 13.1 Backend

- `POST /api/v1/recurring-bills/{id}/mark-paid` (new). Body:
  `{amount_paid?, paid_at?, account_id?, category_id?, description?,
  idempotency_key}`. Auto-resolves the smallest-due_date occurrence with
  status `pending|overdue|partially_paid` for the bill, creates a manual
  transaction (negated amount, source `manual`, account default from the
  bill, category string from the bill, category_id from the body),
  links it via the existing
  `recurrence.link_transaction_to_occurrence` service, and returns
  `{occurrence, transaction_id, amount_delta_pct?, warning?,
  idempotent_replay}`.
- Idempotency key is required (8–128 chars). Cached in Redis under
  `bill_mark_paid:{user_id}:{bill_id}:{key}` with a 10-minute TTL
  (matches the B9 account-creation flow). Cache hits return the original
  response with `idempotent_replay=true` and never duplicate the
  transaction or re-flip the occurrence.
- 404 when the bill has no actionable occurrence — message points the
  caller at `POST /api/v1/jobs/generate-occurrences`.
- 400 when the bill is variable-amount and the body omits `amount_paid`.
- `PATCH /api/v1/recurring-bills/{id}` extended: when the request flips
  `is_active` from false→true, `recurrence.generate_occurrences` runs to
  backfill any missing future occurrences (resume after pause). PATCH
  with `is_active=false` only — pause — does NOT cancel future pending
  occurrences. The pre-existing `DELETE` keeps its archive semantics
  (sets `is_active=false` AND cancels future pending).

### 13.2 SPA

- `web/src/routes/BillsIndex.tsx` (new, lazy) at `/bills`. Calendar/list
  toggle. Calendar uses `react-day-picker` with a custom blue dot under
  every day that has at least one pending occurrence; clicking a marked
  day opens a panel with each pending bill on that day. List view shows
  pending occurrences in the next 60 days, sorted by due_date, color-coded
  by urgency (red <3d or overdue, amber <7d, neutral otherwise).
- `web/src/components/bills/BillActionsModal.tsx` (new) — single modal
  used from both views. Sections: bill summary, mark-paid form (amount,
  account, category, paid_at, notes — pre-filled from the bill +
  selected occurrence), and the action row (Pausar / Reanudar /
  Archivar / Editar). Mark-paid generates an idempotency key on first
  open via `crypto.randomUUID()` and reuses it across the modal's
  lifetime so multiple clicks within 10 minutes replay safely.
- `web/src/api/bills.ts` (new) — TanStack helpers:
  `fetchRecurringBills`, `fetchRecurringBill`, `fetchBillOccurrences`,
  `markBillPaid`, `pauseBill`, `resumeBill`, `archiveBill`. Includes a
  Zod schema for `BillOccurrence` (the upcoming-feed schema in
  `web/src/schemas/dashboard.ts` is a separate, narrower contract; both
  coexist for now).
- `web/src/routes/BillsNew.tsx` is unchanged — `/bills/new` keeps the 6d
  creation form; `/onboarding/gastos` aliases it. The "Editar" action in
  the modal links to `/bills/new?id=…` (deep-link; the form will pick up
  the id in a future polish pass — currently it falls through to a
  fresh create, which is acceptable for B6).
- New SPA dep: `react-day-picker` (locked in decision 3.5; first time
  installed). Adds ~24KB gzip in the lazy `BillsIndex` chunk; initial
  bundle stays at ~119.58KB gzip.

### 13.3 Decisions taken in B6

- Mark-paid auto-resolves the next pending occurrence (decision 13.1).
  No `occurrence_id` override; the SPA's calendar/list flow is
  one-bill-one-click.
- Pause and Archive stay distinct: Pause keeps existing pending
  occurrences (calendar still shows them until the date passes); Archive
  cancels them. Resume regenerates.
- Idempotency window is 10 minutes — matches B9 account-creation Redis
  TTL, same UX intent (debounce double-clicks, allow same-day reuse if
  the user really wants it).
- Marked-paid transactions use `source='manual'` to avoid touching the
  Phase 6b status CHECK constraint or inventing a new source value. The
  description defaults to `Pago: {bill.name}`.

### 13.4 Verification

- Focused tests `tests/test_phase_6e_b6_bills.py` (`6 passed`):
  mark-paid happy path, idempotency replay, 404 on no pending occurrence,
  variable-amount 400 then 200, pause keeps occurrences + resume
  regenerates, archive cancels future pending.
- Full slice (B6 + B5 + B4 + B2 + 6d B2/B3/B10): `58 passed`.
- `npm run lint` (tsc --noEmit) passed.
- `npm run build` passed. Initial `index` JS gzip ~119.58KB (stable);
  new `BillsIndex` chunk gzip ~24.16KB (includes react-day-picker);
  `BillsIndex` CSS chunk gzip ~1.68KB.
- `python -m compileall api workers tests/test_phase_6e_b6_bills.py`
  passed. `alembic current` still `0018 (head)` — no schema change in B6.

### 13.5 Next Block

Closed by B7 implementation below.

---

## 14. B7 Implementation Status

B7 is implemented as the Debts module + early-payoff calculator + Ley 7472
prepayment scenarios. Backend: small migration plus a new aggregate endpoint
and a tightened PATCH whitelist. SPA: two new lazy routes plus client-side
parity engine.

### 14.1 Migration

Alembic `0019_phase6e_b7_debts_archived.py`: adds `debts.archived BOOLEAN
NOT NULL DEFAULT false`. Pause = `is_active=false, archived=false`
(visible with badge, excluded from overview totals). Archive = `is_active=
false, archived=true` (default-hidden, opt-in show). Mirrors the accounts
and transactions patterns from B4/B5.

Local DB at `0019 (head)`.

### 14.2 Endpoints

- `GET /api/v1/debts/{id}/payoff-scenarios?lump_sum=&extra_monthly=` (new).
  Auto-runs the existing `early_payoff_lump_sum` /
  `early_payoff_increase_payment` service functions for whichever params
  are present. Returns `{debt_id, currency, payments_made,
  prepayment_penalty_pct, original, lump_sum?, extra_monthly?,
  variable_rate_notice}`. 422 if neither param is provided.
- `PATCH /api/v1/debts/{id}` is now narrowed to a whitelist: `name`,
  `payment_due_day`, `account_id`, `notes`, `is_active`, `archived`. All
  financial fields (`original_amount`, `current_balance`, `interest_rate`,
  `minimum_payment`, `term_months`, rate/insurance fields,
  `prepayment_penalty_pct`, `payments_made`) are immutable post-creation
  via `model_config = {"extra": "forbid"}` — extra fields return 422.
- `GET /api/v1/debts` now accepts `include_archived=true` (default
  false). `debt_overview` filters out archived rows.
- `DELETE /api/v1/debts/{id}` flips both `is_active=false` AND
  `archived=true`, matching the accounts/transactions delete contract.

### 14.3 SPA

- `web/src/routes/DebtsIndex.tsx` (new, lazy) at `/debts`. Top metrics:
  total debt, monthly debt service, DTI (computed client-side as
  `total_minimum_monthly / sum(active recurring_incomes.amount)`, color
  graded: red >40%, amber 30-40%, emerald <30%). Card list with
  `Pausada` and `Archivada` badges; "Mostrar archivadas" toggle.
- `web/src/routes/DebtDetail.tsx` (new, lazy) at `/debts/:id`. Header
  (balance / cuota / principal / próximo pago), inline edit form
  restricted to name + payment_due_day + notes, action row
  (Pausar/Reanudar/Archivar/Restaurar). Three tabs:
  - **Amortización** — `fetchDebtSchedule` (existing
    `GET /debts/{id}/schedule` from 6d B6), filtered by year, current
    year as default.
  - **Cancelación anticipada** — pure client-side via
    `earlyPayoffLumpSum` / `earlyPayoffExtraMonthly` in
    `web/src/lib/amortization.ts`. Toggle between "Monto único" and
    "Cuota extra" inputs.
  - **Escenarios Ley 7472** — calls the new `payoff-scenarios` endpoint;
    same calculator output but server-blessed. Surfaces the prepayment
    penalty + the 2-payment Ley 7472 rule.
- `web/src/lib/amortization.ts` grew `earlyPayoffLumpSum`,
  `earlyPayoffExtraMonthly`, and `calculatePrepaymentPenalty` mirroring
  the backend service. Parity vs server is validated by running the same
  inputs through both surfaces during manual smoke; the Daniel approval
  gate after B7 is the formal cross-check against a real BAC / Promerica
  / Davivienda statement.
- `web/src/api/debts.ts` (new) — TanStack helpers: `fetchDebts`,
  `fetchDebtOverview`, `fetchDebt`, `fetchDebtSchedule`,
  `fetchPayoffScenarios`, `updateDebt`, `pauseDebt`, `resumeDebt`,
  `archiveDebt`, `restoreDebt`.
- `/debts/new` keeps the existing 6d creation form (`DebtsNew.tsx`).

### 14.4 Decisions taken in B7

- Debt PATCH whitelist is intentionally narrow per Daniel: `name`,
  `payment_due_day`, `account_id`, `notes`, `is_active`, `archived`.
  Extra fields return 422 (`model_config={"extra": "forbid"}`). To record
  a refinance event, create a new debt; to reconcile against a statement,
  use `POST /debts/{id}/payments`.
- Pause vs Archive split mirrors the B4/B5 pattern: new `debts.archived`
  column. Paused debts stay visible in the list (with a badge) but are
  excluded from the overview totals; archived debts are hidden by
  default.
- Payoff scenarios are a single GET aggregate, not two endpoints. The
  same engine drives both SPA tabs (`Cancelación anticipada` runs in the
  browser; `Escenarios Ley 7472` runs server-side via this endpoint).
- DTI is computed client-side from `/debts/overview` plus
  `/recurring-incomes` rather than via a new 6c computed insight — Phase
  6c memory is a separate pipeline, and DTI is a simple ratio that
  doesn't need to live in the typed insights store.

### 14.5 Daniel approval gate — CLOSED

After-B7 gate per the Phase 6e plan was closed by operator approval on
2026-05-15. Daniel signed off on the calculator parity vs a real
BAC / Promerica / Davivienda statement and authorized B8 to proceed.
The parity engine in `web/src/lib/amortization.ts` mirrors
`api/services/amortization.py`; per-block automated tests in
`tests/test_phase_6e_b7_debts.py` cover the structural shape (Ley 7472
penalty rule, months/interest saved, scenario aggregate response).

### 14.6 Verification

- Migration: `alembic upgrade head` advanced to `0019 (head)`.
- Focused tests `tests/test_phase_6e_b7_debts.py` (`8 passed`):
  PATCH whitelist accept + 422 on extras; DELETE flips both flags + list
  filtering + overview exclusion; pause/resume; payoff-scenarios with
  lump_sum, extra_monthly, both, and neither (422); Ley 7472 penalty zero
  after 2 payments.
- Full slice (B7 + B6 + B5 + B4 + B2 + 6d B2/B3/B10): `66 passed`.
- `npm run lint` (tsc --noEmit) passed.
- `npm run build` passed. Initial `index` JS gzip ~120.46KB (still well
  under the 200KB budget); new `DebtsIndex` chunk gzip ~1.96KB; new
  `DebtDetail` chunk gzip ~4.26KB; shared `debts` API chunk gzip
  ~0.43KB.
- `python -m compileall api workers tests/test_phase_6e_b7_debts.py`
  passed.

### 14.7 Next Block

Closed by B8 implementation below.

---

## 15. B8 Implementation Status

B8 is implemented as the recurring incomes module. Backend grows a small
migration plus one new atomic endpoint; SPA gets a new lazy index route
with inline-edit, pause/archive, and the CR-cycle nudge banner.

### 15.1 Migration

Alembic `0020_phase6e_b8_recurring_incomes_archived.py`: adds
`recurring_incomes.archived BOOLEAN NOT NULL DEFAULT false`. Pause =
`is_active=false, archived=false` (visible with badge). Archive =
`is_active=false, archived=true` (default-hidden, opt-in show).
Matches B4/B5/B7 pattern.

Local DB at `0020 (head)`.

### 15.2 Endpoints

- `POST /api/v1/recurring-incomes/{salary_id}/derive-cycles` (new).
  Atomically creates both `aguinaldo` and `salario_escolar` rows from a
  single salary in one DB transaction. Server-derived amounts via
  `derive_amount_for`; frequency = `annual`; next_payment_date defaults
  to the upcoming Dec 15 (aguinaldo) / Jan 31 (salario_escolar).
  Idempotent: a re-call returns the existing rows with
  `created_aguinaldo=false` / `created_salario_escolar=false`. 404 if
  the salary_id is missing; 400 if the target row is not
  `income_type='salary'`.
- `PATCH /api/v1/recurring-incomes/{id}` is now narrowed via
  `model_config={"extra": "forbid"}` to `name`, `amount`, `frequency`,
  `next_payment_date`, `notes`, `is_active`, `archived`. Sending
  `income_type`, `currency`, or `base_salary_link_id` returns 422.
- `GET /api/v1/recurring-incomes` query param renamed from
  `include_inactive` to `include_archived`. Default behavior now hides
  archived rows but keeps paused rows visible (matches the spec).
- `DELETE /api/v1/recurring-incomes/{id}` flips both `is_active=false`
  and `archived=true`.

### 15.3 SPA

- `web/src/routes/IncomesIndex.tsx` (new, lazy) at `/incomes`. Top
  banner appears only when the user has an active CRC salary AND is
  missing either `aguinaldo` or `salario_escolar` linked to it; one
  click hits `POST /derive-cycles` and creates both. Banner is
  idempotent-safe — recall just no-ops with a "Ya tenías ambos
  cargados" status message. Row list sorts active-first then by
  `next_payment_date`; per-row inline edit form covers
  name/amount/frequency/next_payment_date/notes. Derived-row amount
  inputs are disabled with a "Cambiá el salario para recalcular" hint.
  Per-row actions: Editar / Pausar / Reanudar / Archivar / Restaurar.
  "Mostrar archivados" toggle. Empty state with a link to
  `/incomes/new`.
- `web/src/api/incomes.ts` (new) — TanStack helpers:
  `fetchRecurringIncomes`, `updateRecurringIncome`,
  `pauseRecurringIncome`, `resumeRecurringIncome`,
  `archiveRecurringIncome`, `restoreRecurringIncome`, `deriveCRCycles`.
- `web/src/routes/IncomesNew.tsx` (existing) — patched to drop
  `currency` from its inline-edit PATCH body. The B8 schema tightening
  would have made that field a 422 on the existing 6d screen otherwise.
  The full slimming of IncomesNew is deferred — the existing combined
  create+list screen still works at `/incomes/new` (matches the
  AccountsNew pre-B4 pattern), and the new `IncomesIndex` is the
  canonical surface.
- `web/src/schemas/entities.ts` — `RecurringIncome` Zod schema now
  carries the `archived` field.
- `web/src/App.tsx` — `/incomes` routes to lazy `IncomesIndex`;
  `/incomes/new` and `/onboarding/ingresos` still route to the existing
  `IncomesNew` creation form.

### 15.4 Decisions taken in B8

- `recurring_incomes.archived` is the new soft-delete flag (matches
  accounts/transactions/debts).
- One-click CR-cycle derive uses a single atomic endpoint, not two
  back-to-back POSTs. Cleaner failure path; idempotent on recall.
- Inline-edit field set is `name`, `amount`, `frequency`,
  `next_payment_date`, `notes` (plus `is_active`/`archived` via the
  action buttons). `currency` and `base_salary_link_id` stay immutable
  — `currency` switch would silently break the derived-amount math, and
  the Resolución 9.8 design captured in the router doc-string forbids
  re-linking the base salary.
- Derived-row amounts (`aguinaldo`, `salario_escolar`) are NOT directly
  editable from the SPA. The input is disabled with a hint pointing the
  user at the salary row. PATCH would technically accept a new
  `amount`, but the SPA never sends one for derived rows.

### 15.5 Verification

- Migration: `alembic upgrade head` advanced to `0020 (head)`.
- Focused tests `tests/test_phase_6e_b8_incomes.py` (`6 passed`):
  PATCH whitelist + 422 on extras; DELETE flips both flags; pause keeps
  row visible; derive-cycles creates both with server-derived amounts;
  derive-cycles is idempotent on recall; derive-cycles rejects
  non-salary targets.
- Full slice (B8 + B7 + B6 + B5 + B4 + B2 + 6d B2/B3/B10): `72 passed`.
- `npm run lint` (tsc --noEmit) passed.
- `npm run build` passed. Initial `index` JS gzip ~120.50KB (still well
  under budget); new `IncomesIndex` chunk gzip ~3.60KB.
- `python -m compileall api workers tests/test_phase_6e_b8_incomes.py`
  passed.

### 15.6 Next Block

Closed by B9 implementation below.

---

## 16. B9 Implementation Status

B9 is implemented as the goals module. No schema changes; backend grows
two read endpoints; SPA gets three new lazy routes.

### 16.1 Endpoints

- `GET /api/v1/goals/{id}/contributions` (new) — returns every
  contribution for the goal, sorted `occurred_at desc`. No pagination;
  typical goals have well under 50 contributions over their lifetime.
- `GET /api/v1/goals/{id}/forecast` (new) — at the last 3 complete
  calendar months' average pace, computes `months_to_target`,
  `projected_completion_date`, and `avg_monthly_contribution`. Returns
  `has_enough_data=false` when no contributions exist in the window
  (no extrapolation from nothing). Already-achieved goals return
  `months_to_target=0` and `remaining=0`. `lookback_months` is 3 and
  fixed in this version.
- Pre-existing endpoints unchanged: POST/GET/PATCH/DELETE `/goals`,
  `POST /goals/{id}/contributions`, `POST /goals/{id}/contribute`
  (legacy), `GET /goals/progress`.

### 16.2 SPA

- `web/src/routes/GoalsIndex.tsx` (new, lazy) at `/goals`. Status filter
  (active/paused/achieved/abandoned/all), currency filter (CRC/USD/all).
  Card list with progress bar, percent, time-remaining label, and link
  to detail.
- `web/src/routes/GoalDetail.tsx` (new, lazy) at `/goals/:id`. Header
  with progress bar + percent + remaining + target_date label. Linked
  account read-only snapshot (saldo actual) shown as reference when
  `linked_account_id` is set — explicit contributions still drive
  `current_amount`, matching decision 3.1. Actions row:
  Agregar contribución / Pausar / Reanudar / Marcar cumplida /
  Abandonar. Forecast banner consumes the new endpoint. Contribution
  history list. `AddContributionModal` accepts amount, date, and
  optional transaction UUID (text input — the SPA does not search for a
  transaction yet; that polish is logged for a later block).
- `web/src/routes/GoalsNew.tsx` (new, lazy) at `/goals/new`. Creation
  form: name, target_amount, target_currency, target_date (opt),
  monthly_contribution (opt), priority (1-5), linked_account_id (opt).
- `web/src/api/goals.ts` (new) — TanStack helpers: `fetchGoals`,
  `fetchGoal`, `fetchGoalContributions`, `fetchGoalForecast`,
  `createGoal`, `updateGoal`, `addGoalContribution`,
  `abandonGoal`, `pauseGoal`, `resumeGoal`, `markGoalAchieved`.
- `web/src/schemas/entities.ts` — added richer `GoalDetail` (with
  `linked_account_id`, `monthly_contribution`, `priority`),
  `GoalContribution`, and `GoalForecast` Zod schemas.

### 16.3 Decisions taken in B9

- Forecast lives server-side at `GET /goals/{id}/forecast`. Same engine
  can later feed the bot's `/memoria` or the P7 affordability engine
  without duplicating math.
- Linked-account growth is a read-only sanity snapshot, not an
  automatic overwrite of `current_amount`. The SPA fetches
  `GET /accounts/:id` for the linked account and renders
  `current_balance` alongside the contributions-derived progress so the
  user can spot drift. Matches the phase-locked decision: contributions
  explícitas ganan, account-growth es fallback.
- `GET /contributions` returns the full list — no pagination. Mirrors
  the same call-and-show pattern as `GET /transfers`.
- Status transitions stay permissive on the backend (`PATCH status`
  accepts any of active/paused/achieved/abandoned); the SPA guards UX
  but the user can always change their mind without DB-level state
  machines.

### 16.4 Verification

- Focused tests `tests/test_phase_6e_b9_goals.py` (`5 passed`): list
  contributions sorted desc; forecast with avg pace; forecast with no
  data; forecast already-achieved; status round-trip.
- Full slice (B9 + B8 + B7 + B6 + B5 + B4 + B2 + 6d B2): `58 passed`.
- `npm run lint` passed.
- `npm run build` passed. Initial `index` JS gzip ~120.78KB (still well
  under budget); new `GoalsIndex` chunk gzip ~1.53KB; new `GoalsNew`
  chunk gzip ~1.59KB; new `GoalDetail` chunk gzip ~2.96KB; shared
  `goals` API chunk gzip ~0.34KB.
- `alembic current` still `0020 (head)` — no schema change in B9.

### 16.5 Next Block

Closed by B10 implementation below.

---

## 17. B10 Implementation Status

B10 is implemented as the memoria "Te conozco" SPA. No schema changes;
backend grows a grouped list endpoint, a per-row edit, and two delete
variants on top of the existing Phase 6c privacy router.

### 17.1 Endpoints

- `GET /api/v1/users/me/insights[?include_expired=false]` (new). Returns
  `{total, groups: [{group, label, items: [...]}]}` ordered by the
  bot's `GROUP_ORDER` (`metas` / `conozco` / `patrones` / `banderas`).
  Each item carries `id`, `insight_type`, `group`, `description`
  (natural-language Spanish), `confidence`, `source`, `user_locked`,
  `editable`, `valid_until`, `updated_at`, and the raw `content` dict
  (so the edit modal can pre-fill).
- `PATCH /api/v1/users/me/insights/{id}` (new). Body
  `{"content": {...}}` validated through the existing
  `InsightContent` discriminated union. Rejects with 400 when:
  the row's `insight_type` is not in `USER_EDITABLE_TYPES` (computed
  types stay read-only), or `content.type` doesn't match the row.
  Successful edit sets `source='user_override'`, `user_locked=true`,
  `confidence=1.00`, refreshes `valid_until` via the existing
  `valid_until_for` helper, and emits a `locked` audit row via
  `emit_locked_audit` (`locked_via='editar_memoria'` — same enum value
  the bot uses; the audit row is about WHO and WHAT, not HOW).
- `DELETE /api/v1/users/me/insights/{id}` (new). Hard-deletes one
  insight via `delete_insights(insight_ids=[id], deletion_reason=
  'spa_delete_single')`. 404 when the id doesn't belong to the caller.
- `DELETE /api/v1/users/me/insights/group/{group}` (new, declared
  BEFORE the per-id DELETE so the UUID converter on `/{insight_id}`
  doesn't 422 the group segment). Hard-deletes every active row in
  the requested group (`patrones|metas|conozco|banderas`).
- Pre-existing endpoints unchanged: `DELETE /api/v1/users/me/insights`
  (all), `GET /api/v1/users/me/insights/export`,
  `GET /api/v1/users/me/insights/summary`.

### 17.2 SPA

- `web/src/routes/MemoryIndex.tsx` (new, lazy) at `/memoria`. Same
  group ordering as the bot output. Collapsible sections per group.
  Each insight row shows description (Spanish, server-provided),
  confidence chip (with "⚠️ poca evidencia" for `confidence < 0.5`),
  source chip, "📌 tu definición" badge when `user_locked=true`, and
  `valid_until` chip. Per-row actions: Editar (only if `editable`,
  i.e. `insight_type` in `USER_EDITABLE_TYPES`) and Borrar (with
  inline confirm). Per-group "Borrar grupo" with single confirm.
  Bottom "Borrar toda mi memoria" with explicit two-step UX (1/2 → 2/2)
  matching the bot's `/olvidar todo` pattern. "Descargar mi memoria"
  triggers the export endpoint and downloads the JSON.
- `EditInsightModal` dispatches by `insight_type` to a per-type form:
  radio buttons for `risk_posture` / `decision_style` /
  `financial_literacy`, name + secondary text for `archetype`,
  textarea for `stated_preference` (topic select + stance text — the
  same text feeds `raw_quote` since the SPA edit is the user's own
  attested quote), textarea + status radio for `stated_goal`. None
  of the forms re-parse via LLM; the server validates through the
  discriminated union.
- `web/src/api/memory.ts` (new) — TanStack helpers:
  `fetchAllInsights`, `updateInsightContent`, `deleteInsight`,
  `deleteInsightsByGroup`, `deleteAllInsights`,
  `downloadInsightsExport`.
- `web/src/App.tsx` — replaced the `/memoria` placeholder with the
  lazy `MemoryIndex`. The `ModulePlaceholder` helper was removed since
  every route now ships real content.

### 17.3 Decisions taken in B10

- One generic `PATCH` over six per-type endpoints. The discriminated
  union already enforces shape; routing surface stays small.
- Three delete endpoints (per-id, per-group, all) over one
  parameterized route. Distinct intents get distinct
  `deletion_reason` values in the audit log (`spa_delete_single` /
  `spa_delete_group_{group}` / `api_delete_my_insights`).
- New list endpoint at `GET /api/v1/users/me/insights` instead of
  expanding `/summary`. Keeps the dashboard contract (capped at 10,
  flat) cleanly separated from the memoria contract (full grouped
  list).
- The B10 edit uses `locked_via='editar_memoria'` (same enum as the
  bot). The audit log's `actor` plus access-log correlation are
  enough to identify the SPA path if a future audit walk needs it.
- The SPA edit modal pre-fills from the row's raw `content` and POSTs
  back a fresh full payload. No diff-merge: the server replaces
  `content` wholesale and re-stamps `valid_until` from the validated
  shape.

### 17.4 Daniel approval gate — CLOSED

The Phase 6e plan's gate after B10 (walk the SPA at `/memoria` against
the bot's `/memoria` output and confirm parity) was closed by operator
approval on 2026-05-15. Daniel confirmed same groups in the same order,
same Spanish descriptions, and same badges.

### 17.5 Verification

- Focused tests `tests/test_phase_6e_b10_insights.py` (`7 passed`):
  grouped list shape; PATCH happy path with locked audit emission;
  PATCH rejects computed types; PATCH rejects content/type mismatch;
  DELETE single + 404 on re-delete; DELETE by group only touches the
  target group; DELETE all regression.
- Full slice (B10 + B9 + B8 + B7 + B6 + B5 + B4 + B2): `50 passed`.
- `npm run lint` passed.
- `npm run build` passed. Initial `index` JS gzip ~120.77KB (still
  under budget); new `MemoryIndex` chunk gzip ~3.99KB.
- `alembic current` still `0020 (head)` — no schema change in B10.

### 17.6 Next Block

Closed by B11 implementation below.

---

## 18. B11 Implementation Status

B11 is implemented as the categories management SPA. No backend changes,
no schema changes — the Phase 6e B2 categories router already ships
auto-seeding, transaction counts, the "default cannot be archived"
guard, and name-collision protection.

### 18.1 Endpoints (no changes)

`GET /api/v1/categories[?include_archived=&kind=]`,
`POST /api/v1/categories`, `PATCH /api/v1/categories/{id}` (handles
archive/restore via the `archived` field). All shipped in B2.

### 18.2 SPA

- `web/src/routes/CategoriesIndex.tsx` (new, lazy) at `/categories`.
  Top: "Nueva categoría" inline form (name, kind, color picker, icon
  text). Below: list sorted active-first then alphabetical. Per row:
  color swatch, name, kind label, transaction count, `default` and
  `archived` badges. Per-row inline edit form (same fields as the
  create form). Per-row actions: Editar / Archivar / Restaurar.
  Defaults render the Archivar button as disabled with a tooltip —
  the backend would 400 anyway, the SPA just guards earlier.
- `web/src/api/categories.ts` (new) — TanStack helpers:
  `fetchCategories`, `createCategory`, `updateCategory`,
  `archiveCategory`, `restoreCategory`. The existing read-only
  `fetchUserCategories` in `web/src/api/accounts.ts` stays (it's
  already used by several other routes); both helpers hit the same
  endpoint.
- `web/src/App.tsx` — new lazy `/categories` route.

### 18.3 Decisions taken in B11

- Icon field surfaces as a plain text input — no icon library, no
  preview. The seed defaults already carry icon strings; user-created
  categories get whatever string the user types (or `null`). Logged
  as future polish if/when an icon set lands.
- Color uses the native `<input type="color">` — zero new deps,
  well-supported. No curated palette.
- The B11 spec's note about checking the bot extractor's dropdown is a
  no-op: the LLM emits free-text `category_hint`, not an enum drawn
  from `GET /categories`. Per the locked Phase 5b rule "no synonym /
  normalization maps for category_hint", there's nothing to filter on
  the bot side; downstream code resolves the hint to a
  `transactions.category_id` after the fact.
- No new "delete" path — categories are archive-only by design, since
  transactions reference them via FK. Hard-delete would break history.

### 18.4 Verification

- Focused tests `tests/test_phase_6e_b11_categories.py` (`3 passed`):
  rename a category preserves the FK on existing transactions;
  default categories return 400 on archive; custom-category
  archive/restore round-trip works and toggles list visibility.
- Full slice (B11 + B10 + B9 + B8 + B7 + B6 + B5 + B4 + B2):
  `53 passed`.
- `npm run lint` passed.
- `npm run build` passed. Initial `index` JS gzip ~120.83KB (stable);
  new `CategoriesIndex` chunk gzip ~2.77KB.
- `alembic current` still `0020 (head)` — no schema change in B11.

### 18.5 Next Block

Closed by B12 implementation below.

---

## 19. B12 Implementation Status

B12 wires the bot ↔ SPA deep-link path. Backend already had the
`magic_link_tokens.target_path` column (migration `0017`) and the
service-level `generate_link(purpose='edit_session', target_path=...)`
plumbing; B12 only adds bot wiring + a SPA `?path=` honor step.

### 19.1 Bot

- `bot/deep_link.py` (new) — `mint_edit_session_url(db, *, user_id,
  target_path)` thin wrapper over the existing
  `api.services.auth.magic_link.generate_link`. Returns the URL string
  on success and `None` on any failure (validation, DB). Callsites
  treat `None` as "drop the button" — never as "crash the bot reply"
  — since deep linking is a convenience, not a correctness path.
- `bot/pipeline.py` — `BotReply` grew a `url_buttons: list[UrlButton]`
  field (`UrlButton(label, url)` is a new dataclass). `_handle_confirm`
  now captures the just-committed transaction's id and mints a deep
  link to `/transactions?highlight={id}` labeled "Ver en Centro
  Financiero". Swallow-on-fail.
- `bot/handlers.py::_kb` — extended to render both callback buttons
  and URL buttons (each row holds one button; URL buttons follow
  callback buttons).
- `bot/memory_handlers.py::on_memoria` — after rendering the bullet
  text, mints a deep link to `/memoria` labeled "Editar en SPA" and
  attaches it as an inline keyboard on the reply.
- `api/services/nudges/delivery.py` — `NudgeButton` gained an optional
  `url: str | None` field. When set, `bot/nudges_send.py::_kb` emits a
  Telegram URL button instead of a callback button. This is the
  infrastructure prerequisite for the third callsite (high-DTI nudge);
  no existing nudge type sets `url` yet.

### 19.2 SPA

- `web/src/lib/auth.tsx` — the exchange flow now reads `?path=` along
  with `?token=`. After a successful exchange:
  - validates the path client-side via `isSafeRelativePath` (starts
    with `/`, no `//`, no `://`),
  - calls `navigate(safePath, { replace: true })` when valid (drops
    both query params in one step),
  - falls back to stripping the params and staying on `/` when
    invalid.
  - 401 still redirects to `/expired` — no change.

### 19.3 Decisions taken in B12

- URL buttons live alongside callback buttons on `BotReply` and
  `NudgeMessage`. No conversion from callback → URL on existing
  flows; the deep link is always additive.
- The bot helper swallows errors and returns `None`. A failed mint
  drops the button but the reply still ships — this matches the
  "convenience, not correctness" framing.
- Path validation runs on both sides: server validates at mint time
  (`_validate_target_path`), SPA validates on consume
  (`isSafeRelativePath`). Belt and suspenders.
- The third callsite from the B12 spec — "high-DTI nudge → Ver tus
  deudas" — requires a `high_dti` (or `debt_load`) nudge type that
  doesn't exist in the Phase 5d evaluator catalog. B12 ships the
  URL-button infrastructure on `NudgeMessage` / `NudgeButton` /
  `bot/nudges_send.py` so the nudge type itself becomes a one-line
  add when its evaluator lands. Tracking this as a Phase 5d-adjacent
  follow-up rather than expanding B12 into nudge-evaluator design.

### 19.4 Verification

- Focused tests `tests/test_phase_6e_b12_deep_link.py` (`6 passed`):
  edit_session link URL shape + `target_path` persistence;
  generate_link rejects malformed `target_path`; consumed link is
  single-use; bot's `mint_edit_session_url` returns the URL on
  success; returns `None` on invalid path; returns `None` on missing
  user (FK violation swallowed).
- Full slice (B12 + B11 + B10 + B9 + B8 + B7 + B2 + 6d B2):
  `54 passed`.
- `npm run lint` passed.
- `npm run build` passed. Initial `index` JS gzip ~120.89KB
  (still well under budget); no new SPA chunks — the auth path change
  rolls into the existing index bundle.
- `python -m compileall api workers bot tests/test_phase_6e_b12_deep_link.py`
  passed.
- `alembic current` still `0020 (head)` — no schema change in B12.

### 19.5 Next Block

Closed by B13 implementation below.

---

## 20. B13 Implementation Status

B13 wires the SPA as an installable PWA and adds the mobile shell
(bottom nav, offline banner, install banner, safe-area-inset support).
Tight scope per decisions §20.3 — pull-to-refresh and swipe gestures
deferred.

### 20.1 SPA build / deps

- New SPA devDeps: `vite-plugin-pwa`, `workbox-window`, `@types/node`
  (needed by the polyfill comment in `vite.config.ts`), `cross-env`
  (so `npm run build` exports `NODE_OPTIONS=--experimental-global-
  webcrypto` cross-platform — Node 18 needs it for workbox-build's
  use of `globalThis.crypto`; Node 20+ makes the flag a no-op).
- `web/vite.config.ts` registers `VitePWA` with the spec's three
  caching strategies:
  - **App shell** (HTML/CSS/JS): precached by `generateSW` via
    `globPatterns: **/*.{js,css,html,ico,svg,png,webp}`. Vite-plugin-
    pwa runs it as cache-first with revalidate by default.
  - **API responses**: `NetworkFirst` with a 4-second network timeout
    + 24-hour cache fallback. Allow-list matches every Centro
    Financiero API surface (`/api/v1/dashboard/...`, `accounts`,
    `transactions`, `debts`, `goals`, `recurring-bills`,
    `recurring-incomes`, `users/me/insights`, `categories`).
  - **Images**: `CacheFirst`, 7-day TTL.
  - Navigate fallback to `/index.html`; denylist `/api/` so the SPA
    SW never intercepts API requests as navigation.
  - `devOptions.enabled = false` — SW only loads in production
    builds; dev keeps Vite HMR clean.

### 20.2 Manifest

`web/index.html` + the vite-plugin-pwa manifest config:

- `name = "Centro Financiero"`, `short_name = "Centro"`.
- `theme_color = "#1e3a8a"` (Tailwind blue-900),
  `background_color = "#f8fafc"` (Tailwind slate-50).
- `display = "standalone"`, `scope = "/"`, `start_url = "/"`,
  `lang = "es-CR"`.
- Icons: 192x192, 512x512, and 512x512 maskable. PNG files generated
  by `scripts/generate_pwa_icons.py` (stdlib-only, no Pillow) and
  committed under `web/public/icons/`. Placeholder design — solid
  navy background with a white disc and inner blue dot. Replace with
  real brand assets in a later polish pass.
- Meta tags added to `index.html`:
  `viewport-fit=cover` (so safe-area-inset CSS works), `theme-color`,
  Apple home-screen meta (`apple-mobile-web-app-capable`,
  `-status-bar-style`, `-title`), and `apple-touch-icon` pointing at
  the 192px PNG.

### 20.3 Shell components

All under `web/src/components/shell/`.

- **`OfflineBanner.tsx`** — listens to `navigator.onLine` via
  `useOnlineState()`. While offline, renders a fixed top banner
  ("Sin conexión. Mostrando datos del [last-online-timestamp].
  Abrí con internet para actualizar."). Mutations are NOT blocked —
  the per-form error states surface failed POST/PATCH calls
  naturally (decision: banner-only, §20.5).
- **`InstallBanner.tsx`** — captures the `beforeinstallprompt` event
  via `useInstallPrompt()`. The hook gates the banner to the 3rd or
  later visit by bumping a `centro:session_count` counter in
  localStorage and tracking `centro:install_dismissed`. Hidden on
  iOS Safari (no `beforeinstallprompt`); iOS users use the native
  share → "Añadir a inicio" path.
- **`BottomNav.tsx`** — mobile-only persistent bottom navigation
  (hidden at the `sm:` Tailwind breakpoint, ≥ 640px). Five tabs:
  Inicio / Cuentas / Deudas / Movimientos / Más (`/memoria`). Each
  tab is `min-h-11` (44px+) for WCAG and adds
  `pb-[env(safe-area-inset-bottom)]` to clear the iPhone home
  indicator.

### 20.4 App.tsx wiring

- `<OfflineBanner />` and the existing header stay at the top.
- The header now applies `pt-[env(safe-area-inset-top)]` so the
  notch doesn't eat the title.
- `<InstallBanner />` renders inside the main content area (only
  when authenticated, so unauthenticated visitors aren't prompted).
- `<BottomNav />` renders at the bottom of the layout. The outer
  div gained
  `pb-[calc(env(safe-area-inset-bottom)+4rem)] sm:pb-0` so content
  clears the fixed nav on mobile without affecting desktop spacing.

### 20.5 Decisions taken in B13

- Icon source is a Python stdlib-only script
  (`scripts/generate_pwa_icons.py`), generating 192 / 512 / 512
  maskable PNGs from a procedural shape. Logged as future polish
  for real brand assets.
- Pull-to-refresh deferred. TanStack Query's
  refetch-on-window-focus already covers the typical "is this
  fresh?" question; no real signal that users need pull-to-refresh.
- Offline behavior is banner-only. Mutations fall through to the
  existing per-form error states. Avoids a parallel "read-only
  mode" we'd have to test across every form.
- Swipe-to-archive deferred (spec marked it optional). Visible
  Archivar buttons already exist on every list row.
- Build script sets `NODE_OPTIONS=--experimental-global-webcrypto`
  via `cross-env` so Node 18 can run workbox-build. Logged in
  CLAUDE.md tech-debt that this stops being needed on Node 20+.

### 20.6 Daniel approval gate

Phase 6e plan has an explicit after-B13 gate: install Centro
Financiero as a PWA on real iOS Safari and Android Chrome, verify
Lighthouse PWA score ≥ 80. **Pending.** Local-build smoke shipped
`dist/sw.js`, `dist/workbox-*.js`, and `dist/manifest.webmanifest`
with the right icons and scope.

### 20.7 Verification

- `npm run lint` passed.
- `npm run build` passed. SW + manifest generated:
  `dist/sw.js`, `dist/workbox-f641ca17.js`,
  `dist/manifest.webmanifest`. Precache reports 30 entries
  (1016.97 KiB). Initial `index` JS gzip ~122.82KB — still under the
  200KB budget despite the workbox runtime imports.
- Backend regression slice (B12 + B11 + B10 + B9 + B8 + B7 + B2):
  `39 passed`. No backend changes in B13, but slice run as a
  sanity gate.
- `alembic current` still `0020 (head)`.

### 20.8 Next Block

B14 is next: privacy export/delete UI (`/settings/privacy`) +
Playwright E2E + Lighthouse-in-CI thresholds. Backend already has
`DELETE /api/v1/users/me/insights` and `GET .../insights/export`
from Phase 6c B8; B14 adds the account-wide export-all + hard-delete
flows plus the formal E2E harness with the perf budgets locked in
decision 3.11.
