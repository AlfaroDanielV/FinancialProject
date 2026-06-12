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
