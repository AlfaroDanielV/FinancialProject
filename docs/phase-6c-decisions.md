# Phase 6c Decision Log — User Memory & Behavioral Profiling

> **Goal**: a structured per-user memory layer that lets the bot personalise
> responses and gives the affordability engine (P7) real signals about
> behaviour. NOT a chat log. A set of typed, expirable insights with two
> writers (deterministic computed + LLM-extracted) and one reader tool.

This file is the **single source of truth for Phase 6c architectural
decisions**. Everything that follows is locked in before B2 starts; any
deviation requires re-opening this doc with a new dated entry.

---

## Three-layer memory model

The agent has three memory layers, kept explicitly separate. Confusing
them produces either privacy bugs or cache-busting.

| Layer | Storage | TTL | Purpose | Status |
|---|---|---|---|---|
| **Conversation** (short term) | Redis list, 10 turns | 24h | Continuity within a single chat session | Phase 6a B7 — **not touched in 6c** |
| **Insights** (medium term) | `user_insights` table (Postgres) | 7d–18mo per type | Personalisation, affordability signals | **Phase 6c — this** |
| **Semantic recall** (long term) | Vector embeddings of past chats | — | Q&A over old conversations | **OUT OF SCOPE** — low ROI for finance, high cost, privacy headaches |

Why no vector recall: financial decisions don't benefit from "what did we
talk about three months ago" the way customer-support chatbots do. The
DB already remembers transactions; the conversation layer remembers the
last day; insights summarise everything in between. Adding a vector
store is dollars and complexity for a problem we don't have.

---

## End-to-end flow

```
            ┌─────────────────────────────┐    ┌──────────────────────────────┐
            │  Determinístico              │    │  Conversacional              │
            │  ────────────────            │    │  ──────────────              │
   sources  │  transactions               │    │  query turns (history)       │
            │  recurring_bills            │    │  clarifications confirmed    │
            │  debts, accounts            │    │  /editar_memoria input       │
            └─────────────┬───────────────┘    └──────────────┬───────────────┘
                          │                                   │
                          │ SQL aggregates                    │ Haiku tool-use
                          │ (cero LLM)                        │ (cero SQL)
                          ▼                                   ▼
            ┌─────────────────────────────┐    ┌──────────────────────────────┐
            │  services/insights/         │    │  services/insights/          │
            │    computed.py              │    │    extractor.py              │
            │  source='computed'          │    │  source='llm_extracted'      │
            │  confidence: deterministic  │    │  confidence: capped 0.85     │
            └─────────────┬───────────────┘    └──────────────┬───────────────┘
                          │                                   │
                          │   list[InsightContent]            │   list[InsightContent]
                          │   (Pydantic, validated)           │   (Pydantic; invalid → discard)
                          └────────────┬──────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │  services/insights/     │
                          │    persister.py         │
                          │  upsert by              │
                          │   (user_id, type,       │
                          │    dedup_key)           │
                          │  skip if user_locked    │
                          │  audit on every write   │
                          └────────────┬────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │  user_insights table    │
                          │  + user_insights_audit  │
                          └────────────┬────────────┘
                                       │
                  ┌────────────────────┼────────────────────┐
                  │                    │                    │
                  ▼                    ▼                    ▼
       ┌─────────────────┐   ┌────────────────┐   ┌─────────────────┐
       │ tools/          │   │ /memoria       │   │ DELETE /export  │
       │  user_context   │   │ /olvidar       │   │ endpoints       │
       │ (formatted ES)  │   │ /editar_memoria│   │ (privacy)       │
       └────────┬────────┘   └────────────────┘   └─────────────────┘
                │
                ▼
       ┌─────────────────┐
       │  Sonnet         │
       │  dispatcher     │
       │  (Phase 6a)     │
       │  prompt cache   │
       │  intact ←       │  insights NEVER inline in system prompt
       └─────────────────┘
```

---

## Locked decisions

### 1. Schema tipado, no free-form (2026-05-06)

`user_insights.content` is JSONB, but every row's payload is validated
against a Pydantic model that depends on `insight_type`. No "notes" or
"misc" escape hatches.

**The 14 insight types and their content shapes** (full Pydantic
definitions in B2; documented here as the contract). B2 also stores a
literal `type` discriminator inside `content` so the discriminated union
can validate raw JSON without out-of-band state:

```python
class SpendingPattern(BaseModel):
    category: str
    monthly_avg_crc: Decimal | None       # null when amount is in USD only
    monthly_avg_usd: Decimal | None
    trend_pct_3mo: Decimal                # signed, e.g. +8.0 = up 8%
    volatility_score: Decimal             # 0..1, std-dev / mean

class RecurringDrift(BaseModel):
    bill_id: UUID                         # FK to recurring_bills.id
    baseline_amount: Decimal              # historical median
    current_amount: Decimal               # most recent paid amount
    drift_pct: Decimal                    # signed
    detected_at: datetime

class CashFlowStability(BaseModel):
    score_0_100: int                      # higher = more stable
    monthly_variance_pct: Decimal         # std-dev / mean of monthly net flow
    income_sources_count: int
    savings_rate_pct: Decimal             # (income - spending) / income
    last_income_at: date | None           # last recognised income deposit

class DebtLoad(BaseModel):
    debt_to_income_ratio: Decimal         # 0..N
    total_monthly_debt_service_crc: Decimal
    num_active_debts: int

class EmergencyFund(BaseModel):
    months_of_expenses_covered: Decimal
    liquid_balance_crc: Decimal
    monthly_essential_expenses_crc: Decimal
    sufficiency: Literal["insufficient", "borderline", "adequate", "abundant"]

class CRSeasonalPattern(BaseModel):
    event: Literal["aguinaldo", "salario_escolar", "marchamo"]
    expected_month: int                   # 1..12
    historical_amount_crc: Decimal | None
    last_observed_year: int | None

class StatedPreference(BaseModel):
    topic: Literal["debt_payoff", "savings", "risk_tolerance", "lifestyle"]
    stance: str                           # short summary, e.g. "prioriza pagar deudas"
    raw_quote: str                        # truncated to 280 chars; deleted at 30d
    extracted_at: datetime

class StatedGoal(BaseModel):
    goal_text: str                        # short description ≤200 chars
    target_amount: Decimal | None
    target_date: date | None
    status: Literal["mentioned", "committed", "abandoned"]

class BehavioralFlag(BaseModel):
    flag_type: Literal["weekend_overspend", "paycheck_cycle", "impulse_pattern"]
    evidence_window: str                  # human-readable, e.g. "últimas 8 semanas"
    strength_score: Decimal               # 0..1

class Archetype(BaseModel):
    primary: str                          # e.g. "saver_under_pressure"
    secondary: str | None
    confidence: Decimal                   # capped per source rules
    evidence_summary: str                 # ≤300 chars

class RiskPosture(BaseModel):
    posture: Literal["conservative", "moderate", "aggressive"]
    evidence_basis: Literal["stated", "observed", "mixed"]

class DecisionStyle(BaseModel):
    style: Literal["analytical", "intuitive", "avoidant"]
    evidence: str                         # ≤200 chars

class FinancialLiteracy(BaseModel):
    level: Literal["beginner", "intermediate", "advanced"]
    evidence: str                         # ≤200 chars
```

Plus one **derived-from-conflict** type emitted by lifecycle in B5:

```python
class StatedObservedGap(BaseModel):
    stated_insight_id: UUID
    observed_insight_id: UUID
    description: str                      # auto-generated, e.g.
                                          #   "decís ser ahorrador pero el
                                          #    savings rate está en 2%"
```

Adding a new insight type = migration + schema review. There is no
"free-text override". If the LLM emits something we can't classify, we
drop it.

### 2. Two writers, separated, never crossed (2026-05-06)

| File | When it runs | Inputs | Tools | source value |
|---|---|---|---|---|
| `services/insights/computed.py` | Nightly (Container Apps Job) + on-demand admin trigger | `transactions`, `recurring_bills`, `debts`, `accounts` | Pure SQL aggregates | `'computed'` |
| `services/insights/extractor.py` | Post-query (after dispatcher finishes), post-clarification confirm, /editar_memoria | Last N conversation turns + a brief transaction context summary | Haiku with strict JSON tool | `'llm_extracted'` |
| `services/insights/persister.py` | Called by both writers | `list[InsightContent]` | None | passes through |

The computed writer never calls an LLM. The LLM extractor never queries
aggregates. Crossing the streams blurs accountability — when a number
looks wrong, we want to know which pipeline produced it without log
forensics.

### 3. Behavioral signals > stated signals (2026-05-06)

If a `computed` insight contradicts an `llm_extracted` insight (user
says "soy ahorrador" but `spending_pattern` shows savings_rate=2%),
**both rows persist**. The contradiction itself is signal.

`services/insights/lifecycle.detect_stated_observed_gaps` (B5)
periodically scans for these contradictions and emits a derived
`stated_observed_gap` insight referencing both. P7 (affordability)
uses these gap rows to choose tone — gentle for "stated_aggressive +
observed_conservative", direct for "stated_conservative +
observed_aggressive_spender".

**Never** does one writer overwrite the other based on disagreement.

### 4. Confidence scoring (2026-05-06)

**Computed insights** — confidence is a deterministic function of:
- sample size (more transactions ⇒ more confidence)
- recency (data >90d old ⇒ confidence decays)
- coverage (e.g. spending_pattern in a category with ≥10 occurrences
  in the lookback window scores higher than ≥3)

The exact formula lives in `computed.py` and is unit-tested. Range:
`[0.5, 1.0]` — computed insights never claim less than 0.5 because
they are by construction grounded in real DB rows.

**LLM-extracted insights** — confidence is what the LLM reports,
**capped at 0.85**. We never let an LLM tell us "I'm 95% sure" about
inferred preferences. Reinforcement (see B5) lets confidence climb in
discrete steps when the same observation reappears across multiple
extraction runs:

```
repeated_observations = reinforcement_count - 1
new_confidence = min(0.85 + 0.05 * repeated_observations, 0.95)
```

So even after many reinforcements an LLM-extracted insight tops out
at 0.95 — strictly less than a computed one with full evidence.

**User-edited insights** (`user_locked=true`) hold confidence `1.0`
regardless of source. The user's word is law in their own memory.

### 5. TTL per insight type (2026-05-06)

Every insight expires. The `valid_until` column is filled at write
time based on the type:

| Type | TTL | Why this number |
|---|---|---|
| `spending_pattern` | 60 days | One quarter is the natural cadence; older numbers misrepresent. |
| `recurring_drift` | 30 days | A drift detected last month should be re-confirmed. |
| `cash_flow_stability` | 30 days | Same — instability shifts monthly. |
| `debt_load` | 7 days | Loans pay down or up fast; week is the right granularity. |
| `emergency_fund` | 7 days | Liquid balances change quickly; P7 needs fresh coverage data. |
| `cr_seasonal_pattern` | 18 months | One full year + buffer. Aguinaldo / marchamo / salario escolar are annual. |
| `stated_preference` | 180 days | People's preferences are stable but not forever. Refreshable on re-statement. |
| `stated_goal` | `target_date + 30d` (or 365d if no date) | Outlive the goal slightly so we can confirm completion. |
| `behavioral_flag` | 90 days | Patterns need re-validation each quarter. |
| `archetype` | 90 days | Identity-level inferences should be re-derived seasonally. |
| `risk_posture` | 90 days | Same. |
| `decision_style` | 90 days | Same. |
| `financial_literacy` | 90 days | Improves with use of the agent. |
| `stated_observed_gap` | 30 days | Recomputed nightly; if either ref expires, the gap stops being regenerated. |

Read-time filter: `get_user_context` returns only rows where
`valid_until > NOW()`. Hard-delete: `expire_stale` job removes rows
where `valid_until < NOW() - 30d` so audit history isn't permanently
buried under stale data.

### 6. User-visible and editable, no exceptions (2026-05-06)

The user owns their memory. Three Telegram commands enforce this:

- `/memoria` — lists all active insights in plain Spanish, grouped by
  category. NEVER shows JSON or jargon.
- `/olvidar` — soft menu of categories or whole-purge. Whole-purge
  requires two confirmations.
- `/editar_memoria` — user provides a natural-language correction; bot
  parses to schema and writes with `source='user_override'` and
  `user_locked=true`.

`user_locked=true` is sacred:
- `computed.py` skips locked rows on upsert.
- `extractor.py` checks for locked rows before writing same `(user_id,
  type, dedup_key)` and bails if found.
- Only the user can unlock (via `/editar_memoria` editing the same
  insight again, or `/olvidar` deleting it entirely).

Pretty UI for memory management lives in 6e (Centro Financiero SPA).
6c is Telegram-first.

### 7. Privacy as first-class feature (2026-05-06)

Concrete commitments backed by code:

- `DELETE /api/v1/users/me/insights` → hard-delete every row + audit
  entry. Returns count. No soft-delete fallback.
- `GET /api/v1/users/me/insights/export` → JSON dump of all the user's
  insights (active + still-not-purged expired). Streams if >1MB.
- `raw_quote` field on `stated_preference` is auto-truncated to 280
  chars at write time and **wiped at age 30d** by the lifecycle job.
  After 30 days we keep `topic` and `stance` but lose the raw quote.
- Logging redaction middleware: `user_insights.content` is never
  serialised in error logs, access logs, or APM traces.
- The privacy commitment lives at `docs/phase-6c/privacy.md` (B8) and
  becomes the substantive content for the public privacy policy in
  P9.

### 8. Cache discipline — insights via tool, not prompt (2026-05-06)

The system prompt of the Sonnet query dispatcher (Phase 6a) is **not**
modified to inline user insights. Doing so would bust the 5-minute
ephemeral cache hit rate that makes Sonnet usage affordable.

Instead, the dispatcher gets a new tool `get_user_context(insight_types,
max_insights)` that returns a formatted Spanish bullet list of
currently-relevant insights. The tool result is a per-call, per-user
piece of conversational context — exactly the kind of thing the cache
was designed to handle.

**Hard rule**: anything that varies per user goes through a tool, not
the prompt. The prompt updates in B9 are static instructions about
*when* to call the tool, not *what* the user's memory contains.

### 9. Model selection (2026-05-06)

| Step | Model | Rationale |
|---|---|---|
| Insight extraction (Phase 6c) | `claude-haiku-4-5` | Cheap; structured-JSON tool-use is well within Haiku capability. We log validation failures so we'd see if the model can't keep up. |
| Query dispatch (Phase 6a, unchanged) | `claude-sonnet-4-5` | Tool-using reasoning. No change in 6c. |
| `/editar_memoria` parsing (Phase 6c B7) | `claude-haiku-4-5` | One short user utterance → one schema. Haiku is enough. |

`get_user_context` is a tool the Sonnet dispatcher can call; the tool's
implementation never calls an LLM (it just queries the table and
formats).

### 10. No fallback on validation failure (2026-05-06)

When the extractor's LLM produces JSON that fails Pydantic validation
(missing field, wrong literal, malformed date, etc.):

1. The whole extraction output is **discarded**. No partial-rescue, no
   "use the valid fields and drop the bad one".
2. A `WARNING` is logged with the validation error path and the
   message-hash (NOT the message text — PII).
3. The conversation continues; the user sees nothing.

Why: a partial rescue introduces low-confidence garbage into a table
the dispatcher trusts. Better zero new insights than tainted ones.
The DB stays clean; the next extraction run gets another shot.

---

## Schema design

### Table `user_insights`

Migration **0013** (next available; 0012 is the Phase 6b
`gmail_sender_whitelist`).

```sql
CREATE TABLE user_insights (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID            NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    insight_type    TEXT            NOT NULL,
    content         JSONB           NOT NULL,
    confidence      NUMERIC(3,2)    NOT NULL,
    source          TEXT            NOT NULL,
    valid_until     TIMESTAMPTZ     NOT NULL,
    user_locked     BOOLEAN         NOT NULL DEFAULT FALSE,
    -- dedup_key disambiguates rows for the same (user, type). Examples:
    --   spending_pattern → category name
    --   recurring_drift  → bill_id
    --   stated_preference → topic
    --   archetype, risk_posture, etc. → 'global'
    dedup_key       TEXT            NOT NULL,
    -- Reinforcement: each time the same observation reappears, we bump
    -- this counter and extend valid_until. confidence climbs in steps
    -- (see decision #4).
    reinforcement_count INTEGER     NOT NULL DEFAULT 1,
    last_reinforced_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT ck_user_insights_confidence
      CHECK (confidence >= 0 AND confidence <= 1),

    CONSTRAINT ck_user_insights_source
      CHECK (source IN ('computed', 'llm_extracted', 'user_override')),

    CONSTRAINT ck_user_insights_type
      CHECK (insight_type IN (
        'spending_pattern',
        'recurring_drift',
        'cash_flow_stability',
        'debt_load',
        'emergency_fund',
        'cr_seasonal_pattern',
        'stated_preference',
        'stated_goal',
        'behavioral_flag',
        'archetype',
        'risk_posture',
        'decision_style',
        'financial_literacy',
        'stated_observed_gap'
      )),

    -- One active row per (user, type, dedup_key). Upserts use this.
    CONSTRAINT uq_user_insights_dedup
      UNIQUE (user_id, insight_type, dedup_key)
);

-- Hot path: get_user_context filters by user_id, then by valid_until,
-- then orders by confidence DESC, updated_at DESC.
CREATE INDEX ix_user_insights_user_type_valid
  ON user_insights (user_id, insight_type, valid_until DESC);

CREATE INDEX ix_user_insights_user_confidence
  ON user_insights (user_id, confidence DESC, updated_at DESC);

-- For the lifecycle job sweeping expired rows.
CREATE INDEX ix_user_insights_valid_until_unlocked
  ON user_insights (valid_until)
  WHERE user_locked = false;
```

Notes:
- `dedup_key` is a TEXT, not normalised. Each insight type is
  responsible for its own canonical form (lowercase category, UUID
  string, etc.). The persister calls a `dedup_key_for(content)`
  function on each Pydantic model.
- `user_locked` insights are never auto-expired even past
  `valid_until`. The user has to explicitly delete or re-edit them.
  The partial index on `valid_until` excludes them so the lifecycle
  job doesn't waste time scanning rows it can't touch.
- No `tenant_id`. Multi-tenancy is a P9 concern; P8 already scopes
  everything by `user_id` and that's enough.

### Table `user_insights_audit`

```sql
CREATE TABLE user_insights_audit (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Nullable on purpose: when an insight is hard-deleted we keep the
    -- audit row pointing at the (now-gone) UUID so the timeline is
    -- intact.
    insight_id  UUID,
    action      TEXT         NOT NULL,
    actor       TEXT         NOT NULL,
    -- payload captures whatever the action needs: the diff for
    -- 'updated', the deleted content for 'deleted' (so the user could
    -- in principle un-delete via /editar_memoria within ~30d), the
    -- export job metadata for 'exported'.
    payload     JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT ck_user_insights_audit_action
      CHECK (action IN (
        'created', 'updated', 'deleted',
        'reinforced', 'locked', 'unlocked', 'exported'
      )),
    CONSTRAINT ck_user_insights_audit_actor
      CHECK (actor IN ('computed_worker', 'llm_extractor', 'user', 'admin'))
);

-- Read pattern: timeline of one user's actions.
CREATE INDEX ix_user_insights_audit_user_time
  ON user_insights_audit (user_id, created_at DESC);

-- Read pattern: history of one specific insight.
CREATE INDEX ix_user_insights_audit_insight
  ON user_insights_audit (insight_id, created_at DESC)
  WHERE insight_id IS NOT NULL;
```

Audit row is written by `persister.py` for every successful upsert and
by every `/olvidar` / `/editar_memoria` / `DELETE /insights` action.

### Dedup key strategy per insight type

The persister calls a per-type `dedup_key(content)` function.
Documented here so B2 can implement them without re-deriving:

| Type | dedup_key |
|---|---|
| `spending_pattern` | `category` (lowercased) |
| `recurring_drift` | `bill_id` (UUID as string) |
| `cash_flow_stability` | `'global'` |
| `debt_load` | `'global'` |
| `emergency_fund` | `'global'` |
| `cr_seasonal_pattern` | `event` (already an enum) |
| `stated_preference` | `topic` (already an enum) |
| `stated_goal` | normalised goal_text — lowercase, whitespace-collapsed, first 80 chars |
| `behavioral_flag` | `flag_type` (already an enum) |
| `archetype` | `'global'` |
| `risk_posture` | `'global'` |
| `decision_style` | `'global'` |
| `financial_literacy` | `'global'` |
| `stated_observed_gap` | concat of the two referenced insight_ids, sorted |

A user has at most ONE active row per type for the `'global'` ones;
that's the whole point of dedup.

### Persister upsert semantics

```
UPSERT(content, source, user_id):
  type = inferred from Pydantic model class
  dk   = dedup_key_for(content)
  existing = SELECT WHERE (user_id, type, dk)
  IF existing AND existing.user_locked:
    audit('skipped_locked')
    RETURN  -- never overwrite a locked row
  IF existing AND existing.source == 'user_override' AND source != 'user':
    -- User-set rows are also protected
    audit('skipped_user_override')
    RETURN
  IF existing AND content_equivalent(existing.content, new content):
    -- Same observation reappeared
    existing.reinforcement_count += 1
    existing.last_reinforced_at = now()
    existing.confidence = bump(existing.confidence, source)
    existing.valid_until = max(existing.valid_until, now() + ttl(type))
    existing.updated_at = now()
    audit('reinforced')
  ELSE:
    UPSERT new row, mark old (if any) as updated
    audit('created' OR 'updated')
```

`content_equivalent` is per-type (numeric tolerance for amounts,
exact match for enums and stated text).

---

## Out of scope (re-stated, locked)

- **Vector embeddings** for semantic recall over chats. Not built.
- **Cross-user learning** — federated insights wait for P9.
- **Web UI for memory management** — Phase 6e (Centro Financiero).
- **Real-time insight streaming** — nightly + post-query is enough.
- **Multi-language extraction** — Spanish (CR voseo) only.
- **Insights from raw email content** — emails already feed transactions
  via Phase 6b; 6c reads transactions, not raw email.
- **ML clustering for archetypes** — LLM with curated few-shots covers
  the early case.
- **`Transaction.amount` float → Decimal refactor** — pre-existing tech
  debt, not 6c's problem.

---

## Decisions resolved during B1 review

### 2026-05-07 — Add `emergency_fund` insight type (Q1)

Decision: agregar un 14º tipo `emergency_fund` (computed) y enriquecer
el shape de `cash_flow_stability` con `savings_rate_pct` y
`last_income_at`. NO se agrega `income_pattern` separado porque
`cash_flow_stability` ya cubre fuentes y volatilidad de ingresos.

Motivo: P7 (affordability engine) necesita responder "¿podés absorber
un gasto de X sin endeudarte?" — eso requiere meses de cobertura de
fondo de emergencia. Sin este tipo, P7 deriva un proxy débil. Mejor un
señal explícito.

```python
class EmergencyFund(BaseModel):
    months_of_expenses_covered: Decimal     # 0..N, ej 2.5 = 2.5 meses
    liquid_balance_crc: Decimal             # suma de cuentas líquidas
    monthly_essential_expenses_crc: Decimal # usado para calcular meses
    sufficiency: Literal["insufficient", "borderline", "adequate", "abundant"]
                                            # <1 / 1-3 / 3-6 / >6 meses
```

`cash_flow_stability` enriquecido:
```python
class CashFlowStability(BaseModel):
    score_0_100: int
    monthly_variance_pct: Decimal
    income_sources_count: int
    savings_rate_pct: Decimal               # NEW: (income - spending) / income
    last_income_at: date | None             # NEW: fecha del último depósito reconocido
```

Implicación: TTL de `emergency_fund` = 7 días (cambia rápido como
`debt_load`). Dedup_key = `'global'`. La lista de 14 tipos queda
locked para 6c.

### 2026-05-07 — Confidence ranges confirmed (Q2)

Decision: se mantiene la propuesta original.
- `user_locked=true` ⇒ `confidence=1.0` (set al editar, no decae).
- `computed` ⇒ `[0.5, 1.0]` deterministic por evidencia.
- `llm_extracted` ⇒ base 0.85, +0.05 por reinforcement, cap 0.95.

Tiebreak en el dispatcher: `confidence DESC, updated_at DESC`. Si un
computed full-evidence (1.0) y un user_override (1.0) coinciden, gana
el más reciente — alineado con "el último input del usuario es el más
relevante".

### 2026-05-07 — `source='user_override'` is the current origin (Q3)

Decision: `source` refleja el origen **autoritativo del contenido
actual**, no la derivación histórica. Cuando el usuario edita un
insight (computed o llm_extracted), el `source` cambia a
`user_override`. La historia ("antes era computed con value X") vive
en audit log via `payload.previous_content`.

Motivo: leer una fila debe dar verdad inmediata sobre quién es la
autoridad sobre el contenido actual. Forensics es responsabilidad del
audit log. Mantener `source='computed', user_locked=true` después de
una edición usuario sería ambiguo — invita preguntas como "¿confío en
el contenido como si lo hubiera puesto la pipeline computed?".

`source` ENUM final: `'computed' | 'llm_extracted' | 'user_override'`.

### 2026-05-07 — `stated_observed_gap` TTL fixed at 30d + nightly recompute (Q4)

Decision: TTL fijo de 30 días. `lifecycle.detect_stated_observed_gaps`
recomputa nightly. Si una de las refs expira, el gap deja de
recomputarse y desaparece naturalmente en su propio TTL.

Motivo: la alternativa "inherit shorter TTL of refs" agrega complejidad
de lookup en write-time y join en read-time. Recompute nightly logra el
mismo resultado correctness-wise (el gap nunca outlives its evidence)
sin esa complejidad.

Si el gap insight detecta que sus refs ya no existen al recomputar,
emite un audit row `'deleted'` con razón en payload y la lifecycle hace
hard-delete en el siguiente sweep.

### 2026-05-07 — Dedup_key as TEXT, per-type semantics (Q5)

Decision: `dedup_key TEXT`. Cada `InsightContent` Pydantic declara su
`dedup_key()` como método de instancia. Sin columnas explícitas
(`category`, `bill_id`, `topic`).

Motivo: simplicidad de schema + flexibilidad para agregar tipos sin
migración pesa más que la query-ability per-columna. Queries
analíticas que necesitan `category` específico usan
`content->>'category'` (jsonb path), que es lo que harían igual aún
con columna explícita por compatibilidad cross-type.

Caveat aceptado: no hay FK desde `recurring_drift` a `recurring_bills`.
Cuando un recurring_bill se soft-deletea (is_active=false), las drift
insights huérfanas expiran en su TTL de 30d sin causar daño. Si en P9
SaaS hardening esto duele, se introduce un sweep job dedicado.

### 2026-05-07 — Audit payloads typed per action (Q6)

Decision: cada valor de `action` tiene un Pydantic schema obligatorio
para `payload`. Persister valida antes de insert. Schemas declarados
en `schemas/insights.py` como `AuditPayload*` y validados en
`persister.py::audit()`.

Shapes por acción:

```python
class AuditPayloadCreated(BaseModel):
    insight_type: str
    dedup_key: str
    content: dict[str, Any]      # snapshot for replay
    confidence: Decimal
    source: str
    valid_until: datetime

class AuditPayloadUpdated(BaseModel):
    insight_type: str
    dedup_key: str
    previous_content: dict[str, Any]
    new_content: dict[str, Any]
    previous_confidence: Decimal
    new_confidence: Decimal

class AuditPayloadDeleted(BaseModel):
    insight_type: str
    dedup_key: str
    content_at_deletion: dict[str, Any]
    confidence_at_deletion: Decimal
    deletion_reason: str         # e.g. 'user_olvidar', 'expired_swept'

class AuditPayloadReinforced(BaseModel):
    insight_type: str
    dedup_key: str
    reinforcement_count_after: int
    confidence_before: Decimal
    confidence_after: Decimal

class AuditPayloadLocked(BaseModel):
    insight_type: str
    dedup_key: str
    content: dict[str, Any]
    locked_via: Literal["editar_memoria", "admin"]

class AuditPayloadUnlocked(BaseModel):
    insight_type: str
    dedup_key: str
    content: dict[str, Any]
    unlocked_via: Literal["editar_memoria", "admin"]

class AuditPayloadExported(BaseModel):
    count: int                   # number of insights in export
    format: Literal["json"]      # extensible
    request_id: str              # for cross-referencing API logs
```

Motivo: forensics depende de payloads predecibles. JSONB free-form
vuelve la audit table inútil para "rebuild user X's memory state at
time T" o "show diff for insight Y" — operaciones que P7 y futura
privacy review necesitan.

`'deleted'` payload incluye `content_at_deletion` para soportar un
"undo" futuro dentro del retention window de audit. No se construye
hoy; se deja la puerta abierta.

---

## Updated insight type list (post-B1 review)

14 types locked:

1. `spending_pattern`
2. `recurring_drift`
3. `cash_flow_stability` *(enriched: +savings_rate_pct, +last_income_at)*
4. `debt_load`
5. `emergency_fund` *(NEW)*
6. `cr_seasonal_pattern`
7. `stated_preference`
8. `stated_goal`
9. `behavioral_flag`
10. `archetype`
11. `risk_posture`
12. `decision_style`
13. `financial_literacy`
14. `stated_observed_gap` *(derived)*

The CHECK constraint on `user_insights.insight_type` admits all 14.
Adding a 15th = migration + schema review.

---

## Approval gates (between blocks)

- **After B2** (this doc + migrations + Pydantic models): Daniel reviews
  schema, indices, audit table.
- **After B5** (computed + extractor + lifecycle): Daniel reviews 5
  computed outcomes and 5 extracted outcomes against synthetic fixtures.
- **After B7** (Telegram commands): Daniel runs `/memoria`, `/olvidar`,
  `/editar_memoria` against his own real data.
- **After B11** (shadow validation): 7 days in production with the
  dispatcher tool *disabled*. Daniel reviews `/memoria` daily.
  Approval before flipping `INSIGHTS_DISPATCHER_ENABLED=true`.
- **B12 closes the phase**: CLAUDE.md merged, curl guide green.

---

## Implementation Status Through B6

### B1-B2 — decisions, schema, migration

Status: implemented.

- Decision log created in this file.
- `user_insights` and `user_insights_audit` shipped in migration `0013`.
- SQLAlchemy models live in `api/models/user_insight.py`.
- Pydantic schemas live in `api/schemas/insights.py`.
- The schema includes 14 insight types, including the B1 review addition
  `emergency_fund`.
- Each insight content model owns its `dedup_key()`.
- Audit payloads are typed by action and validated before insert.

### B3 — deterministic computed writer

Status: implemented.

Files:

- `api/services/insights/computed.py`
- `api/services/insights/persister.py`
- `tests/test_phase_6c_b3_computed_insights.py`

Implemented computed functions:

- `compute_spending_patterns`
- `compute_recurring_drift`
- `compute_cash_flow_stability`
- `compute_debt_load`
- `compute_cr_seasonal_patterns`
- `compute_emergency_fund`
- `compute_all`

Important repo-specific adaptations:

- There is no `recurring_incomes` table yet. B3 derives recent monthly
  income from confirmed positive `transactions`.
- `debts` uses `minimum_payment`, not `monthly_payment`.
- `accounts` has no balance column. `emergency_fund` estimates liquid
  balance from confirmed checking/savings transactions up to `as_of`.
- All computed functions exclude duplicate and non-confirmed transactions.

Computed confidence formula:

```text
0.50 + 0.50 * (0.70 * sample_score + 0.30 * recency_score)
```

`sample_score` caps at 1.0 once the insight has enough observations for
that type. `recency_score` is 1.0 for <=30d, 0.85 for <=60d, 0.70 for
<=90d, and 0.50 after that. Output is quantized to two decimals.

### B4 — LLM extractor writer

Status: implemented.

Files:

- `prompts/insight_extractor.py`
- `api/services/insights/extractor.py`
- `migrations/versions/0014_phase6c_insight_extractor_tracking.py`
- `tests/test_phase_6c_b4_insight_extractor.py`

Extractor contract:

```python
extract_insights(
    user_id: UUID,
    conversation_window: Sequence,
    transaction_context: str | dict | None,
) -> list[InsightContent]
```

The public function has no DB side effects. Production hooks call
`run_insight_extraction_job`, which:

1. Calls Haiku with `prompts/insight_extractor.py`.
2. Validates the tool output as one strict object:
   `{"insights": [{"type": ..., "confidence": ..., "content": {...}}]}`.
3. Rejects the whole output on any validation failure.
4. Persists through `persist_insights(..., source="llm_extracted")`.
5. Logs usage and cost in `llm_query_dispatches`.

Allowed LLM types are exactly:

- `stated_preference`
- `stated_goal`
- `archetype`
- `risk_posture`
- `decision_style`
- `financial_literacy`

The tool schema does not allow computed-only types. If a malformed or
computed type reaches app validation anyway, the whole batch is discarded.

Hook behavior:

- Query dispatcher success path enqueues extraction after appending the
  successful user/assistant turn to Redis history.
- Write clarification path enqueues extraction after a stored
  clarification answer is merged and routed.
- The enqueue path is non-blocking (`asyncio.create_task`) and returns the
  user response without waiting for Haiku.
- Runtime flag: `INSIGHTS_EXTRACTOR_ENABLED=false` by default. Turn this
  on for B11 shadow validation. This protects local/unit runs from
  accidental paid Anthropic calls.

Tracking migration `0014`:

- Adds `llm_query_dispatches.extractor_run_id UUID NULL`.
- Adds `llm_query_dispatches.estimated_cost_usd NUMERIC(12,6) NULL`.
- Each extractor run creates its own `llm_query_dispatches` row with
  `tools_used[0].name = "insight_extractor"`.
- If the extractor run came from a query dispatch, the originating query
  row points to that extractor run via `extractor_run_id`.

Cost estimate basis:

- Defaults use Claude Haiku 4.5 pricing on Anthropic Platform:
  `$1/MTok` input, `$5/MTok` output, `$0.10/MTok` cache hits, and
  `$1.25/MTok` 5-minute cache writes.
- These are configurable through:
  `LLM_INSIGHT_INPUT_USD_PER_MTOK`,
  `LLM_INSIGHT_OUTPUT_USD_PER_MTOK`,
  `LLM_INSIGHT_CACHE_READ_USD_PER_MTOK`,
  `LLM_INSIGHT_CACHE_WRITE_USD_PER_MTOK`.
- Source: Anthropic pricing docs,
  `https://docs.claude.com/en/docs/about-claude/pricing`.

Validation fixtures:

- B4 includes 10 curated Spanish fixture shapes covering all six
  LLM-extractable types.
- Invalid mixed batches return `[]`; no partial salvage.
- A computed-type output returns `[]`.
- DB-backed job test verifies persistence, origin dispatch linking, token
  logging, and non-zero estimated cost.

### B5 — insight lifecycle

Status: implemented.

Files:

- `api/services/insights/lifecycle.py`
- `workers/insights_lifecycle.py`
- `tests/test_phase_6c_b5_lifecycle.py`

Implemented functions:

- `expire_stale(session, now=...)`
- `reinforce(session, insight_id, now=...)`
- `detect_stated_observed_gaps(session, user_id, now=...)`
- `run_lifecycle_for_user(session, user_id, now=...)`

Nightly lifecycle worker:

```bash
uv run python -m workers.insights_lifecycle
```

This B5 worker is lifecycle-only. The full computed nightly worker with
Azure Container Apps Job infra lands in B10. The lifecycle worker:

1. iterates active users;
2. recomputes `stated_observed_gap`;
3. redacts stale `raw_quote` values;
4. hard-deletes unlocked insights expired for more than 30 days.

Expiry semantics:

- No new `expired_at` column was added. Expiry is represented by
  `valid_until < now`.
- Read paths must continue filtering by `valid_until > now`.
- `user_locked=true` rows are never hard-deleted by lifecycle, even when
  `valid_until` is in the past.
- Unsupported gap rows are marked expired by setting `valid_until` into
  the past. That removes them from reads while preserving the row until
  the 30-day purge grace period passes.

Raw quote retention:

- `stated_preference.raw_quote` is replaced with
  `[eliminado por privacidad]` after 30 days.
- The redaction itself is intentionally not audited because an audit diff
  would copy the raw quote into `user_insights_audit`, defeating the
  privacy rule.
- Deletion audit payloads also redact `raw_quote` before storing
  `content_at_deletion`.

Reinforcement formula:

```text
llm_extracted:
  repeated_observations = reinforcement_count - 1
  confidence = min(max(previous_confidence,
                       0.85 + 0.05 * repeated_observations),
                   0.95)

computed:
  confidence = min(previous_confidence + 0.05, 1.00)

user_override / user_locked:
  no automatic lifecycle mutation
```

The first repeated LLM observation moves confidence to at least 0.90;
the second moves it to at least 0.95; it never exceeds 0.95. Computed
rows can reach 1.00. User-locked rows are skipped because automatic
processes must not mutate user overrides.

Gap detection rules in B5:

- `stated_preference(topic="savings")` vs
  `cash_flow_stability.savings_rate_pct <= 5`.
- `stated_preference(topic="debt_payoff")` vs non-positive recent
  savings rate.
- active savings/fund `stated_goal` vs non-positive recent savings rate.
- `risk_posture(posture="conservative")` vs
  `debt_load.debt_to_income_ratio >= 0.40`.
- `risk_posture(posture="aggressive")` vs `emergency_fund.sufficiency`
  in `insufficient|borderline`.

Descriptions are factual and avoid psychological interpretation.

### B6 — user context tool

Status: implemented.

Files:

- `app/queries/tools/user_context.py`
- `app/queries/tools/__init__.py`
- `app/queries/llm_client.py`
- `app/queries/prompts/system.py`
- `tests/test_phase_6c_b6_user_context.py`
- `tests/test_system_prompt_builder.py`
- `tests/test_tool_registry.py`

Implemented behavior:

- `get_user_context(insight_types=None, max_insights=10)` reads only
  active rows for the current user (`valid_until > now`).
- The tool formats insights as compact Spanish bullets, never JSON.
- Rows are ordered by `confidence DESC, updated_at DESC` and truncated
  to the requested limit.
- `compare_periods` remains the last built-in tool so Anthropic cache
  breakpoints stay aligned with the existing dispatcher rule.
- The dispatcher system prompt now includes static memory guidance and a
  few-shot example that uses `get_user_context` for comparative
  personalization.
