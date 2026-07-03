# P10 — Knowledge Base / Principle Library (component of Capa de Asesoría)

> **Status:** 🔒 DECISIONS LOCKED — PLAN FIRST. Resolve the 5 gates, stop, await approval per gate. No code until approved.
> **Parent:** `phase-10-advisory-decisions.md` (v2). This is a component *inside* P10; it does not change the Capa 1/2/3 risk decomposition.
> **Scope of this doc:** how the curated behavioral-finance principle corpus is stored, matched, narrated, guard-railed, and **how the existing curated JSONs are imported.**
> **Language split:** doc body English; user-facing copy `// UX-COPY (es-CR, voseo)`.

---

## 0. The one principle that orders the whole design

Two kinds of knowledge, never mixed:
1. **The user's financial reality** (balances, debts, surplus, cuotas) — exact, quantitative, 100% deterministic. Principle-matching never reads or produces a number.
2. **Advisory knowledge from books** (how to reframe scarcity, how to talk to someone underwater without shaming, what gives psychological momentum) — qualitative: principles, frames, ways of saying things.

Extension of the core invariant: **rules decide WHAT** (numbers, debt order, verdict); **the library shapes HOW** it is narrated (tone, framing, psychological approach). The library shapes the *how*, never the *what*.

---

## 1. LOCKED DECISIONS (do not re-litigate)

### L1 — Curated principle library, NOT RAG, for v1.
Books are distilled into structured principle records (schema §3). Deterministic code matches user state → applicable principles; the LLM narrates with that framing. Books are research input to *build* the library, not a runtime dependency.
**Steelman of vector/RAG, on record:** scales without manual curation, captures nuance. **Rejected for v1** because: (a) un-curated passages risk surfacing US-specific tactics (401k/Roth) invalid in CR; (b) auditability — advice must trace to rule + cited source; (c) embedding copyrighted book text verbatim into a commercial product is legal exposure; (d) the append-only ledger's stale-index problem. If embeddings are ever added, they run only over the already-vetted corpus, never raw books.

### L2 — Library shapes narration, never computation.
No principle, match, or template may alter, produce, or imply a financial figure. Advisory math stays in its single source of truth (Capa 1/2/3 + P7). This layer consumes the result.

### L3 — Hope anchored in real numbers; never toxic positivity.
When the data is genuinely bad, the goal is honest-but-compassionate framing, not false optimism. A principle may never manufacture optimism the numbers don't support. Enforced as a guardrail, not a guideline (Gate D).

### L4 — Localization, not translation.
Source literature is mostly US/individualist. CR financial life (solidarismo, aguinaldo/salario escolar cycles, family obligations, JPS, CCSS→LPT/ROP transition) differs. Curation **localizes** principles; it does not translate them.

### L5 — Coaching/education, not therapy or licensed advice.
Principles touching trauma/clinical territory are flagged; on real distress signals the agent frames with warmth and routes to a human/professional resource — it does not perform clinical intervention. Consistent with counsel: recommendations yes, no autonomous orders, no guarantees.

### L6 — Human review gate.
No distilled principle enters the live corpus without human review + CR localization pass. NotebookLM (or any tool) proposes; a person approves.

### L7 — Integrates with P6c and P7 without duplication.
Triggers reference `user_insights` (P6c) + money archetype + Grable-Lytton signal + deterministic financial state. **Available-funds computation is never re-done here.**

### L8 — Guardrails are machine-readable, not prose. (schema fix)
Gate D is "enforced, not advisory." Therefore the "when NOT to use" cannot live only as prose in `hope_anchor`; it must exist as machine-readable fields the matcher and the guardrail pass can evaluate: `requires_positive_state`, `forbidden_when`, `scope`. Prose cannot gate code.

### L9 — Archetype is a ranking modifier, never a selector. (consistency with parent D7.1)
`applies_when.money_archetype` must not, alone, select a principle. The deterministic `financial_state` (from real data) dominates the match; the archetype only re-weights ranking among already-matched principles, and always weighs below the behavioral signal. This keeps the parent's D7.1 (archetype = soft overlay; KMSI weak factorial validity) intact. Because this layer affects narration only — not computation — a mis-tagged archetype mis-frames a message but never corrupts a number; still, it must not drive selection.

---

## 2. Single owner for `financial_state` (do this before import)

`financial_state` (e.g. `negative_surplus`, `high_interest_debt`, `irregular_income_stress`, `recent_overspend`, `first_time_saving`, `stable_building`) is a **deterministic label over the metrics-layer + P7 signals, with exactly one owner.** If the principle layer defines `high_interest_debt` differently than P7, there are two truths. Freeze this enum in the deterministic layer first; the library *consumes* labels, never computes them. **Principles cannot be tagged against a moving target — the enum is frozen before the import in §5 runs.**

---

## 3. Principle record schema (reconciled with existing JSONs)

Preserves the good fields already in the curated JSONs; adds the runtime machinery they lack.

```json
{
  "principle_id": "snake_case_slug",
  "source": { "title": "", "author": "", "locator": "chapter/section" },
  "centrality": "core | supporting | peripheral",
  "core_idea": "1-2 sentences, distilled in our own words (the abstracted principle, NOT the book's retold anecdote)",
  "source_illustration": "optional: the book's example, for audit/locator only; NEVER narrated as a product feature (copyright)",
  "mechanism": { "named_mechanism": "why it works psychologically" },
  "applies_when": {
    "financial_state": ["negative_surplus", "high_interest_debt", "irregular_income_stress", "..."],
    "money_archetype": ["money_avoidance | money_worship | money_status | money_vigilance | n/a"],
    "risk_signal": ["low_tolerance | high_tolerance | n/a"]
  },
  "framing_template": "tone-neutral framing INTENT with {slots}; an INSTRUCTION to the narrator, NOT final user copy (voseo applied at Gate C)",
  "hope_anchor": "how to use for honest grounded encouragement, in prose (human-facing rationale)",
  "requires_positive_state": false,
  "forbidden_when": ["financial_state labels where this principle must NOT fire"],
  "excluded_tactics": ["US-specific or numeric tactics this principle must never surface (from the old tactic_excluded)"],
  "cultural_flags": { "cr_flag": "CR-localized adaptation; already populated in existing JSONs" },
  "scope": "coaching | clinical_boundary",
  "provenance": { "distilled_by": "tool/date", "reviewed_by": "person/date", "localized": true }
}
```

Notes: `mechanism` keeps the existing rich dict form. `source_illustration` is new and exists to hold the book anecdote **out of** the narration path (audit only). `excluded_tactics` is the existing `tactic_excluded` promoted to a first-class guardrail input. `requires_positive_state`/`forbidden_when` are the L8 machine-readable guardrails.

---

## 4. Field mapping from the existing curated JSONs

Three buckets. The third is the safety-critical one: **a human fills it; an LLM may never auto-populate it.**

| Existing field | Target field | Bucket | Action |
|---|---|---|---|
| `principle_id` | `principle_id` | **direct** | copy (snake_case is fine) |
| `behavioral_mechanisms` (dict) | `mechanism` | **direct** | copy; dict form retained |
| `cultural_flags` (dict/null) | `cultural_flags` | **direct — already localized** | copy; this is the expensive human-judgment part, already done |
| `tactic_excluded` (array) | `excluded_tactics` (+ `forbidden_when`) | **direct (guardrail)** | copy; split US-specific vs numeric-tactic; numeric exclusions evidence the no-numbers discipline |
| `analytical_synthesis` + `strategic_context` | `core_idea` (+ `source_illustration`) | **reshape** | abstract to the principle; move retold anecdotes (Read/Fuscone, gold-coins) into `source_illustration`, kept out of narration |
| `framing_template` | `framing_template` | **reframe** | rewrite from final formal copy → tone-neutral framing *intent*; voseo deferred to Gate C |
| `transition` | — | **drop** | distillation connective tissue; sever the narrative coupling, verify `core_idea` stands alone |
| *(absent)* | `source` {title, author, locator} | **add — human** | clearly Housel *Psychology of Money*, but must be structured (provenance fixture) |
| *(absent)* | `applies_when` {financial_state, money_archetype, risk_signal} | **add — LLM proposes, human confirms** | biggest gap; freeze `financial_state` first (§2); archetype = modifier not selector (L9) |
| *(absent)* | `centrality` | **add — LLM proposes** | core/supporting/peripheral |
| *(absent)* | `hope_anchor` | **add — human** | the rationale + when-not-to-use intent |
| *(absent)* | `scope`, `requires_positive_state`, `forbidden_when` | **add — human ONLY** | safety fields; LLM guessing these = enforced guardrail depending on LLM inference (the thing the architecture exists to avoid) |
| *(absent)* | `provenance` {distilled_by, reviewed_by, localized} | **add — pipeline + human** | `reviewed_by` gates selection |

**Granularity note:** the `transition` field shows these 5 were distilled as a connected arc (temperament → intellect-without-control → individual logic → lottery/hope → we're all newbies). Content is atomic enough; just cut the `transition` coupling. If a future book is distilled as chapter-summaries rather than atomic principles, an **atomization** step (1 chapter → N principles) precedes mapping.

---

## 5. Pause-and-report gates

### Gate A — Storage & versioning of the corpus
**Decision: version-controlled file (YAML/JSON in repo, loaded at boot), not a principles table.** Rationale: the corpus is curated, low-cardinality, human-reviewed, changes by PR. Git gives diff + blame + the human-review gate for free (the PR *is* the Gate E review). It is product knowledge, not user data — it must not touch the append-only ledger or its stale-index problem. Cardinality is low enough that in-memory matching in code is trivial. Provenance/review status recorded as fields on each record (§3); a CI check blocks merge if a record lacks `reviewed_by` or has null guardrails.

### Gate B — Deterministic matching & selection
Define the matcher: user state (`financial_state` from the deterministic layer + `user_insights` + archetype + risk signal) → set of applicable principles → ranked, **top-k** selected (do not flood the user). Specify: ranking = centrality + match strength, **archetype as ranking modifier only (L9)**; de-duplication of overlapping principles; and **conflict resolution** — when two matched principles disagree (e.g. "celebrá pequeñas victorias" vs "confrontá el panorama completo de deuda"), the **Gate D guardrail anchored to financial state is the tie-breaker**, not arbitrary order. Integration: this is a *sibling selector* on the parent's context-pack assembler (component B); it adds a `principles[]` field to the context-pack. It does not re-resolve state and never recomputes available funds (L7).

### Gate C — Narration contract
This **is** the parent's component C, now with concrete shape:
```
{ findings: {numbers, verdict}, framing: { principles: [{id, framing_template, source}], guardrail_flags: {...} } } → one es-CR voseo message
```
Instruction to the LLM: narrate the findings using the templates as *tone intent*; never emit a number absent from `findings`; never emit framing text that *implies* a number; if a distress flag is set, use the hand-off copy. Prohibited-syntax rules live here (prohibit syntax — no asterisks/hashes — not categories). **Voseo localization is applied here**, not stored in the template.

### Gate D — Hope & safety guardrails (enforced, not advisory)
**Both pre- and post-narration.**
- **Pre-narration:** filter eligible principles — block `requires_positive_state` principles when the state doesn't support them; block `scope: clinical_boundary` principles from normal use (L8).
- **Post-narration:** a scorer verifies the output manufactured no optimism the state doesn't support and did not drift into clinical advice.
- **Distress path:** define distress signals + warm hand-off copy (`// UX-COPY (es-CR, voseo)`); this routes the same way the bot already handles crisis signals — not a second parallel mechanism. It is *financial-distress* framing with hand-off, never clinical intervention.
- Gate D is also the **tie-breaker for Gate B** conflicts.

### Gate E — Curation & provenance pipeline (includes the import)
Path: source book → NotebookLM distillation → **import/migration (§4) into staging** → human review (fills human-only fields, confirms LLM-proposed triggers, localizes) → CR localization pass → corpus. "Reviewed" requires: source attribution present, all guardrail fields non-null, `applies_when` confirmed, `reviewed_by` set. **The import is additive to staging and never overwrites reviewed records** (key by `principle_id` + source; re-distillation lands in staging, reviewed work is preserved). An un-reviewed/un-localized record **cannot be selected** by the matcher (inert by construction).

---

## 6. Done-when

- A user in a known state (e.g. `negative_surplus` + `money_avoidance`) produces a deterministic verdict narrated with a matched, source-attributed framing principle, in es-CR voseo, **zero fabricated numbers**.
- **Toxic-positivity fixture:** a genuinely bad state never yields manufactured optimism; output is honest-compassionate.
- **Distress fixture:** a distress signal routes to the warm human-referral copy, not clinical advice.
- **Provenance fixture:** every live principle traces to source + reviewer; an un-reviewed record cannot be selected.
- **No-duplication check:** this layer reads the advisory result and never recomputes available funds.
- **No-number-in-principle scorer (CI):** no `framing_template` or narrated principle text contains or implies a figure. Runs alongside the parent's number-match scorer (which checks that numbers trace to findings).

---

## 7. Out of scope (v1)

- Vector DB / RAG / embeddings over raw books.
- Auto-ingesting books into the live corpus without human review.
- Any financial computation, product recommendation, or numeric tactic in this layer.
- Clinical/therapeutic intervention.
