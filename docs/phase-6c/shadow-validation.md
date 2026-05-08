# Phase 6c B11 — Shadow validation runbook

The 7-day window before B12 closes the phase. The extractor runs and
persists insights; the dispatcher does NOT yet expose them through
`get_user_context`. Daniel reviews `/memoria` daily and decides whether
the extracted memory looks correct. Approval at the end of the window
is a hard gate — code stays unchanged either way.

This file is the operational checklist. If something here conflicts
with `docs/phase-6c-decisions.md`, the decisions doc wins.

---

## Day 0 — Deploy

### 0.1 Pre-deploy verification

- `tests/test_phase_6c_b11_dispatcher_flag.py` passes locally.
- Coverage on `api/services/insights/*`, `app/queries/tools/user_context.py`,
  `bot/memory_handlers.py` is ≥ 80% (current: ~88%).
- `scripts/test_phase_6c.sh` passes against staging or local.
- Cache baseline recorded per `docs/phase-6c/cache-verification.md`.

### 0.2 Environment variables

In Container Apps env-config (NOT the worker job):

```env
INSIGHTS_EXTRACTOR_ENABLED=true       # post-query Haiku writes insights
INSIGHTS_DISPATCHER_ENABLED=false     # dispatcher does NOT use them yet
```

Both flags default to `false` in code; the deploy explicitly sets
extractor on, dispatcher off.

### 0.3 Smoke checks after deploy

```bash
# 1. Health check.
curl -fsS https://<api>/health/ready | jq

# 2. Confirm dispatcher prompt does NOT contain memory section.
#    (Indirect — via a test query against the API. The system prompt
#    isn't exposed; instead we confirm tool-use doesn't call
#    get_user_context by inspecting llm_query_dispatches.)
psql "$DATABASE_URL" -c "
SELECT tools_used
FROM llm_query_dispatches
WHERE created_at > now() - interval '5 minutes'
  AND tools_used::text NOT LIKE '%insight_extractor%'
ORDER BY created_at DESC
LIMIT 3;
"
# Expect: NO row contains 'get_user_context'.

# 3. Confirm extractor IS writing insights.
psql "$DATABASE_URL" -c "
SELECT COUNT(*)
FROM user_insights
WHERE source = 'llm_extracted'
  AND created_at > now() - interval '1 hour';
"
# Expect: >0 after a few normal bot conversations.
```

If either of (2) or (3) is wrong, roll back — do not start the window.

---

## Days 1 – 7 — Daily review

Each day, at any time:

### `/memoria` from Telegram

Daniel pulls `/memoria` on his real account and reviews:

- Are the **stated_goal** rows things he actually said?
- Are **stated_preference** rows accurate paraphrases?
- Are **archetype** / **risk_posture** / **decision_style** /
  **financial_literacy** plausible given recent conversations?
- Are computed insights (spending_pattern, debt_load, etc.) numerically
  correct against the ledger?
- Is anyone surfacing a confidence-low (`⚠️`) insight that's wildly
  wrong? (Computed never goes below 0.5; LLM base is 0.85, so this
  should be rare.)

### Operator SQL view (when reviewing without Telegram)

```sql
SELECT
    insight_type,
    source,
    confidence,
    user_locked,
    valid_until,
    content
FROM user_insights
WHERE user_id = '<daniel-user-id>'
  AND valid_until > now()
ORDER BY source, insight_type, dedup_key;
```

### Audit log spot-check

```sql
SELECT
    action,
    actor,
    payload->>'insight_type' AS insight_type,
    created_at
FROM user_insights_audit
WHERE user_id = '<daniel-user-id>'
  AND created_at > now() - interval '24 hours'
ORDER BY created_at DESC
LIMIT 50;
```

What to look for:

- `created` rows from `actor='llm_extractor'` should be present and
  match what the conversation produced.
- `reinforced` rows mean the same insight reappeared — healthy signal.
- No `unlocked` actor='admin' rows; no `deleted` actor='admin' rows
  unless Daniel did `/olvidar`.

### Cost spot-check

```sql
SELECT
    DATE(created_at) AS day,
    COUNT(*)         AS rows,
    SUM(estimated_cost_usd) AS usd_spent
FROM llm_query_dispatches
WHERE tools_used::text LIKE '%insight_extractor%'
  AND created_at > now() - interval '7 days'
GROUP BY 1
ORDER BY 1 DESC;
```

Expected: a few cents per active day. If a day jumps to tens of cents,
investigate — extractor shouldn't fire on every message, only
post-query and post-clarification.

---

## Day 8 — Approval gate

### Pass criteria (all must hold)

1. **Memory accuracy:** Daniel reads `/memoria` once a day for 7 days
   and finds nothing materially wrong. "Materially wrong" = an insight
   that contradicts what he said or what the ledger shows.
2. **Privacy invariants:** redaction filter never logged a
   `user_insights.content`; no PII in stdout. (Spot-check a sample of
   30 minutes of API logs.)
3. **Cost stability:** extractor cost per day < $0.10 for personal use.
4. **No dispatcher regressions:** Sonnet cache hit rate per
   `docs/phase-6c/cache-verification.md` is within ±5pp of the
   pre-B11 baseline.
5. **No audit gaps:** every persisted insight has its `created` audit
   row; every locked insight has its `locked` row.

### If pass → flip the flag

Update Container Apps env-config:

```env
INSIGHTS_DISPATCHER_ENABLED=true
```

Re-deploy. This causes one one-time prompt cache miss (Decision #8),
then the cache settles back to its prior hit rate. Verify with the SQL
in `docs/phase-6c/cache-verification.md` 48 hours after the flip.

The flip closes B11 and unblocks B12. CLAUDE.md gets the flag-state
update in B12.

### If fail → rollback or extend

- **Material accuracy failure** (extractor hallucinates):
  - `INSIGHTS_EXTRACTOR_ENABLED=false`, leave dispatcher off.
  - Re-record fixtures in `tests/test_phase_6c_b4_*` against the bad
    cases.
  - Tighten `prompts/insight_extractor.py` and re-validate locally.
  - Run a fresh 7-day window. Do not flip dispatcher until extractor
    accuracy is back.
- **Privacy leak** (filter missed something):
  - Same: `INSIGHTS_EXTRACTOR_ENABLED=false`.
  - Patch `api/middleware/sensitive_redaction.py` and re-deploy.
  - Restart the window after a 24h soak.
- **Cost spike**:
  - Investigate which path is hot. Most likely culprit: an enqueue
    that runs on every Telegram message instead of post-query only.
  - Fix and continue the window without restarting (if the spike
    didn't corrupt data).
- **Cache regression > 5pp**:
  - Check that `compare_periods` is still last in the registry
    (Decision #8 anchor).
  - Confirm `_MEMORY_FEW_SHOT` and `_MEMORY_GUIDANCE` are NOT in the
    flag-off prompt; the flag-off prompt should be byte-identical to
    pre-B6 footprint.

---

## What this window does NOT cover

- Multi-tenant onboarding scenarios (P9).
- The dispatcher actually consuming memory in answers (B12).
- Concurrency / race conditions at scale (P9).
- Cross-user privacy (the privacy commitment is per-user; we don't
  test "user A can't see user B's memory" here because that is enforced
  at every read by `WHERE user_id = ?` and is regression-tested in B7
  + B8 unit suites).

---

## Cross-references

- `docs/phase-6c-decisions.md` — canonical decisions, including #8
  (cache discipline).
- `docs/phase-6c/cache-verification.md` — cache hit-rate measurement.
- `docs/phase-6c/privacy.md` — what the user is allowed to do with
  their memory and what we never log.
- `docs/phase-6c/deployment.md` — deploy steps incl. ACA Job for the
  nightly worker (B10).
- `scripts/test_phase_6c.sh` — curl smoke covering DELETE / export /
  admin recompute end-to-end.
