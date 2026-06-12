# Phase 7e — Data Foundation: Advice Trace, Snapshots, Consent Ledger

Status: code-complete on branch `phase-7e-data` (2026-06-11; originally drafted as "Phase 7d"/migration 0030 — renamed after discovering the operator's parallel Phase 7d goal-funding stream owns that number). Decision note:
vault `Decision - Data Foundation (Advice Trace, Snapshots, Consent)`;
strategy context: vault `Long-Term Strategy - Financial Institution`.

## Why

The deterministic engines (affordability, cashflow, revolving) issue
verdicts and forget them; envelope/cashflow figures are computed live and
lose history when limits change; consent must exist before the second user,
because it can be aggregated later but never retro-fitted. Phase 7e turns
the engines into a labeled-dataset generator and lands the consent
substrate. No product behavior changes — everything in this phase is
passive accretion.

## Schema (migration `0031` — on top of the parallel Phase 7d goal-funding `0030`)

- **`advice_events`** — append-only. `kind` (app-validated, NOT
  CHECK-constrained — advice surfaces grow every phase; see
  `advice_trace.KNOWN_KINDS`), `verdict`, `surface`, optional polymorphic
  `subject_type`/`subject_id` (no FK — traces outlive subjects), full
  `inputs` + `result` JSONB, `outcome_status` (CHECK:
  pending/followed/ignored/mixed/unknown — semantics locked now, labeler is
  future work), `outcome`, `outcome_observed_at`. Indexes:
  `(user_id, created_at DESC)`, `(kind)`.
- **`envelope_snapshots`** — per-period frozen copy of each envelope's
  summary figures, identity denormalized (name/class/currency/parent/depth)
  so history survives hard deletes (`envelope_id` FK `SET NULL`). Partial
  UNIQUE `(envelope_id, period) WHERE envelope_id IS NOT NULL` = idempotent
  upsert key.
- **`cashflow_snapshots`** — per-period MonthlyCashflow picture (income or
  honest NULL, debt/bills/allocations/committed/surplus/savings, the gate)
  + the envelope grand totals + full `payload` JSONB.
  `UNIQUE(user_id, period)`.
- **`user_consents`** — append-only ledger. `purpose` CHECK-constrained
  (`core_service`, `behavioral_insights`, `product_research`,
  `aggregated_datasets` — compliance data, widen by migration like 0023),
  `status` granted/revoked, consent-text `version`, `source`,
  `occurred_at`. Current state = latest row per (user, purpose).

## Services + wiring

- **`api/services/advice_trace.py`** — `record_advice_event(...)`:
  **own short-lived session** (via the `app/queries/session` settable proxy
  so tests inject per-loop NullPool engines), **swallow-on-fail with loud
  logging** — recording can never break an advice path. `verdict_from()`
  maps feasible/gate to canonical strings. This is telemetry-class write
  (same class as `llm_query_dispatches`) — the query dispatcher's read-only
  rule (no user financial state mutation) is explicitly preserved.
- **Wired surfaces (5):** `assess_purchase` + `get_savings_capacity`
  (`app/queries/tools/affordability.py`), `get_card_analysis`
  (`app/queries/tools/credit_cards.py`, verdict `never_payoff`/`info`,
  subject = the account), the goal feasibility gate
  (`telegram_dispatcher._dispatch_create_goal`, surface
  `write_dispatcher`), and `over_commitment` (recorded in
  `nudges/orchestrator.py` at the post-dedup INSERT so one trace per fired
  nudge; `_insert_candidate` now returns the new id).
- **`api/services/snapshots.py`** — `capture_user_snapshots(db, user,
  today=None)`: upserts current period; on day ≤ 3 also recaptures the
  just-closed period with `today=last day of prev month`
  (`compute_envelope_summary` gained an optional `today` override — limits
  and reservations still read CURRENT rows; a limit edited inside the grace
  window bleeds into the closed month's snapshot, accepted). The cashflow
  recapture's structural inputs (income/debt/bills) are current-rows by
  design; only the envelope-window totals shift.
- **Runs from:** the nightly insights worker (`workers/insights_nightly.py`
  `_run_one_user`, own session + own try/except so snapshots and insights
  can't fail each other) and `POST /api/v1/jobs/capture-snapshots`
  (X-Shortcut-Token, idempotent, `JobRunResult`).
- **Consents:** `api/services/consents.py` (`record_consent`,
  `current_consents`, `has_consent`), schemas (`ConsentRecordRequest` is
  `extra="forbid"`, purpose is a Literal → 422 on unknown), router
  `GET/POST /api/v1/users/me/consents` (POST appends; GET shows
  `never_set` for undecided purposes). No consent UX in this phase — P8
  onboarding wires it.

## Verification (2026-06-11)

- `tests/test_phase_7e_data_foundation.py` — 7 passed (recorder persistence,
  assess_purchase + goal-gate wiring proven by positive row assertions —
  the recorder swallows failures, so only row presence proves wiring;
  snapshot freeze + idempotent upsert + prev-period recapture; consent
  grant/revoke append-only + 422 on unknown purpose).
- Regression: nudges (actions/callback/delivery/evaluators/feed) + nightly
  + goal-create + affordability + goal-feasibility + over-commitment —
  **107 passed**. Canonical `scripts/test_phase_7b.sh` cross-check green
  (48 focused + 136 regression incl. the byte-locked unified-cashflow
  regression). `scripts/test_phase_7e.sh` added as the phase gate.
- `alembic current` → `0031 (head)`.

## Operational incident (preserved)

This block was first numbered **0030 / "Phase 7d"** — colliding with the
operator's **parallel, uncommitted Phase 7d goal-funding work** in the main
checkout, whose own migration `0030` (`transactions.goal_id`) was already
applied to the shared dev DB. Effect: the first `upgrade head` silently
no-opped (version said 0030, the new tables didn't exist; no "Running
upgrade" log line). An intermediate `stamp 0029` + upgrade applied this
block's DDL but overwrote the goal-funding version record. Resolution:
renamed to **Phase 7e / migration `0031`** with `down_revision="0030"`;
both DDL sets verified physically present; `alembic_version` set to `0031`.
**Lessons: (1) claim migration numbers + phase letters against committed
history AND in-flight working trees; (2) if a fresh migration "applies"
without a "Running upgrade" log line, compare `alembic_version` against
physical tables before trusting it.** Note: this branch's alembic chain
needs the goal-funding `0030` file (uncommitted, main checkout) to resolve —
run alembic from a tree that has both files (post-merge).

## Deferred (explicitly)

- **Outcome-labeling worker** — turns `outcome_status='pending'` into
  followed/ignored/mixed by joining advice rows against subsequent
  transactions/goals/payments. Needs labeled-definition decisions (what
  counts as "followed"?) — P8-adjacent.
- Consent onboarding UX (native + bot) — P8.
- Auto-seeding `core_service` consent at register — with the UX, P8.
- Snapshot read API (the data is for analysis, not display, until a
  "tu historial" surface exists).
- `assess_financing` tool trace (advisor seam) — add kind when touched.
