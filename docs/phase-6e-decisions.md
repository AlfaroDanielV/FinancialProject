# Phase 6e — Centro Financiero SPA

**STATUS:** ACTIVE
**Predecessor:** Phase 6d (Onboarding & Self-Registration)
**Created:** 2026-05-12
**B1 drafted:** 2026-05-12
**B2 implemented:** 2026-05-12
**B3 implemented:** 2026-05-12

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

B4 is next: full accounts module. The current `/accounts` route still points to
the existing 6d account creation/CRUD screen as a temporary bridge.
