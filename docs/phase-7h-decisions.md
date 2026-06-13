# Phase 7h — Savings Clarity + Analytics Screen

Status: code-complete on branch `phase-7h-analytics` (2026-06-13). Operator
on-device sign-off pending. Canonical decisions: vault
`Decision - Savings Excluded From Available Balance` +
`Decision - Charts Via react-native-svg`.

## Why

Operator dogfooding feedback:
1. The home screen's total was confusing because it summed savings + checking
   into one number. Savings should be **plata apartada** — excluded from the
   available total — and the user warned of this **when creating a savings
   account**. (The operator dropped the earlier "cuenta principal" idea —
   no primary flag, no migration.)
2. No analytics surface. Wanted a screen reached by tapping the budget card,
   with charts, and an **"explícame este gráfico"** button per chart that
   opens the chat and has the LLM explain it in plain Spanish.

## Feature 1 — Savings excluded from the available total (no migration)

- `api/services/dashboard/summary.py::_balance_split` — new helper returns
  `(available, savings)` bucketed by `Account.account_type`, JOINing
  transactions→accounts so a checking→savings transfer correctly lowers
  available and raises savings (transfers still net). Same
  `initial_balance + Σ confirmed txns` convention as `_balance_total`, which
  stays for back-compat.
- `DashboardSummary` (`api/schemas/dashboard.py`) gains `available_balance`
  (savings EXCLUDED — the home figure) + `savings_balance` (shown separately).
  Additive; `balance_total` unchanged.
- Mobile: `Dashboard.tsx` footer "Saldo total en cuentas" → **"Disponible"**
  (= `available_balance`) + a muted "Ahorros: ₡X (aparte)" line; a compact
  **DISPONIBLE** strip under the budget hero (hero stays "Te queda este mes").
  `AccountsScreen.tsx` consolidated strip mirrors the rule. `AccountCreateScreen.tsx`
  shows a savings-only hint: *"El dinero en cuentas de ahorro no se cuenta en
  tu disponible del mes — lo tratamos como plata apartada para tus metas."*
- **Envelope spend is UNCHANGED** — still counts expenses from all accounts;
  only the balance figure excludes savings (the operator's exact ask).

## Feature 2 — Analytics screen + "explícame este gráfico"

- New dep **`react-native-svg@15.12.1`** (Expo SDK 54-bundled, Expo Go-safe —
  no dev build). Reverses UI 2.0 §5 "no chart lib" (see decision note).
- `mobile/src/components/charts/{DonutChart,LineChart}.tsx` — on-palette SVG
  charts (color = meaning only; muted axes). Bars stay flex Views.
- `mobile/src/screens/AnalyticsScreen.tsx` — three cards from **existing**
  endpoints (no new backend): Flujo de caja 6m (`/dashboard/cash-flow`, line),
  Gastos por categoría (`/dashboard/summary` category_breakdown, donut),
  Sobres por clase (`/envelopes/summary` by_class, bars). Each card has an
  **"Explícame"** button.
- Navigation: `InicioNavigator` wraps the Inicio tab (DashboardHome →
  Analytics); `SobresSection` gained an `onOpenAnalytics` affordance
  ("Análisis") wired from `Dashboard.tsx`.
- Explain handoff (mobile only, **zero backend**): `ChatNavigator` Chat screen
  gains `{ initialMessage? }`; `Chat.tsx` auto-sends it once on mount (one-shot
  `useRef`, then clears the param). AnalyticsScreen's "Explícame" cross-tab
  navigates `navigate("Chat", { screen: "Chat", params: { initialMessage } })`
  with a per-chart Spanish question. The existing read-only query tools
  (transactions/aggregate, categories, envelopes, compare_periods) fetch the
  data and the LLM explains — rules provide data, LLM explains.

## Verification (2026-06-13)
- `scripts/test_phase_7h.sh` green: mobile `tsc --noEmit` clean; 2 focused
  (`tests/test_phase_7h_savings_balance.py` — savings excluded from available,
  transfer-netting correct) + 37 regression (dashboard b2, cashflow byte-lock,
  monthly cashflow, envelopes, goal funding).
- No migration (`alembic` unchanged). `committed_outflows` untouched.

## Deferred (logged)
- Quincenal (biweekly) budget periods.
- Scoping envelope *spend* by account.
- Credit-account treatment in the available total (unchanged).
- Passing chart raw-data into the LLM (we use a natural-language question +
  existing tools).
