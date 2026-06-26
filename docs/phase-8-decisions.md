# Phase 8 — Beta Users

Done-when (phase): onboard a second person via Telegram with accurate
reports within a week. Block status below; decisions locked per block.

## B1 — Telegram cold-start registration (2026-06-12)

**Why.** The operator hit the dead-end himself after the DB reset: an
unpaired `/start` pointed at `POST /api/v1/users/me/telegram/pairing-code` —
an authenticated endpoint, unusable without curl. There was no self-serve
path for a stranger: Phase 6d built guided *setup* for an existing, paired
user; registration of strangers was always P8.

**Key insight.** Pairing codes are only needed in the API→Telegram
direction (proving ownership of a pre-existing user row). In the inverse
direction no code is needed at all: Telegram authenticates
`telegram_user_id`, so the bot can create the user and bind it in one step.

**Flow (deterministic, zero LLM — control state like 6d B9):**
`/start` from an unknown telegram id → `bot/registration.py` mini-flow in
Redis (`telegram:registration:{tg_id}`, TTL 15 min, keyed by telegram id
because no user row exists yet): email (regex + uniqueness check; a taken
email points at the pairing path without burning the flow) → nombre →
confirmación con ToS → create user + bind + deliver `shortcut_token`
in-chat → `/setup` (the existing 6d onboarding takes over). `/cancel`
aborts; `begin_registration` is re-entrant (re-prompts the current step).

**Decisions:**
- **Shared creation path.** `api/services/users.py::create_user_with_defaults`
  extracted from the register router; REST register + bot flow both use it
  (token mint, default notification rule, default categories in one place).
  `telegram_user_id` binds in the same INSERT — no post-create pairing.
- **Consent at registration (first real Phase 7e ledger use).** Confirming
  the summary IS the `core_service` grant: `record_consent(purpose=
  'core_service', version=TERMS_VERSION, source='telegram')` commits in the
  same transaction as the user row. `TERMS_VERSION` lives in
  `bot/registration.py` ("2026-06.v1") — bump when the confirm copy changes.
- **Token in chat history, accepted.** Same exposure class as magic links
  and `/login` codes already sent to the same chat; rotate-able via API.
  Defaults CR/CRC/es-CR are not asked — change later in settings (P8
  follow-up), keep the flow 3 questions max.
- **`PAIR_PROMPT` rewritten**: unknown users are pointed at `/start` (new
  account) first; the curl path remains documented for existing-user
  pairing only.

**Files:** `bot/registration.py` (new), `api/services/users.py` (new,
extraction), `bot/redis_keys.py` (`telegram:registration:{tg_id}`, TTL 15
min), `bot/messages_es.py` (REGISTER_*), `bot/handlers.py` (on_start /
on_text / on_cancel unpaired branches), `api/routers/users.py` (refactor to
the service).

**Verification (2026-06-12):** `tests/test_phase_8_registration.py` (6 —
full flow incl. consent row + rule + categories + state cleanup, invalid
email, taken email, decline, re-entrant begin, no-state passthrough);
regression: handler coverage + 6d welcome + 6d endpoints + dispatcher (58)
and 7e data foundation + chat goal + magic link (27) green. No migration.

**Deferred:** currency/timezone questions in the flow (defaults CR);
language of ToS document itself (only the consent line exists); a
"¿borrar mi cuenta?" self-serve flow; rate-limiting registration attempts
per telegram id (abuse concern at public scale, not at beta).

## Activation & Advisor UX track (B2–B6) — planned 2026-06-25

**Status: CODE-COMPLETE 2026-06-26 (B2–B6); operator on-device sign-off
pending.** Mostly orchestration over existing primitives; the hard rules are
intact (LLM-extracts-rules-decide; the unified-cashflow byte-lock untouched; no
synonym/normalization maps; voseo CR). Full gate `scripts/test_phase_7b.sh` →
**141 passed**; mobile `npx tsc --noEmit` clean; `alembic → 0042`. A pre-merge
adversarial code-review workflow (5 scopes, find→refute) caught **1** confirmed
bug — `count_unassigned_month_expenses` was missing the `AJUSTE_CATEGORY`
exclusion (a reconciliation ajuste would wrongly count as an unassigned gasto) —
**fixed + regression-tested**; 2 other candidates (float-vs-Decimal on
`limit_amount`) were refuted (float is the documented API convention,
`NUMERIC(12,2)` quantizes).

Driven by the first two non-technical
user tests (2026-06-25) + the prior UX/psychology audit. Forks locked by the
operator: **reactive advisor** (not proactive) and **chat-led first run** (not
a visual wizard). Decision note: vault `Decision - Activation & Advisor UX
(Phase 8)`. Principle: **make depth optional, make the first win free, reward
real progress** — without breaking "LLM extracts; rules decide" or chat-first.

### User-test evidence (the official basis)
- **Tester 1 — activation.** Did not know what to do / where to start after
  login ("no instructions, no clear step-by-step"); additionally overwhelmed by
  **Gmail/sender configuration**, a power feature reachable in a first session.
  → B2 + B3.
- **Tester 2 — advisor expectation.** Expected advice, not just recording.
  Verbatim: *"estoy por pasarme del sobre de Gustos, ¿de dónde muevo plata?"* —
  asks at once for advice-forward behavior, a move-budget-between-sobres
  capability, and the over-limit moment as a decision (not the verdict
  `"Te pasaste por ₡X"`). → B4 + B6.

### B2 — Activation: chat-led first run + redefine "activated"
**Why.** The blank `DISPONIBLE ₡0` dashboard with no CTA is the highest
freezing-risk surface (audit + Tester 1). Time-to-first-value today is ~17–18
decisions / 8–20 min before any non-zero number.
**Plan.** First post-login surface = the chat, guiding step-by-step with zero
forms: *"¿Cuánto tenés ahora mismo en la cuenta donde te cae el salario?"* →
create the account + set the balance **anchor** (reuse the existing
`Intent.SET_BALANCE` + 6d B9 conversational account creation) → real number in
~20s → offer the next tiny step ("¿registramos tu último gasto?"). No new write
path — orchestration over existing primitives.
**Decisions.**
- "Activated" = 1 account + 1 real balance + 1 logged expense. The 4-entity
  completion model (account+income+debt+bill) stops being a *gate*; the "te
  falta registrar" copy → progress framing ("ya llevás…").
- Deep-link new registrants straight into the app via the B15 `ledgercr://`
  magic link + `useMagicLinkListener` — skip the `/login` 6-char dance for
  brand-new users (it stays for re-login).
- CR bank **picker** (deterministic list) instead of a free-text bank name.
- **Net-salary fast path**: accept the take-home (neto) directly; the gross→net
  CR salary calculator is optional, never a gate. Soften the "Calculá el salario
  neto desde el bruto antes de guardar" push.
- Email-taken recovery in registration (offer another email / pairing path, not
  a raw API endpoint).
- Dashboard empty states get explicit CTAs.
**Done-when.** A non-technical user, cold, reaches a real balance + one logged
expense without leaving the chat or asking for help.
**✅ Implemented 2026-06-26:** new `StartAccountCreation` dispatcher result — a
user with ZERO accounts who states a balance (`Intent.SET_BALANCE`) gets the 6d
B9 account-creation flow SEEDED with the stated balance+currency (skips those
questions via `account_creation._next_collection_step`), so the first account is
born with the real balance via `initial_balance` (no anchor). `onboarding_status`
gains `is_activated`/`has_balance`/`has_expense` (1 account + a real balance
[`initial_balance>0` or an `account_anchors` row] + 1 real expense [confirmed,
non-archived, not transfer/goal/ajuste]; `completeness_score` kept as legacy);
`onboarding_welcome` reframed to chat-led empty + progress next-step copy (drops
"te falta registrar"); `registration` REGISTER_DONE appends a `ledgercr://` deep
link (`/login` fallback). Mobile: `is_activated`-gated `FirstRunCard` (Dashboard)
+ first-run `EmptyHint` (Chat) + `api/onboarding.ts`. No migration. Tests:
`tests/test_phase_8_b2_activation.py` (11) + registration deep-link.

### B3 — Gmail out of the critical path + guided connect
**Why.** Tester 1 broke on Gmail/sender config — a power feature with no
guidance, reachable too early.
**Plan.** Gmail stops being surfaced to brand-new users (behind a clear opt-in,
not in the first-run path). When opted in, a real step-by-step guided flow: what
it does, why, what to expect (shadow review), and the ~7-day reconnect caveat
(GCP Testing-mode token expiry). Mobile + copy only; the Gmail endpoints exist.
**Done-when.** A new user never lands on sender config by accident; a user who
*chooses* Gmail is walked through it with instructions.
**✅ Implemented 2026-06-26:** mobile/copy only, no backend. Gmail demoted from
the primary "Más" grid to a de-emphasized "Avanzado" section; the tile routes
through a new `GmailIntroScreen` (4-step voseo opt-in: qué hace / para qué /
shadow review / ~7-día reconnect caveat) before sender config. The mid-scan
reconnect fallback + 90s timeout in `GmailScreen` preserved. `tsc` clean.

### B4 — Reactive advisor: reallocation-on-request
**Why.** Tester 2 wanted the agent to answer "¿de dónde muevo plata?". Today
envelopes have hard caps but **no move-budget primitive**, and the advisor can't
suggest a reallocation.
**Plan.**
- New deterministic **move-budget-between-sobres** primitive (a validated,
  atomic two-envelope limit change; same-currency v1; respects parent/child caps
  — Σ children ≤ parent, a parent can't shrink below allocated). Likely
  `POST /envelopes/reallocate {from_id, to_id, amount}`.
- New **read-only** advisor tool that computes candidates to pull from (sobres
  with the most unused `available`, reusing `compute_envelope_summary` so it
  can't drift from the bars).
- Chat: the LLM *proposes* a specific move ("¿muevo ₡15.000 de Ahorro a
  Gustos?"); user confirms; deterministic commit. Also fires when a sobre limit
  is hit during a chat capture (reactive, not unprompted).
- System-prompt capability bullet for the advisor.
**Decisions.** Obeys "LLM extracts; rules decide" — candidate computation + the
write are deterministic; the LLM phrases + proposes; the move executes only on
confirm. Reactive only (proactive interjection deferred behind a flag).
**Done-when.** "estoy por pasarme de Gustos, ¿de dónde muevo?" returns a
concrete, confirmable reallocation that correctly updates both sobres.
**✅ Implemented 2026-06-26:** deterministic
`api/services/envelopes.py::reallocate` + pure `reallocation_blocker` (shared by
REST + chat): amount>0, same currency, **`from.parent_id == to.parent_id`** (the
byte-lock guard — keeps `committed_outflows = total_limit = Σ root limits`;
root↔root incl. cross-class, or sibling↔sibling; root↔child rejected), from-side
floor (`from.limit − amount ≥ Σ active children AND > 0`).
`POST /api/v1/envelopes/reallocate`. Read-only `suggest_reallocation_candidates`
query tool (reuses `compute_envelope_summary`, ranks by `available` desc,
registered BEFORE the `compare_periods` cache anchor). `Intent.REALLOCATE_ENVELOPE`
(command "movéme X de A a B" → write; question "¿de dónde muevo?" → query) →
`_dispatch_reallocate` → `ProposeAction` → `_commit_reallocate` (NOT in /undo,
reverse by moving back). System-prompt cap bumped 8900→9100 for one capability
bullet (sanctioned bump, as at 7b B4). Mobile `reallocateEnvelopes` helper. No
migration. Tests: `tests/test_phase_8_b4_reallocate.py` (13) incl. a `total_limit`
before/after invariance proof.

### B5 — Emotional symmetry: earned-celebration layer
**Why.** The app is emotionally asymmetrical — 6 problem-focused nudge types,
near-zero celebration; goal-achieved is a silent status flip (peak-end rule: the
best moments are wasted). Habit-building users need "you did well."
**Plan.** Real positive peaks at genuine milestones: goal achieved, debt paid
off, first full month tracked, stayed under a sobre/budget for the month.
Delivered in-app (a celebratory moment on the relevant screen + a chat
acknowledgment) and, where appropriate, the first *positive* nudge type.
**Decisions.** The *decision* to celebrate is **deterministic** (rules decide
when a real milestone is hit — mirrors "the LLM never decides whether/when to
nudge"); the LLM may phrase the text. **No streaks, no points, no badges** (the
what-the-hell effect + infantilization risk). Aguinaldo / salario escolar are
authentic CR celebration + fresh-start anchors.
**Done-when.** Achieving a goal / killing a debt / closing a clean budget month
produces an earned, voseo moment — not a silent number change.
**✅ Implemented 2026-06-26:** 4 READ-ONLY evaluators (`goal_achieved`,
`debt_paid_off`, `first_full_month`, `under_budget_month`) on the Phase 5d
dedup/feed/delivery rails (`ALL_EVALUATORS` 7→11); rules decide WHEN, the LLM
only phrases; **no streaks/points/badges**. Migration `0042` widens the
`nudge_type` CHECK on `user_nudges` + `user_nudge_silences` (downgrade purges the
4 new types — the 0041/0033 safeguard). Event-driven immediacy:
`api/routers/goals.py` (status→achieved) + `debts.py` (record_payment→0 /
delete-at-0) call
`api/services/nudges/celebrations.py::trigger_celebration_nudges` **post-commit,
in an isolated session, swallow-on-fail** (a celebration can NEVER break a
goal/debt write); `first_full_month`/`under_budget_month` ride the nightly worker
AFTER `capture_user_snapshots` and read the FROZEN snapshot (never re-fire after a
limit edit). Mobile: celebratory render in `AlertsScreen` (`CelebrationModal`
deferred — the Alerts feed satisfies the done-when). Tests:
`tests/test_phase_8_b5_celebration.py` (13) + 94 nudge regression; migration
up/down/up clean.

### B6 — Envelope humanization
**Why.** The over-limit state (`"Te pasaste por ₡X"` + empty red bar) is a
punitive dead-end at the exact moment the ostrich quits; class + nesting +
allocation accounting are power-user load shown to everyone.
**Plan.**
- Over-limit → a decision: "¿cubrís moviendo de otro sobre?" reusing B4's
  reallocation primitive.
- Progressive disclosure: default the *tipo* on create (changeable, not a
  required upfront choice); hide "Sin asignar/Sobreasignado" until a sobre has
  children; hide nesting by default (advanced action); a **starter-sobres pack**
  (approve & tweak 5) for first envelope creation.
- Near-automatic assignment via the existing bulk-assign view as a gentle batch
  prompt ("tenés N gastos sin sobre, asignalos en 30s").
**Decisions.** An optional deterministic **merchant→sobre memory** (auto-tag
after the first confirmed assignment) is a **separate mini-decision** — it is a
user-confirmed mapping, explicitly NOT an LLM normalization map, so the "no
synonym/normalization maps" hard rule is preserved. Default this block to the
batch prompt; gate merchant-memory on its own decision note.
**Done-when.** Going over a sobre offers a one-tap reallocation; a first-time
user creates a working budget by approving a starter pack, not designing one
from a blank screen.
**✅ Implemented 2026-06-26:** `POST /api/v1/envelopes/starter-pack`
(deterministic bulk-create ≤8 ROOTS, explicit-trigger only so the byte-lock is
untouched); `EnvelopeCreate.envelope_class` now optional, default `wants`
(a sub-sobre still inherits the parent's class). Over-limit → one-tap
reallocation reusing B4's `reallocate` (mobile `ReallocateModal`, same-level
same-currency source picker). Progressive disclosure (hide allocation jargon
until a parent nests, nesting behind an "avanzado" toggle), `StarterPackModal`
(5 editable starter sobres scaled to monthly income), bulk-assign batch prompt.
Deterministic dispatcher `_maybe_append_unassigned_suggestion` ("tenés N gastos
sin sobre", once/conversation, mirrors `_maybe_append_attach_suggestion`; counts
via `count_unassigned_month_expenses` — now correctly excluding ajustes).
**Merchant→sobre memory deliberately NOT built** (stays a future
user-confirmed-mapping decision, never an LLM normalization map). No migration.
Tests: `tests/test_phase_8_b6_envelope_humanization.py` (11, incl. the
ajuste-exclusion regression).

### Sequence & dependencies
B2 first (biggest retention lever + Tester 1's core break). B4 before B6 (B6
reuses the reallocation primitive). B3 and B5 are independent and cheap — slot
anywhere. Each block ships with the usual done-when + the deterministic-write
guarantee; no new deps without justification.

### Deferred (track-wide)
Proactive/interjecting advisor (flagged, post-reactive); cross-currency
reallocation (fx); merchant→sobre auto-assignment (own decision); net-worth
trend view (post-beta); visual setup wizard (rejected for chat-led).
