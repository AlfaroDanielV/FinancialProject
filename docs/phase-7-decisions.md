# Phase 7 — Affordability / Pushback Engine

**STATUS:** ACTIVE (all three surfaces shipped; operator on-device sign-off pending)
**Predecessor:** Phase 6f (Native iOS App) + the conversational-creation backlog
**Created:** 2026-06-08
**Vault decision note:** `05_Decisions/Decision - Affordability Engine.md`

> **Updated 2026-06-09 — Unified Monthly Cashflow.** The verdict denominator
> changed from `income − fixed − debt` to the envelope-aware **surplus**, and is
> now **gated**. See **§9** (the canonical description of the current engine);
> §3 below is kept for history with the change flagged inline. Vault:
> `05_Decisions/Decision - Unified Monthly Cashflow.md`.

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

> **Superseded 2026-06-09 (see §9).** `assess_affordability` now takes a
> `MonthlyCashflow` and judges against the **surplus** (`income − committed
> envelopes`), gated. The `income − fixed − commitments` denominator below is
> historical; `gather_affordability_inputs` (income/bills/debt gathering) is
> unchanged and still feeds the cashflow.

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

## 5d. Financing advisor + advise-first debt handoff

**Problem (observed):** a user explored financing a car — *"si pido un préstamo
para la prima y luego financiarlo a 20 años al 45%"* — and the agent replied
*"Perfecto, te abro el formulario de préstamo"*. It went into **register mode**
when the user wanted **advisor mode**. Root cause: "advisor vs register" already
maps to the `query` vs `write` dispatcher, but (a) there was no read-only tool
that could analyze a *hypothetical* loan, so the only financing intent in the
system was `CREATE_DEBT`, and (b) the affordability tool literally asks
"¿estás considerando financiarlo?" with no tool to answer it. The conditional
"si…" was ignored and routing fell through to the form.

**Fix — three parts, same "rules decide; LLM explains" split:**

1. **`assess_financing` read-only query tool** (`app/queries/tools/financing.py`).
   Reuses `amortization.compute_french_payment` (same cuota math as the debt
   module) + the affordability engine's `gather_affordability_inputs` /
   `SAFETY_MARGIN`. Given price / rate% / term / optional down-payment → cuota,
   total interest, interest-multiple-of-price, and a **cuota-vs-safe-disposable**
   verdict. No income on file → `cuota_fits_disposable=None` (honest), still
   simulates the loan. Registered before the `compare_periods` cache anchor.
   This is advisor mode for loans and closes the loop `assess_purchase` opens.
2. **Routing (extractor prompt, examples not synonym maps).** `create_debt` is
   narrowed to *registering* a decided/existing loan ("registrá", "saqué",
   "tengo", "debo"); **exploratory financing** ("si lo financio…", "cuánto sería
   la cuota", "¿me conviene?") → `dispatcher=query`. Two contrasting examples
   added, plus a `query` system-prompt capabilities bullet for loan simulation.
3. **Advise-first debt handoff** (`_dispatch_create_debt`, now async). When the
   register path *does* fire and already carries principal + rate + term, it
   computes the deterministic cost and **leads the form handoff** with it
   ("Ojo: a esa tasa la cuota rondaría ₡X/mes y pagarías ~₡Y en intereses.
   Supera tu disponible seguro…") instead of opening blind. No rate → plain
   handoff (the form's PDF-upload / no-rate path is unchanged). Debt still never
   commits in chat.

**Why not a sticky "mode" toggle:** a global advisor/register flag traps capture
("gasté 5000 en café" must always work) and the user forgets which mode they're
in. Per-utterance intent (the LLM's job) + advise-first-by-default for the
high-stakes debt write is safer. Users can still force it explicitly ("solo
analizá" → query, "registrá el préstamo" → write) — those are just more routing
examples.

Verified by `tests/test_phase7_financing.py` (cuota math, fits/doesn't-fit,
down-payment, no-income honesty, advise-first handoff leads-with-cost vs plain
when no rate) + no regression in `tests/test_phase_6f_chat_create_debt.py`.

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
- `tests/test_phase7_financing.py` — `assess_financing` cuota/interest math,
  fits/doesn't-fit verdict, down-payment, no-income honesty; advise-first debt
  handoff leads-with-cost vs plain-when-no-rate. No regression in the existing
  `test_phase_6f_chat_create_debt.py`.
- `tests/test_system_prompt_builder.py` — capabilities-list discoverability lock.
- `tests/test_phase7_monthly_cashflow.py` — the cashflow source of truth
  (positive/deficit/gates/double-count guard/`months_to_goal`/savings
  passthrough + DB end-to-end excluding CR lump cycles). **(§9)**
- `tests/test_phase7_unified_cashflow_regression.py` — the ₡1M phone:
  verdict + surplus + savings-plan consistent across tools, and the three gates
  firing distinct reason+copy. **(§9)**
- Full P7 + touched-path slice green (engine, goal gate, over-commitment nudge,
  in-app feed, all four nudge suites, goal chat creation, both system-prompt
  suites). Migration `0023` applied; `alembic current` → `0023 (head)`. The §9
  unified-cashflow rework added no migration (envelopes already exist).

---

## 8. Open / deferred

- **FX placeholder** — cross-currency inputs convert at the fixed ₡500/US$ rate
  until the BCCR worker lands. Immaterial today (income/bills/debt are
  overwhelmingly CRC). Cross-currency *goals* skip the gate entirely.
- **Operator on-device sign-off** — the chat affordability answer and the
  goal-creation pushback wording want a real-device pass before P7 closes.
- **80% margin revisit (§9, sub-decision A)** — the margin's rationale weakened
  under a complete budget; revisiting it is a **separate dogfood ticket**, not
  this work (changing the verdict base AND the margin together would make a
  dogfood behavior change unattributable). No date/owner assigned.
- **Redirectable savings** — a schema-level model that distinguishes an
  earmarked emergency fund from free accumulation (so savings could be netted
  out of committed) is a later decision. Today all envelope classes count as
  committed; redirection is offered as the user's option in copy only (§9 B).

---

## 9. Unified Monthly Cashflow (2026-06-09) — envelope-aware verdict

**The problem.** "¿Puedo comprar un teléfono de ₡1M?" correctly answered *no*,
while "¿cuánto me sobra al mes?" reported ~₡900k. Both flowed through the **same**
engine (`assess_for_user → assess_affordability`); the single defect was that the
`income − fixed − debt` disposable is **blind to envelopes** — the *gastos fijos
aún no realizados* the user has already budgeted. Hiding the surplus from the
display (and leaving the verdict on the blind denominator) was rejected: a sibling
contradiction survives ("te sobran ₡100k" + "sí, comprá el de ₡600k"). The
denominator, not the display, is the disease — so the **verdict** goes
envelope-aware too. Vault: `Decision - Unified Monthly Cashflow`.

**One source of truth.** `api/services/finance/cashflow.py::compute_monthly_cashflow`.
Every "cuánto sobra / puedo comprar / cuánto ahorro" answer flows through it. The
LLM never computes these — it calls a tool and narrates.

**Model A (envelopes = the complete budget).** `committed_outflows =
envelope_allocations` (active **root** allocations, FX-converted; reuses the live
envelope summary's `total_limit` so it can't drift from the home bars and nested
children aren't double-counted). Debt payments + recurring bills are computed for
**transparency** but NOT re-added — in a zero-based budget they already live inside
an envelope, and there's no FK linking an envelope to a bill/debt, so adding them
would double-count with no way to dedup. `surplus = income − committed_outflows`;
negative = **deficit**, surfaced honestly.

**Envelope-aware verdict.** `assess_affordability(cashflow, *, desired_amount,
timeline_months)` judges `monthly_needed ≤ 0.80 × surplus`. This **supersedes the
Phase 7a lock** (`feasible = 0.80 × (income − fixed − debt)`), done consciously —
`tests/test_phase_7a_context.py`'s headline assertions were rewritten; its
context-SIGNAL assertions (over-limit flag + upcoming event don't move the verdict)
are unchanged.

**Three gates** withhold the confident verdict/surplus and emit DISTINCT copy
(each a different user action — `gate_guidance()` is the single copy source):
- `no_income` → *registrá tu ingreso* (no recurring income on file).
- `no_budget` → *armá tus sobres* (no active envelopes; committed would be 0 and
  surplus collapses to the old inflated income figure).
- `under_coverage` → *que tus sobres cubran tus deudas + gastos fijos*
  (`allocations < debt + bills`; committed understates reality → surplus inflated).
  A hard gate — a `max(envelopes, debts+bills)` floor was rejected (over-states in
  the disjoint case).

**Sub-decisions (operator, 2026-06-09).**
- **A — KEEP the 80% margin**, now on `surplus`. Changing the verdict base AND the
  margin in one PR would make dogfood unattributable; revisit is a separate ticket.
- **B — count ALL classes** as committed (`committed = total_limit`). The engine
  must not unilaterally decide savings is grabbable. A transparency-only
  `savings_allocations` (Σ savings+investing roots; subtracts nothing) powers a
  deterministic nuance: when the verdict is *no* and `savings_allocations > 0`, the
  copy offers reallocation as the **user's** option ("podés reasignar parte de tus
  sobres de ahorro/inversión").

**CR lump cycles excluded.** `_monthly_income` now excludes `aguinaldo` +
`salario_escolar` (annual lumps paid Dec/Jan; amortizing them was phantom monthly
cash). One-line filter at the single income source → flows to the envelope summary,
the engine, and the cashflow.

**Consumers reconnected (the verdict engine is shared, so they flip together).**
`assess_purchase`, `get_savings_capacity`, `assess_financing`
(`cuota_fits_surplus`), the goal-creation gate (`_goal_feasibility_line`, with the
savings-reallocation nuance), and the debt-handoff advisory line — all report
`surplus` + `gate_reason`. **Exception by design:** the `over_commitment` nudge
keeps `committed = fixed + debt` — it answers *structural fixed-obligation pressure
vs income*, a different question; rerouting it to envelopes would false-trigger an
aggressive saver. (It still benefits from the lump-excluded income.)

**Contract — `MonthlyCashflow`:** `monthly_income`, `debt_payments`,
`recurring_bills`, `envelope_allocations`, `committed_outflows`, `surplus`,
`has_budget`, `covers_obligations`, `income_known`, `savings_allocations`,
`currency`; properties `reliable` / `gate_reason` / `is_deficit`; helper
`months_to_goal(amount)` (`ceil(amount / surplus)` when surplus > 0, else None).
All `Decimal`, CRC default. No migration (envelopes already exist).
