# Duplicate Detection + Transaction Hard Delete — decisions

Operator ask 2026-06-15. Code-complete on `feature/dedup-hard-delete`; operator
on-device sign-off pending. Migration `0033` (CHECK widen only). Canonical
strategic note: vault `Decision - Duplicate Detection & Transaction Hard Delete`.

## A — Duplicate detection (deterministic; LLM only phrases)

- **Detector** `api/services/dedup/duplicate_detector.py`:
  - `find_likely_duplicate` — same currency + magnitude (±`0.01`), date within
    `DUP_DATE_WINDOW_DAYS = 3`, both confirmed non-archived expenses, not
    transfer legs / goal flows. Merchant similarity is a tiebreak booster, NOT a
    gate. Picks the closest by date.
  - `flag_and_notify` — at-capture hook: flags the **newer** row
    (`transactions.is_duplicate`, column unused since migration 0001 → no
    migration) + raises the nudge (idempotent, dedup_key `duplicate:{txn_id}`).
    Best-effort, swallow-on-fail (never breaks a capture). Returns the matched
    row + nudge_id for the inline chat warning.
  - `resolve_duplicate(keep)` — keep=clear flag, delete=hard-delete the row;
    resolves the nudge as **acted_on** (NOT dismissed → no auto-silence).
  - `clear_duplicate_nudges_for_txn` — resolves a stale dupe nudge when the row
    is deleted from any path.
- **Hooks**: chat post-commit (`bot/pipeline.py` `_handle_confirm` log_expense),
  `POST /transactions`, `POST /transactions/shortcut`. Gmail keeps its own
  reconciler dedup (not re-hooked).
- **Nudge** `nudge_type="duplicate_transaction"`: evaluator
  (`api/services/nudges/evaluators/duplicate_transaction.py`, safety net for
  flagged rows lacking a nudge), buttons `[Eliminar(act), Conservar(dismiss)]`,
  phrasing prompt, feed render. Delivers to Telegram + in-app Alertas. WhatsApp
  not wired (P5c) → inherited when it lands.
- **Inline "Ambas" surface**: `open_screen screen="duplicate_warning"` (mirrors
  `assign_envelope`; preferred over it when a dupe is found). Native
  `Chat.tsx` renders Eliminar/Conservar wired to the nudge act/dismiss
  endpoints (same as Alertas + Telegram). Telegram ignores `open_screen`.

## B — Permanent delete

- `DELETE /api/v1/transactions/{id}` (`api/routers/transactions.py`) →
  `hard_delete_transaction` (`api/services/transactions.py`, generalized from the
  `/undo` `delete_telegram_transaction`). Guards → 409 (shadow / transfer leg /
  goal flow / linked-to-bill / linked-to-debt; Spanish copy in
  `TXN_DELETE_REASON_ES`). Archived rows ARE deletable. Also resolves any stale
  dupe nudge for the row.
- Native: `deleteTransaction` + "Eliminar definitivamente" on
  `TransactionDetailScreen` with a simple destructive Alert confirm.

## Resolution surfaces (one behavior, three entry points)

- Telegram nudge buttons → `bot/pipeline.py::handle_nudge_callback` (dup branch).
- In-app Alertas + inline chat card → REST `/nudges/{id}/act|dismiss` (dup
  branch in `api/routers/nudges.py`).
- All call `resolve_duplicate`; act=delete (409 if guarded), dismiss=keep.

## Hard rules honored

- LLM never decides a duplicate / never deletes — rules decide, LLM phrases the
  push only.
- `committed_outflows` / cashflow math untouched (byte-lock green).
- Voseo CR copy. Day-level dates. No silent failures.

## Verification

`tests/test_duplicate_detection.py` (15) + `tests/test_transaction_hard_delete.py`
(8) + nudge/transactions/chat-post-commit regression + cashflow byte-lock green;
mobile `npx tsc --noEmit` clean. `alembic → 0033 (head)`.

## Deferred

WhatsApp delivery (P5c); auto-merge; backfilling `is_duplicate` on historical
rows; AlertsScreen-specific 409 reason copy.
