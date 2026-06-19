# Reassign Movement To Account (Edit Modal) — decisions

Operator ask 2026-06-15: *"necesito poder adjuntar los movimientos a las cuentas
desde la pantalla de movimientos y que se descuente el monto correspondiente en
la cuenta, la selección debe ocurrir con un dropdown."* Merged to `dev`
(`87dcb8a`, rebased over the envelope-sharing commit); operator on-device sign-off
pending. No migration. `committed_outflows`/cashflow math untouched (byte-lock
green). Canonical strategic note: vault `Decision - Reassign Movement To Account
(Edit Modal)`.

## The "se descuenta el monto" is automatic

Balances are **never stored** — `api/services/accounts.py::compute_account_balances`
derives each as `initial_balance + Σ confirmed, non-archived transactions WHERE
account_id = X`. Changing `transactions.account_id` moves the amount out of the
old account's balance and into the new one's with **zero balance-update code**. A
balance can never drift from the ledger (same principle as envelope spend).

## Backend (`api/`)

- `api/schemas/transaction.py::TransactionUpdate` — `account_id` is now an
  editable field (was deliberately excluded; the schema comment called account
  "immutable post-creation").
- `api/routers/transactions.py::update_transaction` — validates the account is
  the caller's and active (else **400 "Cuenta inválida."**, mirroring the
  create-time validation block). Existing immutability guards are UNCHANGED:
  shadow (`status != confirmed`), transfer leg (`transfer_id`), goal flow
  (`goal_id`), archived → **409**.
- **Cross-currency conversion.** When the destination account's currency differs
  from the row's, convert the amount via `api/services/fx.py::convert` (fixed
  ₡500/US$ reference) and **rewrite `transactions.currency` to the destination
  account's**. This mirrors the transfers convention
  (`api/services/transfers.py`: each leg stored in its own account's currency), so
  the currency-naive per-account balance `SUM(amount)` stays correct. The client
  edits the amount in the row's CURRENT currency, so the effective amount
  (`update_data.get("amount", txn.amount)`) is interpreted in `txn.currency`
  before converting, then quantized to cents.
- **No funds guard** (unlike transfers): reassigning doesn't move money the user
  lacks — it only relabels which account a movement hit.

## Mobile (`mobile/`)

- `mobile/src/components/AccountPickerModal.tsx` (new) — bottom-sheet picker;
  mirrors `CategoryPickerModal` / `EnvelopePickerModal`. Lists active accounts of
  **all currencies** (each row: name + ₡/$ + type) plus a "Sin cuenta" clear row.
  `onSelect(account | null)`, mutates nothing.
- `mobile/src/components/TransactionEditModal.tsx` — new **"Cuenta"** dropdown
  field beside Categoría/Sobre; resolves the current account name/currency via the
  cached `["accounts","active"]` query; shows a one-line hint *"El monto se
  convertirá a {colones|dólares}."* when the chosen account's currency differs;
  includes `account_id` in the save payload.
- `mobile/src/api/transactions.ts` — `TransactionUpdate` gained `account_id`.
- `mobile/src/screens/TransactionDetailScreen.tsx` — unchanged; already renders
  the Cuenta row and invalidates the `accounts` + `dashboard` query caches on
  save (the PATCH response carries the reassigned/converted row).

## Operator decisions

- **Location** → the **edit modal** (not an inline per-row dropdown on the list).
  Consistent with the app's bottom-sheet picker pattern.
- **Currency** → attach to **any account regardless of currency** with conversion
  (rejected: filtering the picker to same-currency accounts).

## Verification

- `tests/test_phase_6e_b4_accounts.py` — **8 passed** (3 new:
  `test_patch_transaction_reassigns_account_moves_balance`,
  `test_patch_transaction_cross_currency_converts` (₡1000 → $ account = −$2.00 at
  ₡500), `test_patch_transaction_rejects_foreign_or_archived_account` → 400).
- `tests/test_envelopes.py` (7) — post-rebase over the envelope-sharing commit.
- Mobile `tsc --noEmit` clean; `scripts/test_phase_7b.sh` green (cashflow
  byte-lock + transfers intact). No migration.

## Deferred

- Inline-on-list assignment. Manual fx-rate entry (uses the fixed ₡500/US$
  reference — BCCR live rate is the tracked tech-debt). Preserving the original
  amount/currency of a converted row (transfers don't either; could stash in
  `raw_data`). The **at-capture** account picker (choosing the account before a
  chat capture commits) stays deferred — a pre-commit proposal concern, separate
  from this post-create edit.
