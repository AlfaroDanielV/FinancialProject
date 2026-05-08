# Phase 6c — Privacy commitment for the user-memory layer

This document is the substantive privacy contract for `user_insights`.
It will become the basis of the public privacy policy in P9. Until
then, it is the operational reference.

The commitments below are backed by code, not by intent. Every claim
either points at a function or a test that enforces it.

---

## What we store

`user_insights` holds typed, expirable insights about the user's
finances and stated preferences. Three sources only:

- `computed` — deterministic SQL aggregates from your own ledger rows.
- `llm_extracted` — Haiku-derived signals from short windows of your
  recent conversation with the bot.
- `user_override` — what you wrote into `/editar_memoria`.

Every row carries a `valid_until`. Read paths filter by it; rows whose
`valid_until` is in the past don't surface in `/memoria` or in the
dispatcher's `get_user_context` tool. Lifecycle worker hard-deletes
unlocked rows 30 days after expiry.

`user_locked=true` rows (from `/editar_memoria`) are exempt from
automatic mutation and from automatic deletion. Only you can change or
remove them.

`stated_preference.raw_quote` is auto-truncated to 280 characters at
write time and **wiped by the lifecycle worker after 30 days**, leaving
only `topic` and `stance` behind.

---

## What you can do

### Inspect — `/memoria` (Telegram)

Lists every active insight in plain Spanish, grouped by category. Never
shows JSON or jargon. Confidence < 0.5 surfaces with a `⚠️` disclaimer.

### Correct — `/editar_memoria` (Telegram)

Pick any editable insight (preferences, goals, archetype, risk posture,
decision style, financial literacy). Tell the bot how to remember it.
Result is persisted with `source='user_override'`, `user_locked=true`,
confidence 1.0. Computed-only insights cannot be corrected by design —
they reflect your real ledger, not a stated preference. You can still
delete them.

### Forget — `/olvidar` (Telegram)

Single delete (per group, per insight) with one confirmation tap.
`/olvidar todo` requires **two** confirmation taps and shows you what
you are about to delete before the second one fires. Each deletion
emits an audit row with the snapshot at deletion time (with
`raw_quote` redacted) so a future "undo within audit retention" is
possible if we add it. The deleted row itself is gone immediately.

### Export — `GET /api/v1/users/me/insights/export`

Auth: `X-Shortcut-Token` (your real token; the dev shim is rejected).

Returns a JSON dump of every insight we have for you (active by
default, plus expired-not-yet-purged). The shape:

```json
{
  "user_id": "...",
  "exported_at": "<iso>",
  "format_version": 1,
  "request_id": "...",
  "include_expired": true,
  "count": <int>,
  "insights": [
    {
      "id": "...",
      "insight_type": "...",
      "content": { ... },
      "confidence": "...",
      "source": "computed | llm_extracted | user_override",
      "valid_until": "<iso>",
      "user_locked": <bool>,
      "dedup_key": "...",
      "reinforcement_count": <int>,
      "last_reinforced_at": "<iso|null>",
      "created_at": "<iso>",
      "updated_at": "<iso>"
    }
  ]
}
```

Streaming kicks in over 2000 rows. Below that you get a single
JSONResponse. Either way, an `exported` audit row is written with
`{count, format='json', request_id}`.

Query: `?include_expired=false` to exclude expired-but-not-yet-purged
rows. Default is `true` because the export is a privacy primitive —
you should see exactly what is still in the database.

### Delete — `DELETE /api/v1/users/me/insights`

Auth: `X-Shortcut-Token`.

Hard-deletes every insight row for the caller. Per-row audit (one
`AuditPayloadDeleted` row per insight, with `deletion_reason='api_delete_my_insights'`).
Returns `{"deleted": <count>}`.

Audit rows survive — that's the timeline of what happened to your
memory. The insight content snapshots inside audit rows have
`raw_quote` redacted.

---

## What we do NOT log

The repository ships `api/middleware/sensitive_redaction.py`, a logging
filter installed on the root logger from the FastAPI lifespan. Before
any record reaches stdout (and from there the orchestrator's log
collector), it walks the record's args and extras and replaces any
value keyed under one of:

- `content`
- `content_at_deletion`
- `previous_content`
- `new_content`

with the literal string `[redacted]`. Nested dicts get walked too, so
embedded payloads inside `payload` or `args_summary` are caught.

This is enforced by `tests/test_phase_6c_b8_privacy.py`. See the
filter source for the conservative match rule (key name only, not
shape).

---

## What we do NOT do

- We do not share your insights with another user. There is no
  cross-user learning in Phase 6c. Federated insights are a P9 SaaS
  hardening question.
- We do not embed your past chats. Vector recall over conversation
  history is explicitly out of scope (see `docs/phase-6c-decisions.md`).
- We do not use your insights to train a model. The Haiku extractor
  reads your conversation; it does not contribute to model weights.
- We do not silently change a row you locked. `user_locked=true` is
  sacred — the persister and the lifecycle worker both skip those rows
  and they survive `/olvidar todo` confirmations the same as any
  other row (the user always pulls the trigger).

---

## Audit trail

Every state change writes one typed `user_insights_audit` row. Actions:

| action | written by |
|---|---|
| `created` | persister (computed worker, LLM extractor, user override) |
| `updated` | persister |
| `reinforced` | persister |
| `deleted` | `/olvidar` (single + todo), `DELETE /api/v1/users/me/insights`, lifecycle hard-purge |
| `locked` | `/editar_memoria` flow, when a row transitions to user_override |
| `exported` | `GET /api/v1/users/me/insights/export` |

The audit timeline is the source of truth for "what happened to my
memory" questions. Audit rows reference the (now possibly hard-deleted)
insight UUID and outlive the data.

---

## Future commitments (P9)

- Public privacy policy derived from this file.
- Per-user export of audit rows (today the audit table is operator-only).
- Configurable retention windows.
- Sharing primitives for couples (gated, opt-in, never on by default).

The current commitments are the floor, not the ceiling.
