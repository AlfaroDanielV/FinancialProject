# Fix Pack — Dispatcher Income, Card First-Due Date, Payments Nav, Reset Flows

**Date:** 2026-07-02 · **Commit:** `d03b683` (merged to `dev`, fast-forward,
pushed to origin) · **Migration:** `0044` · **Status:** code-complete, operator
on-device sign-off pending.

`committed_outflows`/unified-cashflow **byte-lock untouched**. Vault decision
notes: `Decision - Fetch Registered Income (Never Ask For Known Data)`,
`Decision - Credit Card First Due Date (Mid-Cycle Clamp)`,
`Decision - Empezar De Cero (Reset Flows)`.

Four dogfood-driven fixes. All honor the locked invariants: append-only ledger,
read-time projection (cards/debts never materialized), "LLM extracts; rules
decide", single source of truth for available funds, day-level granularity,
voseo (CR) user-facing copy.

---

## B1 — Dispatcher must use stored salary (bug)

**Symptom.** "¿Con mi salario puedo hacerle frente a mis deudas?" → the query
dispatcher fetched the debts correctly but then asked **"¿Cuánto ganás al mes?"**
despite a registered salary.

**Root cause (a dispatcher/prompt seam, not the engine).**
`get_savings_capacity` already reads registered income via
`api/services/envelopes.py::_monthly_income` and returns `monthly_income` +
`monthly_debt_payments` + `surplus`. But there was **no first-class income tool**,
**no prompt routing** for the salary-vs-debts question, and the only tool with
"salary" in its name — `compute_net_salary` — is a pure gross→net calculator that
*requires the user to type a gross*, luring the model into asking.

**Fix.**
- New read-only tool `app/queries/tools/income.py::list_registered_income(*,
  user_id)` — reuses `_monthly_income` (same-currency, quincenal ×2 via
  `income_frequency.PAYMENTS_PER_MONTH`, aguinaldo/salario-escolar excluded as
  annual lumps), lists each income with a `counted_in_monthly` flag, registered
  **before** the `compare_periods` cache anchor in `app/queries/tools/__init__.py`.
- Deterministic `debt_to_income_ratio` added to `get_savings_capacity`
  (`monthly_debt_payments / monthly_income`, zero-guarded) so the salary-vs-debts
  answer carries a real number the LLM narrates but never computes.
- System-prompt `_CONVENTIONS` routing: "¿me alcanza el salario/ingreso para mis
  deudas?" → `get_savings_capacity`; **NUNCA preguntés «¿cuánto ganás?» cuando el
  ingreso está registrado** (leelo con `list_registered_income`);
  `compute_net_salary` is only for a hypothetical gross the user provides. Prompt
  cap bumped 9100 → 9500 (sanctioned, logged in
  `tests/test_phase_6c_b9_system_prompt.py`).
- **Currency (operator decision): same-currency only.** A USD income for a CRC
  user is listed but not summed (`counted_in_monthly=false`) — no FX, no
  byte-lock risk.

**Hard rule.** The agent fetches registered income; it never asks the user for a
value the ledger already holds.

**Tests.** `tests/test_fixpack_income_tool.py` (5) + `test_tool_registry.py` +
the prompt-cap test.

---

## B2 — Credit-card first due date / phantom mid-cycle "overdue" (migration `0044`)

**Symptom.** A credit card created **1-jul** with `payment_due_day = 28` and a
`statement_day` earlier in the month surfaced a **28-jun** payment on Inicio,
marked **overdue**. Nothing is due until **28-jul**.

**Root cause (all read-time).** Cards are **projected, never materialized** (no
`bill_occurrences` row). `app/domain/credit/statement_cycle.py::last_corte`
returns the *previous* month's corte when today is early in the month, and
`api/services/accounts.py::balance_as_of` falls back to `initial_balance` (the
just-entered owed balance) with **no creation floor** — so the July-1 debt leaks
backward to a June corte that never existed → `statement_balance > 0` → due
`28-jun` → `is_overdue`.

**Fix.**
- Migration `0044`: nullable `credit_card_terms.first_due_date DATE`. NULL →
  unchanged behavior; existing cards unaffected until edited.
- **Clamp** in `api/services/credit_cards.py::card_statement_status`: after the
  natural `due_date = statement_due_date(corte, payment_due_day)`, apply
  `due_date = max(due_date, first_due_date)` when set. The feed reads
  `status.due_date`, so this corrects every consumer at once. A genuinely-overdue
  *later* statement is NOT suppressed (once the natural cycle produces a due ≥
  `first_due_date`, `max` returns the natural due).
- Mobile: `CardAccountCreateScreen` shows a two-candidate "¿Cuál es tu próxima
  fecha de pago?" picker (past "(ya pasó)" / future, default) and sends the
  chosen ISO date as `first_due_date` in the card-terms PUT; `CardTermsEditModal`
  has a `DateField` that edits it — **the repair path** for the existing card
  (no data backfill; set it to `2026-07-28` and the false overdue clears at read
  time).

**Hard rule.** A credit card opened mid-cycle clamps its projected due to the
user-confirmed `first_due_date`; it stays a read-time projection, never
materialized.

**Tests.** `tests/test_statement_cycle.py` +3 (clamp = `max(natural, first_due)`;
NULL keeps the natural/phantom due; feed shows no overdue when clamped).

---

## B3 — "Próximos pagos" home rows → payment flow (mobile only)

**Fix.** The home feed rows were plain `<View>`; now `Pressable`, reusing
`BillsScreen`'s mapping cross-tab (Inicio → Mas): `bill` → resolve the full
`bill`+`occurrence` (via `fetchRecurringBills` + `fetchBillOccurrences`) →
`BillDetail` mark-paid; `debt` → `DebtDetail` "Registrar pago"; `card_payment` →
a mounted `TransferModal` (card payment is a transfer). Added the missing
`recurring_bill_id` to the mobile `UpcomingFeedItem` type — the backend
(`api/routers/calendar.py`) already returned it, **no backend change**. The
payment-write code is untouched (honors the `paid_at → Optional[date]` ruling).

---

## B4 — «Empezar de cero» reset flows (no migration)

New service `api/services/reset.py`.

**A. Reiniciar saldos (non-destructive, recommended).** `POST
/api/v1/accounts/reset-balances` (body `[{account_id, value}]`) loops
`api/services/anchors.py::apply_anchor(source="reanchor", write_ajuste=True)` per
account in one transaction (validated owned + active → all-or-nothing). Reuses
the reconciliation machinery — each account gets an anchor + a labeled "ajuste de
reconciliación", the projected balance becomes the stated value in one step, and
**history is preserved**.

**B. Borrar historial (destructive).** `POST /api/v1/accounts/wipe-history`
requires the typed phrase `BORRAR HISTORIAL` (case-insensitive; else 400).
`wipe_user_history` runs in FK-safe order so the per-txn guards never fire:
delete `debt_payments` → delete `goal_contributions` → reset `bill_occurrences`
to `status='pending'` (clear `transaction_id`/`amount_paid`/`paid_at`) → delete
`transactions` → delete `transfers` → delete `account_anchors`; then reset
`Debt.current_balance = original_amount` + `payments_made = 0` and
`Goal.current_amount = 0`. **Operator-chosen scope = movements + derived records
+ anchors.** KEEPS config (accounts / debts / bills / goals / envelopes / incomes
/ categories) and **Gmail dedup** (`gmail_message_seen`, so old emails don't
re-import). The client then routes into the Option A balance form.

**Mobile.** `ResetScreen` (chooser → confirm → shared balance form) registered in
`MasNavigator`, entered from a new "Configuración" tile in the "Más" hub.

**Tests.** `tests/test_reset_flows.py` (4): A re-anchors each account to the
stated value (+ N anchors); a foreign account → `ResetError`; B deletes movements
+ derived + anchors, resets debt/goal progress + occurrences → pending, KEEPS
config, and the endpoint 400s without the exact phrase (data intact) then wipes
with it.

---

## Verification

`scripts/test_phase_7b.sh` green — mobile `tsc --noEmit` + 48 focused + 141
regression (cashflow byte-lock intact). Plus `test_fixpack_income_tool.py` (5),
`test_statement_cycle.py` (+3), `test_reset_flows.py` (4). `alembic → 0044
(head)`. A post-merge 4-agent adversarial workflow verified every documented
claim against `d03b683` — **zero mismatches**.

## Deferred

- Auto-detect the card first-due from a parsed statement PDF (today the user
  picks it at creation/edit).
- Option-B undo (deliberately irreversible; the typed-phrase confirm is the
  guard).
- Credit-account re-anchor in the reset form (fund accounts only — credit is
  movement-driven).
- On-device sign-off for the four mobile surfaces (no native CI — `tsc` is the
  only automated guard there).
