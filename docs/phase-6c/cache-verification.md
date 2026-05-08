# Phase 6c B9 — Cache verification plan

The dispatcher system prompt carries memory guidance and one extra
few-shot as of B9. Decision #8 forbids inlining user insights — they
travel via the `get_user_context` tool result, not the prompt — so the
prompt should remain stable and the Anthropic ephemeral cache should
keep hitting at the same rate as pre-B9.

This doc is the operational checklist for verifying that.

---

## Pre-deploy invariants (already enforced by tests)

`tests/test_phase_6c_b9_system_prompt.py` locks:

- Prompt size ≤ `MAX_PROMPT_CHARS` (8500). Current build is ~6300.
- Two different users produce identical prompts modulo their first
  name. No user_id, last name, email, or insight payload leaks in.
- `_MEMORY_GUIDANCE` mentions the tool and the "cuánto gasté ayer"
  negative example.
- `_MEMORY_FEW_SHOT` contains both Example 6 (comparative) and
  Example 7 (recommendation).
- Static sections carry no `{...}` placeholders (those belong only in
  few-shots).
- `build_system_prompt(user, now)` is byte-stable for identical inputs.

If these tests pass, the prompt cache key is stable. If any of them
fail in CI, B9 isn't ready to ship.

---

## Baseline measurement (before merging B9 to prod)

Run on production database before the deploy. Window covers the last
7 full days; 24h alone is too noisy.

```sql
WITH window AS (
    SELECT
        SUM(total_input_tokens)         AS total_input,
        SUM(cache_read_input_tokens)    AS cache_read,
        SUM(cache_creation_input_tokens) AS cache_create,
        COUNT(*)                         AS dispatches
    FROM llm_query_dispatches
    WHERE created_at >= now() - interval '7 days'
      AND error IS NULL
      AND tools_used IS NOT NULL
      AND tools_used::text NOT LIKE '%insight_extractor%'
      AND tools_used::text NOT LIKE '%editar_memoria_parser%'
)
SELECT
    dispatches,
    total_input,
    cache_read,
    cache_create,
    ROUND(100.0 * cache_read / NULLIF(total_input, 0), 2)
        AS cache_hit_rate_pct
FROM window;
```

Record the resulting `cache_hit_rate_pct` somewhere durable (a comment
on the deploy PR is fine). The two `LIKE` exclusions remove extractor
and editor parser rows, which use a different Haiku prompt and would
muddy the dispatcher metric.

---

## Post-deploy monitoring (48-hour window)

Re-run the same query 48h after deploy. Compare to baseline.

| Result | Action |
|---|---|
| Hit rate within ±5pp of baseline | Pass. Done. |
| Hit rate 5–15pp below baseline | Investigate prompt drift. Check that the daily `_date_block` rotation is the only per-day change, and that no per-user content leaked in. Open a follow-up; do not roll back. |
| Hit rate >15pp below baseline | Roll back B9. Treat as regression and root-cause before retrying. |
| Hit rate above baseline | Lucky. Document why if you find a reason. |

---

## Why this matters

The Sonnet 4.5 dispatcher's per-query cost is dominated by cached input
tokens. A healthy hit rate (>70% on prompt input) keeps personal-use
spend under a dollar a day; dropping to no caching multiplies that by
the cache discount ratio (5–10x). Anything that silently breaks caching
hurts the cost model in production before it shows up in the dashboard.

The prompt is the cache key. If two consecutive dispatcher calls don't
get the same prompt, they don't share a cache entry. The B9 invariants
above guarantee that the prompt only changes when the calendar day
rolls over (because of the `_date_block`) — which is one cache miss per
user per day, amortized over many requests.

---

## Cross-references

- Decision #8: `docs/phase-6c-decisions.md` — "Cache discipline — insights via tool, not prompt".
- B9 prompt content: `app/queries/prompts/system.py`.
- B9 tests: `tests/test_phase_6c_b9_system_prompt.py`.
- Telemetry source: `llm_query_dispatches` (migration 0009 + 0010 cache columns).
