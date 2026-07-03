# Phase 10 — Capa de Asesoría (Advisory Mode) — Decisions

> **Status:** 🟠 STUB / RECONSTRUCTION — the original `phase-10-advisory-decisions.md` (v2)
> was never committed to the repo or the vault (verified 2026-07-01, re-verified 2026-07-03).
> This stub exists so the two surviving child documents have a resolvable parent and so the
> decision inventory is version-controlled. It records **only** what the surviving documents
> attest; it does not reconstruct the lost v2 prose. Do not treat gaps here as open questions —
> check the children first.
>
> **Children (canonical, both in this repo):**
> 1. `docs/advisory-mode-and-principle-library.md` — the implementation plan (B0–B12),
>    locked decisions D1–D13, operator resolutions O1–O5 (2026-07-01), and the
>    chat-robustness prerequisite (§13).
> 2. `docs/phase-10-principle-library.md` — the Principle Library component
>    (locked decisions L1–L9, gates A–E, record schema, import mapping).
> 3. `docs/phase-10-b6-cr-pension-constants.md` — B6 retirement-engine constants,
>    source-verified (research closed 2026-07-03).

---

## What the lost v2 contained (attested by references in the children)

These concepts are referenced by the surviving docs and therefore existed in v2. Their
authoritative definitions now live wherever the reference points:

- **Capa 1/2/3 risk decomposition** — the advisory layer's decomposition of user risk
  context (Capa 3 = the Grable-Lytton questionnaire; most Capa 1/2 users lack the risk
  signal — see plan §2 resolution O1).
- **D7.1 — archetype = soft overlay** (KMSI weak factorial validity) — inherited by the
  library's L9 ("archetype is a ranking modifier, never a selector") and the plan's D9.
- **Component B — context-pack assembler**; the principle matcher is a *sibling selector*
  on it, adding a `principles[]` field (library doc, Gate B). Realized as the Option C
  frozen context-pack (plan B11).
- **Component C — narration contract**; realized as the library doc's Gate C.
- **The parent's number-match scorer** (every narrated number must trace to a finding) —
  realized in the plan as the Gate-D scorer of B11.

## Decision inventory (authoritative copies live in the children)

| ID | One-line | Where |
|---|---|---|
| D1–D13 | Advisory-mode locked decisions (sequencing, Option A→C, retirement in v1, no MCP v1, library-shapes-narration-never-computation, hope-not-toxic-positivity, coaching-not-therapy, `financial_state` single owner, archetype=modifier, curated-not-RAG, auditable end-to-end, intent-per-message spine, no bare chat errors) | plan §1 |
| O1–O5 | Operator resolutions 2026-07-01 (matcher on `financial_state` alone; SUPEN banded retirement methodology; two-tier distress hand-off; slot-fill faithfulness; no hard sticky mode) | plan §11 |
| L1–L9 | Principle-library locked decisions (curated not RAG; narration never computation; hope anchored in real numbers; localization not translation; coaching not therapy; human review gate; P6c/P7 integration without duplication; machine-readable guardrails; archetype = ranking modifier) | library doc §1 |
| Gates A–E | Storage / matching / narration / hope-safety / curation-provenance | library doc §5 |

## Provenance

- Decisions locked with the operator **2026-07-01** (plan header).
- B6 constants research closed **2026-07-03** (constants doc header).
- This stub authored **2026-07-03** during the P10 implementation (branch `phase-10-advisory`);
  it is deliberately non-canonical — edit the children, not this file.
