# Phase 10 — Advisory Mode + Principle Library — Implementation Plan

> **Status:** 🟡 PLAN — decisions locked with the operator 2026-07-01. Build block-by-block; pause for sign-off at the gates marked ⛳.
> **Parent:** `phase-10-advisory-decisions.md` (the P10 "Capa de Asesoría" + Principle Library decisions). **NOTE:** that doc + the curated NotebookLM export do **not** currently exist in the repo or the vault (verified). Bringing them in is **B0**.
> **Scope:** a toggleable *advisory mode* (Telegram command + native chat button) that puts the assistant into a flexible planning/coach persona, performs a holistic assessment (net worth, debt, cashflow, chat memory), answers open-ended planning questions, and narrates the answer through a curated behavioral-finance *principle library* — **without ever inventing or altering a number**.
> **Language split:** doc body English; user-facing strings marked `// UX-COPY (es-CR, voseo)`.

---

## 0. The one principle that orders the whole design

Two kinds of knowledge, never mixed (extends the app's core thesis "LLM extracts; rules decide"):

1. **The user's financial reality** — balances, debts, surplus, cuotas, net worth. Exact, quantitative, 100% deterministic. Produced by the existing engines; the advisory layer *consumes* it and never recomputes it.
2. **Advisory knowledge from books** — how to reframe scarcity, how to talk to someone underwater without shaming, what gives psychological momentum. Qualitative: principles, frames, tone.

**Rules decide WHAT** (numbers, verdict, feasibility). **The library shapes HOW** it is narrated (tone, framing, sequencing). The library shapes the *how*, never the *what*.

---

## 1. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Everything, sequenced** in one gated doc: advisory shell → assessment tools (incl. retirement) → principle library → guardrails → C-hardening. | Operator choice 2026-07-01. |
| **D2** | **Option A now, harden to Option C later.** v1 = an in-process advisory sub-mode of the existing read-only query dispatcher (agentic tool loop, behavioral guardrails). A later block freezes a deterministic context-pack and gives the narration LLM **no DB tools** (Option C), making "no invented number" a *structural* guarantee. | Fast value now; structural safety later. |
| **D3** | **Retirement engine is in v1.** All four flagship questions answered at launch, including `project_retirement`. | Operator choice 2026-07-01. Highest-risk block (⛳ operator-validated). |
| **D4** | **In-process only; NO MCP server in v1.** The assessment tools are pure delegators in the existing `@query_tool` registry, designed so a thin MCP adapter can wrap the *same* `run_advisory`/tools later if an external client (Claude Desktop) ever becomes a real requirement. | MCP adds zero capability in-process; an engine-importing MCP server is a second front door into the monolith (illusory independence); the Anthropic MCP-*connector* route bypasses the budget gate + audit choke points. |
| **D5** | **Library shapes narration, never computation.** No principle, match, or template may produce/alter/imply a financial figure. Enforced by a CI *no-number-in-principle* scorer, not a guideline. | P10 L2/L8. |
| **D6** | **Hope anchored in real numbers; never toxic positivity.** When the data is genuinely bad, honest-but-compassionate framing — never manufactured optimism. Enforced (Gate D). | P10 L3. |
| **D7** | **Coaching/education, not therapy.** Distress signals route to a warm human hand-off via a **deterministic** detector, not an LLM judgment; no clinical intervention. | P10 L5. |
| **D8** | **`financial_state` has ONE owner in the P7 layer** (next to `cashflow.gate_reason`). The advisory layer *consumes* the label; principle matching never computes it. | P10 §2 / L7. Prerequisite — see §2. |
| **D9** | **Archetype = ranking modifier, never a selector.** Deterministic `financial_state` dominates the match; the money archetype (P6c) only re-weights already-matched principles and weighs below the behavioral signal. | P10 L9. |
| **D10** | **Curated library, NOT RAG/vectors (v1).** A version-controlled Python module in `api/data/`; the PR is the human-review gate. | P10 L1 / Gate A. Corpus is tiny today (5 principles, 1 book). |
| **D11** | **Auditable end-to-end.** Every number traces to a deterministic finding (tool JSON in `llm_query_dispatches.tools_used` + `advice_events`); every framing traces to a source-attributed principle. | P10 §6. |
| **D12** | **Intent-per-message spine, NOT a hard sticky mode.** Advisory framing is decided per turn (advisory-intent OR a short-lived idle-expiring continuity session); a transactional query always overrides to a plain answer; exit is always easy, re-entry one line. | Operator O5 — persist-until-`/normal` traps users who forget they're in it. |
| **D13** | **No well-formed chat message may ever produce a non-2xx or a bare generic error; the failure class is always distinguishable** (understanding vs system). Enforce `intent`↔`dispatcher` consistency; net the write/control branch symmetrically with the query branch; a mixed write+analytical message routes to the analytical/advisory path, never an error. | 2026-07-02 compound-intent bug (§13). Advisory questions are compound by nature, so this class must be closed alongside B0–B2. |

---

## 2. Prerequisite — `financial_state` single owner (build FIRST)

`financial_state` (e.g. `negative_surplus`, `high_interest_debt`, `irregular_income_stress`, `recent_overspend`, `first_time_saving`, `stable_building`) is a **deterministic label** with exactly one owner. It **does not exist yet** (grep-confirmed zero hits). If it gets bolted onto the advisory layer, D8/D9 break and the audit trail loses its deterministic anchor.

- **Home:** `api/services/finance/financial_state.py`, a pure function `classify_financial_state(cashflow: MonthlyCashflow, net_worth, dti) -> FinancialState`, sitting next to `cashflow.py::MonthlyCashflow.gate_reason`/`reliable` (`api/services/finance/cashflow.py:98-115`), which is the existing deterministic-label precedent.
- **Inputs:** reuse `compute_monthly_cashflow` (`cashflow.py:188`) surplus/gate_reason, `get_net_worth` (B4), and debt minimums from `gather_affordability_inputs` (`affordability.py:242`). **No recomputation** — compose existing outputs.
- **Enum is frozen here** before any principle is tagged against it (P10 §2). Principles reference these labels only.

> **Resolved (O1, 2026-07-01):** the matcher runs on `financial_state` **alone** in v1. `money_archetype` + `risk_signal` are **optional graceful-degradation modifiers** — absent → the same principles fire, only the ranking loses one input (falls back to `centrality` + match strength). Rationale: archetype is a ranking modifier never a selector (D9); the Grable-Lytton signal only exists after the Capa 3 questionnaire, so most Capa 1/2 users won't have it — gating on it would starve the library. Wire the P6c producers as optional modifiers if they already exist (`get_user_context` is flag-gated OFF today, `api/config.py:35`); otherwise ship on `financial_state` and add them in a later increment. **B7 is not gated on this.**

---

## 3. Architecture — where advisory mode lives

Advisory mode is **not a new pipeline or transport**. It is a **persona + toolset variant of the existing read-only query dispatcher**. Activation is **intent-per-message as the spine, with a short-lived idle-expiring continuity session on top — NOT a hard sticky mode** (D12/O5): a planning/advisory question is advisory-framed; a transactional query (`¿cuánto tengo?`) always gets a plain answer, even mid-session. The write/capture path is untouched, so "LLM never writes" holds by construction.

```
message (Telegram OR native chat)
  → bot/pipeline.py::process_message()            # unchanged deterministic gauntlet
      → command short-circuit: /asesor //normal    # NEW (pipeline.py:304-346)
      → extractor → ExtractionResult.dispatcher
          ├ write/control → deterministic write dispatcher   # unchanged
          └ query        → _route_extraction (pipeline.py:950)
                              → advisory flag set?  ──no──► run_dispatch   (today's path)
                                                    └─yes─► run_dispatch(advisory=True)   [A]
                                                             ...later hardened to...
                                                            run_advisory(...)             [C]
```

- **Option A (v1):** `run_dispatch` (`app/queries/dispatcher.py:198`) receives an `advisory` signal (from the per-turn resolver, B2) and, when advisory, (a) swaps to an advisory system prompt, (b) exposes the advisory tool allowlist, (c) raises the iteration cap. The agentic loop (`app/queries/llm_client.py:95`) is unchanged; tools still delegate to the deterministic engines.
- **Option C (later, B11):** a sibling `run_advisory` freezes a deterministic context-pack of `Finding{value, source_engine, label}`, then narrates with **no DB tools** + a Gate-D scorer + template fallback. Same toggle/routing.

---

## 4. The principle library

### 4.1 Storage (Gate A) — `api/data/principle_library_cr.py`

Follow the existing curated-static-data precedent (`api/data/__init__.py:1-7`: *"a literal Python dict is the simpler choice while the list is small and reviewed in PRs"*). Copy the `bank_directory_cr.py:13-69` shape (TypedDict row + module-level list + linear-scan matchers). **Not a DB table.** Switch to YAML-at-boot only if the corpus outgrows PR review.

```python
# api/data/principle_library_cr.py
class Principle(TypedDict):
    principle_id: str
    source: dict          # {title, author, locator}                         — human
    centrality: str       # core | supporting | peripheral                    — LLM-proposed
    core_idea: str        # abstracted principle, our words (NOT the anecdote) — reshaped
    source_illustration: str | None   # book anecdote — AUDIT ONLY, never narrated (copyright)
    mechanism: dict       # = behavioral_mechanisms (direct)
    applies_when: dict    # {financial_state:[...], money_archetype:[...], risk_signal:[...]} — LLM-proposes/human-confirms
    framing_template: str # tone-neutral framing INTENT + {slots}; NOT final copy (voseo at Gate C) — reframed
    hope_anchor: str      # grounded-encouragement rationale + when-not-to-use — human
    requires_positive_state: bool     # L8 machine-readable guardrail          — human
    forbidden_when: list  # financial_state labels where this must NOT fire    — human
    excluded_tactics: list            # = tactic_excluded (guardrail input)     — direct
    cultural_flags: dict | None       # CR localization (already populated)     — direct
    scope: str            # coaching | clinical_boundary                       — human
    provenance: dict      # {distilled_by, reviewed_by, localized}             — pipeline + human

PRINCIPLES_CR: list[Principle] = [ ... ]   # only REVIEWED records live here
```

### 4.2 Import mapping (Gate E / §4) — `scripts/import_principles.py`

Maps the raw NotebookLM export into the runtime schema. Three buckets; the third is human-only.

| Raw field (your export) | → Runtime | Bucket | Action |
|---|---|---|---|
| `principle_id` | `principle_id` | direct | copy |
| `behavioral_mechanisms` | `mechanism` | direct | copy (dict retained) |
| `cultural_flags` | `cultural_flags` | direct (already localized) | copy — the valuable L4 work |
| `tactic_excluded` | `excluded_tactics` (+ derive `forbidden_when`) | direct (guardrail) | copy; split US-specific vs numeric-tactic. **Exempt from the no-number scorer** (legitimately holds "10%", "6 meses", "401(k)"). |
| `analytical_synthesis` + `strategic_context` | `core_idea` (+ `source_illustration`) | reshape | abstract to the principle; move anecdotes (Read/Fuscone, gold coins) into `source_illustration`, kept OUT of narration |
| `framing_template` | `framing_template` | reframe | rewrite from final *usted* copy → tone-neutral framing **intent**; voseo deferred to Gate C |
| `transition` | — | drop | severs the narrative arc; verify `core_idea` stands alone |
| *(absent)* | `source` {title, author, locator} | add — human | clearly Housel *Psychology of Money*; structure it |
| *(absent)* | `applies_when` | add — LLM proposes, human confirms | **biggest gap.** `financial_state` dominates (D9); freeze the enum first (§2) |
| *(absent)* | `centrality` | add — LLM proposes | core/supporting/peripheral |
| *(absent)* | `hope_anchor`, `scope`, `requires_positive_state`, `forbidden_when` | add — human ONLY | safety fields; an LLM guessing these = the exact thing this architecture avoids |
| *(absent)* | `provenance` {distilled_by, reviewed_by, localized} | add — pipeline + human | `reviewed_by` gates selection |

**`framing_template` reframe — worked example** (your `validacion_logica_individual_historica`):
- *Raw (final usted copy):* "Honro la sabiduría de supervivencia que desarrollaste al vivir {evento_economico}; esa precaución te protegió entonces y es perfectamente lógica…"
- *Reframed (instruction to narrator):* "Validá la lógica histórica detrás de su aversión al riesgo: nombrá el `{evento_economico}` que vivió, reconocé que esa precaución lo protegió y es razonable, antes de proponer cualquier ajuste. Voseo, sin cifras."

**Pipeline invariants (Gate E):**
- Additive to `scripts/principles/staging.json`, keyed by `(principle_id, source)`. **Never overwrites a record already in the live module** (re-distillation logs "already reviewed").
- Human promotes staging → `PRINCIPLES_CR` by hand, filling the human-only fields, in a PR (the PR *is* the review).
- **Inert-until-reviewed is free:** an un-reviewed principle simply isn't in the live module, so the matcher can't select it.

**Starter `applies_when` for your 5 (LLM-proposed → operator confirms, D9):**
- `primacia_comportamiento_disciplinado` → `[stable_building, recent_overspend]`; archetype `money_status, money_worship`; core.
- `vulnerabilidad_del_intelecto_sin_control` → `[stable_building]`, risk `high_tolerance`; **`forbidden_when: [negative_surplus, irregular_income_stress]`**.
- `validacion_logica_individual_historica` → broad; risk `low_tolerance`; archetype `money_vigilance, money_avoidance`; core.
- `paradoja_esperanza_loteria` → `[irregular_income_stress, negative_surplus, first_time_saving]`; archetype `money_avoidance`; `requires_positive_state:false` (this one is *for* bad states).
- `inexperiencia_evolutiva_modernidad` → `[first_time_saving]`, intent `plan_retirement`; `excluded_tactics` 401k/Roth → never surfaced; core for Q2.

### 4.3 Matcher (Gate B) — deterministic, in the module

`match_principles(financial_state, gate_reason, archetype, risk_signal, intent) -> list[Principle]`:
1. **Filter:** `financial_state ∈ applies_when.financial_state` AND `∉ forbidden_when` AND `scope != clinical_boundary` AND (`requires_positive_state ⟹ state supports it`).
2. **Rank:** `centrality` + match strength. **Archetype only re-weights** (D9 — never selects alone; below the behavioral signal).
3. **top-k = 2** (do not flood).
4. **Tie-break = the Gate-D guardrail anchored to `financial_state`** (D6), so "celebrá la victoria" never beats "confrontá la deuda" on bad numbers.

### 4.4 Narration integration

The *rule* is static; the *matched* principles are dynamic per-user (so they can NOT live in the cache-locked, per-user-number-forbidden system prompt):
- **Static** `_PRINCIPLE_FRAMING` constant in `app/queries/prompts/system.py` (added to `sections` at `:325-344`, after `_CONVENTIONS`): the contract only — "frame with the given principle; numbers come only from tools; anchor hope in the real gap; never shame." Number-free, brace-free.
- **Dynamic (Option A):** a read-only tool `get_framing_principles(financial_state, archetype)` returns the matched `{principle_id, framing_template, source}` (never a number). The LLM weaves the deterministic findings into that frame.
- **Dynamic (Option C, B11):** the matched principles become a `framing` field on the frozen context-pack; the no-tools narrator uses them directly.

---

## 5. New assessment tools (all read-only, own session, delegate to engines)

Each mirrors the existing `assess_purchase`/`assess_financing` pattern (`app/queries/tools/affordability.py:103`, `financing.py:62`) and best-effort `record_advice_event`. **Reused verbatim:** `compute_account_balances` (`accounts.py:91`), `assess_for_user`/`assess_affordability` (`affordability.py:145,317`), `compute_monthly_cashflow` (`cashflow.py:188`), `compute_envelope_summary` + reallocation (`envelopes.py:563`), `assess_financing`, `get_savings_capacity`, `get_card_analysis`, `suggest_reallocation_candidates`, `get_user_context`.

| New tool | Delegates to | Answers | Note |
|---|---|---|---|
| `get_net_worth` | `compute_account_balances` (assets) − `Debt.current_balance` + card owed (liabilities) | Q1, Q3 | Pure summation. **Per-currency, USD shown apart** — never a single ₡+$ number on the fixed ₡500 `fx.convert` placeholder (D3 no-placeholder-fx rule). |
| `assess_counterfactual` | affordability's already-returned `shortfall` / `min_timeline_months_feasible` / `max_amount_feasible_in_timeline` (`affordability.py:216`) + `suggest_reallocation_candidates` | Q4 | Near-free. Levers = wait longer / buy cheaper / raise surplus / cut envelope X. |
| `build_multiyear_plan` | `assess_affordability` at a long horizon + `assess_financing` for the mortgage cuota | Q1 | Composition, no new engine. **Linear — no inflation/compounding in v1** (state it). |
| `project_retirement` | **NEW** `app/domain/retirement/cr_pension.py` | Q2 | Returns **3 SUPEN scenario bands** (pesimista/esperado/optimista at 65), not a single number — see B6 ⛳. |

---

## 6. Guardrails (Gate C narration + Gate D safety)

- **No-number-in-principle (CI, D5):** a scorer over `framing_template` + `core_idea` + rendered narration; **excludes `excluded_tactics`**. CR-aware extractor (`₡`, `%`, "millones", "N meses", ROP factors). *All 5 of your current `framing_template`s are already number-free.*
- **Slot-fill faithfulness (O4):** `framing_template` slots (e.g. `{evento_economico}`) fill **known → deterministically** from structured context (`user_insights`, `cultural_flags`, declared goals); **said-but-unstored → the LLM may fill ONLY from something the user explicitly said in-conversation, never invented**. Every filled slot value must trace to a user statement or a stored insight (a faithfulness check) and passes the number-free scorer. Hard-block: narrating grounding the user never gave (e.g. "durante la crisis de 2008" when unmentioned). Slots are framing color (HOW), never a figure or verdict.
- **Human-review gate (CI, Gate E):** a test that fails merge if any live record has null `source`/`reviewed_by`/`scope`/`applies_when`/`forbidden_when`.
- **Distress hand-off — TWO tiers (D7, O3):** a deterministic net (`api/services/advisory/guardrails.py`) runs pre-LLM. **Tier 1 (financial distress** — underwater, hopeless about debt, no crisis signal): non-clinical warmth + point to a real person (a trusted one, an OPC-accredited asesor, their asociación solidarista) — do NOT escalate ordinary money-stress to a suicide line. **Tier 2 (genuine crisis** — hopelessness, self-harm ideation): warm hand-off to **línea 1322** (24/7, also reachable via 911) as primary + **9-1-1** for imminent danger (Aquí Estoy 800-2737869 is secondary only — M–F from 2pm, cannot be the sole pointer). The bot must **not assess, diagnose, or promise confidentiality**, and this routes through whatever crisis handling the bot already has — not a second mechanism. Tier-2 draft copy (review with clinical input before shipping):
  > `// UX-COPY (es-CR, voseo) — Tier 2`
  > "Lo que me estás contando suena muy pesado, y no tenés que cargarlo solo. Yo te acompaño con lo financiero, pero para esto hay gente preparada para escucharte ahora mismo: en Costa Rica podés llamar al 1322, que atiende las 24 horas, o al 9-1-1 si es una emergencia. Cuando te sientas listo seguimos con tus finanzas, sin apuro."
- **Toxic-positivity ban (D6):** prompt rule + on infeasible results always return the deterministic counterfactual levers, never "todo va a estar bien".
- **gate_reason honesty:** inherit the existing rule — if a tool returns `no_income`/`no_budget`/`under_coverage`, do NOT assert a surplus or verdict; ask for the specific corrective action.
- **Audit spine:** add `"advisory_assessment"` to `KNOWN_KINDS` (`api/services/advice_trace.py:41-49`) and log `record_advice_event(inputs={state, matched_principle_ids}, result={...})`. **No migration** — `kind` is un-CHECK-constrained, `inputs`/`result` are JSONB, and `record_advice_event` opens its own session + swallows on failure so it can never break the answer.

---

## 7. Implementation blocks

### B0 — Bring the artifacts into the repo (no code)
- Commit the P10 decision doc into `docs/` and this plan (git: root `.gitignore` blocks `docs/**` → `git add -f`).
- Drop the raw NotebookLM export at `scripts/principles/raw/`.
- **Done-when:** doc + raw corpus are version-controlled.

### B0.5 — Chat robustness hardening (prerequisite / parallel to B0–B2 — see §13)
- Close the compound-intent failure class (2026-07-02 bug) BEFORE advisory ships, because advisory questions are compound by nature. Implements R1–R6 in §13: move `_build_response` inside the endpoint guard + a chat-route exception handler (R1); symmetric write/control error net (R2); graceful re-route instead of the `dispatch()` RuntimeError (R3); extractor `intent`↔`dispatcher` `model_validator` + compound-message prompt guidance (R4); `error_class` on the chat response + distinct client copy (R5); failure-class logging (R6).
- **Done-when:** the verbatim compound message returns an answer or clarification (never a non-2xx, never a bare banner); the four failure classes render distinctly; §13 checks C1–C5 green.

### B1 — `financial_state` label owner (§2) ⛳ prerequisite
- `api/services/finance/financial_state.py::classify_financial_state`, pure, composes existing engine outputs. Freeze the enum.
- **Done-when:** unit tests over each label from fixture cashflow/net-worth inputs; nothing recomputes surplus.

### B2 — Advisory activation (intent-per-message + short-lived session, D12)
- **Per-turn resolver** `advisory_this_turn(user, extraction, redis) -> bool`: true iff (advisory continuity session active **OR** the message is an advisory/planning intent) **AND** it is not a transactional-query override (balance/spend/list lookups always get a plain answer). This is the spine; the "mode" is per-turn routing.
- **Continuity session (optional, on top):** `bot/redis_keys.py::advisory_session_key(user_id)` = `telegram:advisory_session:{user_id}`, **idle-expiring** — TTL (~2–3h) **refreshed on each advisory turn**, so it lapses back to normal on its own. No persist-until-`/normal`.
- **Explicit entry/exit:** `/asesor` (alias `/asesoria`) starts a session; `/normal` (`/salir_asesor`) ends it — deterministic short-circuits in the pre-LLM command block (`bot/pipeline.py:304-346`), zero LLM. Native: a header **"Modo asesor"** control → `POST /api/v1/chat/advisory-session {active}` (NOT the one-shot chip pattern at `Chat.tsx:621`); the button reflects session state on load.
- **Advisory-intent detection:** the extractor already emits `dispatcher="query"`; add a lightweight advisory-vs-transactional sub-signal (a planning / what-if / should-I question → advisory). Keep it deterministic-leaning; when uncertain, default to transactional (least surprising).
- **Overrides:** a transactional intent mid-session still returns a plain answer (the session never forces coach-framing onto a factual lookup). `/cancel` + `POST /chat/reset` clear the session too.
- **Done-when:** a planning question is advisory-framed with or without a session; a `¿cuánto tengo?` is plain even mid-session; the session lapses on idle; both channels share `advisory_session_key`.

### B3 — Advisory persona + prompt variant + cap + tool scoping  *(ships Q3 + Q4)*
- `build_system_prompt(user, now, advisory: bool)` (`system.py:307`): advisory branch prepends `_ADVISORY_PERSONA` + `_PRINCIPLE_FRAMING` (static, number-free, brace-free), keeping `_RULES`/`_CONVENTIONS`/`_date_block`. Bump `MAX_PROMPT_CHARS` (`tests/test_phase_6c_b9_system_prompt.py:32`) deliberately.
- `api/config.py`: `advisory_persona_enabled: bool = False` + `principle_library_path: str = ""`.
- Cap: `settings.llm_advisory_iteration_cap = 8`, passed at `dispatcher.py:262-271` (already a per-call param).
- **Tool scoping (the global-registry trap):** register everything at import as today, but pass an EXPLICIT `ADVISORY_TOOLSET` allowlist to `run_query_loop(tools=…)`; `BASE_TOOLSET` = today's set. Add a test asserting the **normal-mode tool list is byte-identical**.
- **Done-when:** advisory mode answers Q3 (`assess_financing`) and Q4 (`assess_purchase` counterfactual fields) end-to-end; normal-mode prompt + tool list unchanged (cache intact).

### B4 — `get_net_worth`
- Delegates only; per-currency, USD apart (D3). Advice-traced.
- **Done-when:** matches the dashboard `_balance_split` assets and never emits a ₡+$ single number.

### B5 — `assess_counterfactual` + `build_multiyear_plan`  *(completes Q1 + Q4)*
- Compose affordability's existing levers + `suggest_reallocation_candidates` + `assess_financing`. No new engine.
- **Done-when:** Q1 (house) and Q4 (Europa "si no, ¿qué necesitaría?") produce concrete deterministic levers.

### B6 — Retirement engine ⛳ *(completes Q2 — highest risk; O2)*
- **NEW** pure `app/domain/retirement/cr_pension.py` (mirrors `app/domain/payroll`). **Encode SUPEN's mandated projection methodology, NOT an invented one:** a **scenario-banded** projection at retirement age **65** under **three scenarios — pesimista / esperado / optimista** (SUPEN requires operators to send affiliates with ≥5 years in an operator a projected pension under exactly these three bands). Output is a **range with explicit uncertainty**, never a single confident number.
- **Safe-to-encode constants (stable):** ROP contribution = **4.25%** of CCSS-reported salary (of which **1%** is the worker's); retirement age **65**; ROP benefit floor = **≥20% of the IVM minimum pension**.
- **Provisional-and-versioned (load-bearing, NOT hygiene):** the IVM replacement ratio + fund returns are **mid-reform** (CCSS proposing to cut the IVM replacement-ratio ceiling to ~43%, leaning on the 3 pillars for the ~60% international standard; generational-funds reform approved Dec-2023 but Conassif postponed it 12 months in Mar-2025; Q1-2025 operator returns fell sharply, e.g. Popular Pensiones 12.49%→8.01%, BN Vital 10.53%→6.62%). So the **version field + effective dates do real work** — a v1 constant is stale within a year. Mark returns/replacement-ratio assumptions **provisional**; `unconfigured year → error` (mirror `payroll/rates.py`).
- **Narration guardrail:** `project_retirement` returns the 3 bands; the LLM narrates a **range and its uncertainty** — a single confident retirement figure would violate the hope-anchor guardrail (D6, mirror-not-oracle). Delegates only; LLM never computes.
- **⛳ Operator-validated against SUPEN's published methodology + golden tests before user-visible.**
- **Done-when:** 3-scenario golden fixtures pass; narration surfaces a band + uncertainty; operator signs off on the constants + methodology.

### B7 — Memory in advisory mode
- Enable a memory read path so `get_user_context` (P6c insights, `user_context.py:35`) shapes narration (HOW, never numbers). Resolve O1 (archetype/risk producers) here.
- **Done-when:** an insight can re-rank principles (D9) but never surfaces as a number.

### B8 — Principle library corpus + import (§4)
- `api/data/principle_library_cr.py` (schema §4.1 + `match_principles` §4.3) + `scripts/import_principles.py` (§4.2). Hand-complete the 5 records in a PR (Gate E).
- **Done-when:** the 5 principles are reviewed + importable; the matcher returns them for the right `financial_state`.

### B9 — Narration integration + CI scorers
- `get_framing_principles` tool (§4.4) added to `ADVISORY_TOOLSET`. No-number-in-principle scorer + human-review completeness gate (§6) wired into CI.
- **Done-when:** an advisory answer weaves real findings into a matched principle's frame; both CI gates are green and block a bad merge.

### B10 — Safety guardrails
- Distress detector + warm hand-off, toxic-positivity ban, gate_reason honesty, `advisory_assessment` advice-trace kind (§6).
- **Done-when:** the distress fixture routes to hand-off; a genuinely bad state never yields manufactured optimism.

### B11 — Harden to Option C
- `app/queries/advisory/orchestrator.py::run_advisory`: frozen deterministic context-pack (`Finding{value, source_engine, label}`) → matched principles as a `framing` field → narration LLM with **NO DB tools** → Gate-D scorer (every ₡/%/month token must exist in the pack) → template fallback → one `advice_events` row. Route to it from the same flag.
- **Done-when:** "no invented number" is structural (narrator has no tools); a guardrail miss degrades to honest numbers, never a fabricated verdict.

### B12 — MCP adapter *(deferred; documented only)*
- Wrap the SAME `run_advisory`/tools behind a thin MCP adapter via the **in-process MCP-client route** (preserves the budget gate + `llm_query_dispatches`/`advice_events` choke points). Build only when an external client is a concrete requirement. Do NOT use the Anthropic MCP-connector route (bypasses audit).

---

## 8. Done-when (feature)

- A user in a known state (e.g. `negative_surplus` + `money_avoidance`) gets a deterministic verdict narrated with a matched, source-attributed framing principle, es-CR voseo, **zero fabricated numbers**.
- **Toxic-positivity fixture:** a genuinely bad state never yields manufactured optimism.
- **Distress fixture:** a distress signal routes to warm human-referral copy, not clinical advice.
- **Provenance fixture:** every live principle traces to source + reviewer; an un-reviewed record cannot be selected.
- **No-duplication:** the advisory layer reads the deterministic result and never recomputes available funds.
- **No-number-in-principle scorer (CI):** no `framing_template` / narrated principle contains or implies a figure.
- All four flagship questions (house, retirement, finance-car, Europa+counterfactual) answered with numbers that match the home tab.

---

## 9. Out of scope (v1)

- MCP server / external transport (B12 deferred; tools are wrappable later).
- RAG / vector search over raw books.
- Auto-ingesting a book into the live corpus without human review.
- Any financial computation, product recommendation, or numeric tactic inside the principle layer.
- Clinical/therapeutic intervention.
- Inflation/compounding in `build_multiyear_plan` (linear v1).

---

## 10. Watchouts (codebase-specific)

- **Global tool registry** (`base.py:22`, `list_tools_for_anthropic` returns ALL): per-mode scoping must be an explicit allowlist to `run_query_loop(tools=…)`, with a byte-identical normal-mode test — or advisory tools leak into normal mode and fragment the prompt cache.
- **`financial_state` doesn't exist** — build it in the P7 layer (B1), not bolted onto advisory, or D8/D9 break.
- **Iteration cap vs budget:** advisory cap = 8 fights `assert_within_budget` (`dispatcher.py:235`) per-user daily token budget; size empirically, keep prompt caching intact.
- **`KNOWN_KINDS`:** add `"advisory_assessment"` (`advice_trace.py:41`) or the audit row silently no-ops (it swallows on fail).
- **No-placeholder-FX (D3):** net worth + any cross-currency roll-up stays per-currency, USD apart (fixed ₡500 placeholder).
- **`get_user_context` is flag-gated OFF** — advisory memory needs a deliberate enable path; the no-number scorer must ensure an insight string is never surfaced as a number.
- **Retirement CCSS/ROP math is policy-sensitive** — versioned constants (unconfigured year → error), golden tests, operator validation before user-visible.
- **System-prompt tests** (`test_phase_6c_b9_system_prompt.py`): the advisory block must be number-free + brace-free, and only the first name may vary per user; bump `MAX_PROMPT_CHARS` deliberately.
- **Mobile chips are one-shot** (`usedChips`, `Chat.tsx`) — the toggle needs new persistent header UI, not a chip.
- **No sticky-mode footgun (D12):** advisory is per-turn + an idle-expiring session, and a transactional query always overrides — so a factual "¿cuánto gasté ayer?" never gets coach framing. Do NOT reintroduce a persist-until-`/normal` global flag.
- **Asymmetric error nets + out-of-net responses (§13/D13):** the query branch of `_route_extraction` is netted but the write/control branch is NOT, and `_build_response` sits outside the chat endpoint's try — so a mixed-intent message can 500. The mobile "Hubo un error" banner is a **client** `onError` (non-2xx), NOT the server's 200 fallback — don't conflate them. Enforce `intent`↔`dispatcher` consistency at the extractor so a compound message can't reach `dispatch()` mis-tagged.

---

## 11. Resolved decisions (operator, 2026-07-01)

- **O1 → §2/B7:** matcher runs on `financial_state` alone in v1; `archetype`+`risk_signal` are optional graceful-degradation modifiers (absent → fall back to `centrality` + match strength). Wire P6c producers if present, else a later increment. B7 not gated.
- **O2 → B6:** encode **SUPEN's scenario-banded methodology** (pesimista/esperado/optimista at 65); stable constants (ROP 4.25% / 1% worker / age 65 / floor ≥20% of IVM min); returns + replacement-ratio are **mid-reform → provisional + versioned** (effective dates load-bearing); narrate a range with uncertainty, never a single figure. ⛳ operator-validated.
- **O3 → §6/B10:** two-tier hand-off — Tier 1 financial distress → real person (trusted / OPC asesor / asociación solidarista); Tier 2 crisis → 1322 (24/7, via 911) + 9-1-1, routed through existing crisis handling; bot never assesses/diagnoses/promises confidentiality.
- **O4 → §6:** slot fill known→deterministic, said-but-unstored→LLM from conversation only, never invented; faithfulness check on every slot value.
- **O5 → §3/B2:** no hard sticky mode — intent-per-message spine + a short-lived idle-expiring continuity session; transactional intent always overrides. (D12.)

*Remaining for later increments (not blocking):* whether the P6c archetype/risk producers exist (O1 wiring only), and a clinical review of the Tier-2 hand-off copy before it ships.

---

## 12. Gate → P10 mapping

| P10 gate | Realized in |
|---|---|
| Gate A (storage/versioning) | §4.1 + B8 (Python module, PR = review) |
| Gate B (matching/selection) | §4.3 + B8 |
| Gate C (narration contract) | §4.4 + B3 (persona) + B11 (frozen pack) |
| Gate D (hope/safety, enforced) | §6 + B10 |
| Gate E (curation/provenance/import) | §4.2 + B8 + the CI completeness gate |

---

## 13. Chat robustness — compound-intent + failure-class transparency (2026-07-02 bug)

**Trigger:** a compound message — `"Ganó 2000000 al mes analizas mis expenses deudas y saldos de crédito dime si me alcanza con mi salario"` (an income/registration signal + a multi-part analytical/affordability query) — produced the mobile banner "Hubo un error. Intentá de nuevo." Advisory questions are compound by nature, so this class is a **prerequisite** (B0.5), not a nice-to-have.

### Diagnosis (verified in code, 2026-07-02)
- **"Hubo un error. Intentá de nuevo." is the mobile client's `onError` banner** (`mobile/src/screens/Chat.tsx:326`), fired **only on a non-2xx / network failure**. The server's own generic error (`CHAT_UNEXPECTED_ERROR`, `bot/messages_es.py:550`, "Se me complicó procesar eso…") returns **HTTP 200** and renders via `onSuccess`. So the observed banner means a **real non-2xx escaped the server**.
- **Defect A (matches the symptom):** a non-2xx escaped the endpoint guard (`api/routers/chat.py:96-110`). `_build_response(reply)` at `:110` is **outside** the try; FastAPI `response_model` validation of `ChatMessageResponse` runs post-return (uncatchable in-handler); an `HTTPException` is re-raised (`:105-106`). Any → a 500 the client shows as the banner.
- **Defect B (real, adjacent, currently masked):** the write/control branch of `_route_extraction` (`bot/pipeline.py:984-989`) is **unwrapped**, unlike the netted query branch (`:967-975`); and `dispatch()` raises a bare `RuntimeError` (`api/services/telegram_dispatcher.py:309`) when `intent=QUERY` reaches the write path. The extractor schema does **not** couple `intent`↔`dispatcher` (`api/services/llm_extractor/schema.py:59-60`, no `model_validator`) and the prompt gives **no** compound-message guidance — so a mixed message can yield `intent=QUERY, dispatcher="write"` → the RuntimeError (today a 200 "Se me complicó…", still a bug).
- **Root class:** compound/mixed-intent messages are under-determined; the write path has thinner nets than the query path; one client banner masks all failure classes.
- **Disambiguator to pull from a real occurrence:** the HTTP status; which string rendered ("Se me complicó…" = handled 200 / Defect B vs the literal banner = non-2xx / Defect A); whether `chat.py:108` `log.exception` is present (absent ⇒ out-of-net throw); the `llm_extractions` row's `dispatcher`/`intent`/`confidence`.

### Checks (add as tests/CI)
- **C1 — no non-2xx on a well-formed message:** fixture the verbatim compound message + variants → assert HTTP 200 + a graceful body, never a 5xx.
- **C2 — intent↔dispatcher consistency:** property test that `intent=QUERY ⟹ dispatcher="query"` (and write intents ⟹ `"write"`); enforced by a `model_validator`.
- **C3 — symmetric nets:** the write/control branch of `_route_extraction` cannot propagate an exception out of `process_message` (parallels `tests/test_query_robustness.py` for the query branch).
- **C4 — response-build safety:** a `BotReply` with every optional field populated (buttons/url_buttons/open_screen/prefill) serializes to `ChatMessageResponse` without error; malformed → graceful, not 500.
- **C5 — failure-class distinctness:** the four classes (understanding / budget / transient-system / hard-system) map to four distinct user-facing strings + a machine-readable `error_class`.

### Remediation (R1–R6)
- **R1** — move `_build_response(reply)` inside the endpoint try + a chat-route exception handler mapping `ResponseValidationError`/`RequestValidationError`/unhandled → a graceful **200** chat body (Defect A).
- **R2** — wrap the write/control branch of `_route_extraction` symmetrically with the query branch → a graceful message (outermost net stays last resort) (Defect B escape).
- **R3** — replace the `dispatch()` `RuntimeError` (intent=QUERY on the write path) with a **graceful re-route to the query dispatcher** (or a clarification) — preferring the analytical path on a compound message is correct behavior.
- **R4** — extractor: (a) a `model_validator` on `ExtractionResult` forcing `intent`↔`dispatcher` consistency; (b) prompt guidance for compound messages — mixed write+query → prefer the **query/advisory** classification (the analytical verb dominates), surface the income as an optional follow-up ("¿querés que registre ese ingreso?"). Deepest fix — prevents the mismatch at the source.
- **R5** — failure-class transparency: add `error_class` to `ChatMessageResponse` ({understanding | budget | transient | system | none}); the client renders distinct copy per class (network vs "no entendí, reformulá" vs "algo se rompió, reintentá") instead of one banner. Keep the existing distinct server strings (`EXTRACTOR_FAILED` = understanding vs `CHAT_UNEXPECTED_ERROR` = system).
- **R6** — log `{dispatcher, intent, confidence, message_hash, failure_class}` at every net, incl. the out-of-net paths (response build / deps / validation).

### Advisory-mode interaction
R3 + R4 are exactly the routing advisory mode wants: `"Ganó X … dime si me alcanza"` should reach the **advisory affordability path** (assess_purchase / net worth / debts / credit), not the write dispatcher — with the income mention offered as a follow-up, never a hard error.

### Done-when
The verbatim compound message returns an advisory/affordability answer or a clarification (never a non-2xx, never a bare banner); the four failure classes render distinctly; C1–C5 green.
