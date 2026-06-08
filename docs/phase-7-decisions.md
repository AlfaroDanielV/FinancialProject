# Phase 7 — Affordability / Pushback Engine

**STATUS:** ACTIVE (all three surfaces shipped; operator on-device sign-off pending)
**Predecessor:** Phase 6f (Native iOS App) + the conversational-creation backlog
**Created:** 2026-06-08
**Vault decision note:** `05_Decisions/Decision - Affordability Engine.md`

---

## 1. Goal / done-when

Roadmap done-when: **deterministic affordability checks + an LLM explanation
wrapper.** The agent can answer "¿me alcanza para X?" and push back on
unrealistic goals — honestly, with real numbers, and without an LLM ever doing
the arithmetic.

---

## 2. Core decision — deterministic math, LLM explains only

The pushback engine is a **deterministic** service. The LLM never calculates
feasibility and never invents a number; it only explains the engine's verdict
and offers the alternatives the engine already computed. This is the same
"LLM extracts / explains; rules decide" split the rest of the system enforces.
Financial pushback an LLM could fabricate (wrong disposable, made-up "you can
afford it") would be worse than none — the ledger's whole value is that its
numbers are real and auditable, so the math lives in code and the LLM is
structurally unable to do the arithmetic.

---

## 3. The engine — `api/services/finance/affordability.py`

- `assess_affordability(...)` is **pure** (no DB, no LLM):
  `disposable = income − fixed − commitments`; a plan is `feasible` iff
  `monthly_needed ≤ disposable × 0.80`. The **80 % safety margin**
  (`SAFETY_MARGIN`, a single module constant) leaves headroom for the
  unbudgeted — it is the margin the CLAUDE.md spec mandates. Revisit only with
  real dogfood evidence, never preemptively.
- `timeline_months=1` models an immediate purchase ("¿puedo con X?"); a larger
  horizon models saving toward a target ("¿me alcanza para Y en N meses?"). The
  single `monthly_needed ≤ safe` test covers both.
- It returns two **deterministically-computed alternatives** so the LLM offers
  real options instead of inventing them: `min_timeline_months_feasible` (the
  shortest horizon that fits the safe ceiling) and
  `max_amount_feasible_in_timeline`.
- `gather_affordability_inputs(db, user)` pulls the real figures: monthly income
  (reuses `api/services/envelopes.py::_monthly_income`, so the answer can't
  drift from the home-tab income line), active fixed-amount recurring bills
  normalized to monthly, and active debts' minimum payments. Cross-currency is
  converted through `api/services/fx.py` (the ₡500/US$ placeholder pending the
  BCCR worker). Variable / RRULE bills are **excluded and surfaced as notes**,
  not guessed.
- **Honesty over fabrication.** No recurring income on file → `feasible=None`
  with a note, never a fabricated disposable. The LLM is told to ask the user to
  register income, not to assume one.

---

## 4. Surface 1 — read-only query tool (chat)

`app/queries/tools/affordability.py::assess_purchase` answers "¿me alcanza para
X?" / "¿puedo con ₡Y en N meses?" through the Phase 6a read-only dispatcher (no
mutation), mirroring the proven `get_envelope_spending` pattern.

- Registered in `register_builtin_tools()` **before** `compare_periods`, which
  stays the cache-breakpoint anchor (do not reorder).
- The tool description carries the honesty contract (report feasibility plainly,
  offer the pre-computed alternatives when it doesn't fit, never invent
  numbers).
- The query dispatcher **system prompt** capabilities list now surfaces the
  affordability capability in both the memory-on and memory-off variants so the
  LLM knows the surface exists without relying solely on the tool schema. The
  prompt's cache invariants and end-of-prompt snapshot are preserved (a
  capabilities bullet, not an appended few-shot).

---

## 5. Surface 2 — feasibility gate at conversational goal creation

`api/services/telegram_dispatcher.py::_dispatch_create_goal` (now async) calls
`assess_for_user` when the goal has a horizon (a target date) **and** the goal
currency matches the user's currency. `_goal_feasibility_line` words the
deterministic verdict into the proposal summary:

- feasible → "Necesitás ~X/mes y te alcanza con tu disponible."
- infeasible (positive disposable) → states the shortfall and offers
  "extender a ~N meses o bajar la meta."
- over-committed (no positive disposable) → explains fixed bills + debt already
  consume the income.
- unknown income → reports the monthly figure and says it can't confirm
  feasibility.

**Non-blocking:** the goal still proposes and can be confirmed either way
(`payload.action_type == "create_goal"`). Pushback informs; it never vetoes.
Cross-currency goals skip the gate to avoid mixing display currencies and
leaning on the FX placeholder, falling back to the plain monthly-needed line.

---

## 6. Why a shared service, not tool-local math

The engine is the reusable core for all three P7 surfaces:
1. the chat affordability tool — **done**,
2. the conversational goal-creation feasibility gate — **done**,
3. the proactive over-commitment nudge — **done** (see §5b).

One deterministic function keeps every surface consistent and keeps the LLM out
of the arithmetic on all of them.

---

## 5b. Surface 3 — proactive over-commitment nudge

A new Phase 5d evaluator `api/services/nudges/evaluators/over_commitment.py`
(`nudge_type="over_commitment"`) reuses `gather_affordability_inputs` — it does
**not** re-derive income/fixed/debt — and fires when active fixed bills + debt
minimums consume **≥ `OVER_COMMITMENT_RATIO` (0.85)** of monthly income (i.e.
< 15 % disposable headroom). It plugs into the existing evaluator → orchestrator
→ delivery pipeline, so all four Phase 5d anti-saturation rules apply unchanged
(rate limit, per-type silencing, quiet hours, dedup).

- **Honesty:** no recurring income on file → no candidate (never a fabricated
  disposable), same rule as the engine and the `missing_income` evaluator.
- **Dedup:** `over_commitment:{user_id}:{YYYY-MM}` — once per calendar month.
- **Priority:** always `normal` — a chronic state, not a timed emergency; the
  monthly dedup paces it and it does not jump the rate limit.
- **Payload** carries the real figures (income / fixed / debt / disposable /
  committed-ratio %); `phrasing._prompt_over_commitment` words them in voseo and
  is instructed not to calculate or invent. Buttons (`delivery._BUTTONS`):
  Revisar / Más tarde / No mostrar más (3, within the WhatsApp cap).
- **Schema:** migration `0023` widens the `nudge_type` CHECK on both
  `user_nudges` and `user_nudge_silences` to include `over_commitment` (the only
  schema change in P7). `alembic current` → `0023 (head)`.
- **Push delivery is Telegram-only** during Phase 6f (`users.expo_push_token` is
  schema-only, no APNs worker). To make the nudge reachable from the native app
  now — without waiting for P8 push — surface 3 also ships an **in-app pull
  feed** (§5c).

---

## 5c. In-app alerts feed (native pull surface)

`GET /api/v1/nudges/feed` (`api/services/nudges/feed.py::build_feed`) returns the
caller's **pending** nudges rendered to display text, for a native "Alertas"
screen (`mobile/src/screens/AlertsScreen.tsx`, reached from the "Más" hub).
Actions reuse the existing `POST /nudges/{id}/act` and `/dismiss`.

Two decisions distinguish a *pull* surface from the Telegram *push* path:

- **Deterministic rendering, not the LLM.** The push path phrases each nudge
  with Haiku at send time and never persists the text. Coupling a screen the
  user opens to a live Anthropic call (latency, 12s timeout, uptime) would make
  the alerts list fragile. The payload already carries the real, deterministic
  numbers, so `render_nudge_text` templates them in CR voseo. The push path
  keeps its LLM phrasing — only the prose differs; the numbers are identical.
  This is consistent with the deeper "deterministic before AI where possible"
  principle and keeps the alerts screen instant, free, and offline-safe.
- **Push-pacing does NOT gate a pull.** Rate limit and quiet hours exist to
  avoid *interrupting* the user and have no meaning on a screen they chose to
  open, so the feed ignores them. Per-type **silence** still applies ("stop
  showing me this" is channel-independent). The feed is **read-only** — building
  it never marks a nudge `sent`, so it's a safe GET and doesn't race the Telegram
  delivery worker. `later` has no endpoint: the app hides the card locally and it
  returns on the next fetch (matches the existing "Más tarde" semantics).

No schema change for the feed. Verified by `tests/test_nudges_feed.py`
(deterministic render incl. USD, build_feed pending/silenced/non-pending,
endpoint + act-clears-it) and mobile `tsc --noEmit`.

---

## 7. Verification

- `tests/test_affordability.py` — pure engine (feasible / infeasible+shortfall /
  immediate purchase / over-committed / no-income-is-unknown / excluded-bill
  notes / 80 % constant) + DB-backed `gather` and `assess_purchase` against
  seeded income/bills/debt.
- `tests/test_phase7_goal_feasibility.py` — the goal-creation gate wording for
  feasible / infeasible+alternative / over-committed / no-income / no-deadline.
- `tests/test_phase7_over_commitment_nudge.py` — evaluator fires/healthy/
  no-income/no-commitments/dedup, orchestrator create+dedup+silence, phrasing
  carries real numbers, buttons capped at three.
- `tests/test_nudges_feed.py` — deterministic render (incl. USD + unknown-type
  fallback), `build_feed` pending/silenced/non-pending, `GET /nudges/feed`
  endpoint + act-clears-it. Mobile `tsc --noEmit` clean for the Alertas screen.
- `tests/test_system_prompt_builder.py` — capabilities-list discoverability lock.
- Full P7 + touched-path slice green (engine, goal gate, over-commitment nudge,
  in-app feed, all four nudge suites, goal chat creation, both system-prompt
  suites). Migration `0023` applied; `alembic current` → `0023 (head)`.

---

## 8. Open / deferred

- **FX placeholder** — cross-currency inputs convert at the fixed ₡500/US$ rate
  until the BCCR worker lands. Immaterial today (income/bills/debt are
  overwhelmingly CRC). Cross-currency *goals* skip the gate entirely.
- **Operator on-device sign-off** — the chat affordability answer and the
  goal-creation pushback wording want a real-device pass before P7 closes.
