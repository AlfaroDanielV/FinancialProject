# Reset Dev Data — Wipe the Ledger, Keep Your Account

A runbook to clear all financial / transactional / bot data from the **local dev**
database while keeping your `users` row (login, `shortcut_token`, Telegram
pairing) so you don't have to re-onboard. Also includes a full factory-reset
variant.

> ⚠️ **Destructive and irreversible.** This empties the local Postgres tables and
> flushes Redis. Only run against your local dev stack
> (`postgres:16` + `redis:7` from `docker-compose.yml`). Never against prod.

All commands assume the repo root:
`~/Documents/Fiancial_agent/FinancialProject`.

---

## 0. Bring the stack up (if it isn't)

```bash
docker compose up -d db redis

# wait for Postgres to accept connections
for i in $(seq 1 20); do
  docker compose exec -T db pg_isready -U finance >/dev/null 2>&1 && { echo "db ready"; break; }
  sleep 1
done
```

DB connection (inside the compose network): user `finance`, db `finance`,
password `finance`. Host port is **5433** (container `5432`); Redis on `6379`.

---

## 1. (Optional) Inspect what's there first

`n_live_tup` is a stale estimate, so `ANALYZE` first to get real counts.

```bash
docker compose exec -T db psql -U finance -d finance -c "ANALYZE;" >/dev/null
docker compose exec -T db psql -U finance -d finance -c "
SELECT relname, n_live_tup
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY n_live_tup DESC, relname;"

# who are we keeping?
docker compose exec -T db psql -U finance -d finance -c \
  "SELECT id, email, full_name, telegram_user_id, status FROM users ORDER BY created_at;"
```

---

## 2. Wipe data, keep accounts  ← the process we ran

One transaction:
1. **Truncate every real table** except `users`, `alembic_version`, and
   `user_categories` (kept so the category picker stays populated).
   `pg_tables` lists only base tables, so the two materialized views are
   auto-excluded (you can't `TRUNCATE` a matview).
2. **Re-seed** the `global_default` notification rule for any user missing one
   (mirrors what registration creates: `advance_days = [7, 3, 1, 0]`).
3. **Refresh** the summary materialized views off the now-empty `transactions`.

```bash
docker compose exec -T db psql -U finance -d finance -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- 1) Wipe every table except the keep-set. CASCADE handles FK ordering
--    (e.g. notification_rules -> recurring_bills / custom_events).
DO $$
DECLARE stmt text;
BEGIN
  SELECT 'TRUNCATE TABLE ' || string_agg(format('%I', tablename), ', ')
         || ' RESTART IDENTITY CASCADE'
    INTO stmt
  FROM pg_tables
  WHERE schemaname = 'public'
    AND tablename NOT IN ('users', 'alembic_version', 'user_categories');
  RAISE NOTICE 'Truncating: %', stmt;
  EXECUTE stmt;
END $$;

-- 2) Re-seed the global_default notification rule for every user that lacks one.
INSERT INTO notification_rules (id, scope, advance_days, is_active, user_id, created_at, updated_at)
SELECT gen_random_uuid(), 'global_default', '[7, 3, 1, 0]'::jsonb, true, u.id, now(), now()
FROM users u
WHERE NOT EXISTS (
  SELECT 1 FROM notification_rules nr
  WHERE nr.user_id = u.id AND nr.scope = 'global_default'
);

COMMIT;

-- 3) Rebuild the summary materialized views (now empty).
REFRESH MATERIALIZED VIEW mv_monthly_summary_by_user;
REFRESH MATERIALIZED VIEW mv_yearly_summary_by_user;
SQL
```

### Flush Redis bot state

Clears pending writes, clarification / account-creation flows, query history,
device-login codes, and idempotency keys — anything that could reference
now-deleted rows.

```bash
docker compose exec -T redis redis-cli -n 0 FLUSHDB
```

### Verify

```bash
docker compose exec -T db psql -U finance -d finance -c "ANALYZE;" >/dev/null
# Only users(1), user_categories(N), notification_rules(1 per user), alembic_version(1) should remain:
docker compose exec -T db psql -U finance -d finance -c "
SELECT relname, n_live_tup
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY relname;"

docker compose exec -T redis redis-cli -n 0 DBSIZE   # expect 0
```

**Why these are kept**
- `users` — your login, `shortcut_token`, and `telegram_user_id` (no re-onboarding).
- `user_categories` — only referenced by `transactions` (wiped), so safe to keep;
  keeps the picker non-empty. *Add it to the `NOT IN (...)` list to drop it too.*
- `notification_rules` — points **out** to `recurring_bills` / `custom_events`,
  so it can't survive their CASCADE wipe; we truncate it and re-seed the one
  `global_default` row instead.
- `alembic_version` — schema version pointer; leave it (don't re-run migrations).

---

## 3. Alternative — Full factory reset (nukes everything incl. your account)

Use this only if you want to re-register from scratch (new `/start` in Telegram,
re-login the app, reconnect Gmail).

```bash
docker compose down -v            # destroys postgres_data + redis_data volumes
docker compose up -d db redis

# wait for db
for i in $(seq 1 20); do
  docker compose exec -T db pg_isready -U finance >/dev/null 2>&1 && break; sleep 1
done

# rebuild the schema from migrations (head = 0035)
# NOTE: migration 0006 (Phase 5a) SEEDS THE FIRST user from these .env vars —
# without them the upgrade stops at 0006 with "requires env var
# LEGACY_USER_EMAIL". `migrations/env.py` loads `.env`, so you just need them
# set there (already are): LEGACY_USER_EMAIL, LEGACY_USER_NAME,
# LEGACY_SHORTCUT_TOKEN. On a fresh DB this row becomes your account.
uv run alembic upgrade head
```

> If you exported `LEGACY_*` (or `DATABASE_URL`) in the shell, those win over
> `.env` (`env.py` uses `load_dotenv(override=False)`). Heads-up: a duplicate key
> in `.env` resolves to the LAST occurrence.

> `docker compose down -v` only removes the named volumes in `docker-compose.yml`
> (`postgres_data`, `redis_data`). The `financialproject_venv` volume is not
> referenced there and is left alone.

After a full reset the upgrade already **seeded your account** from the
`LEGACY_*` vars (email + `shortcut_token`) — but with **no Telegram link yet**. A
bare `/start` in Telegram won't recognize you: it cold-starts a *separate* new
user, and giving your real email hits *"ese email ya tiene una cuenta"* (the
documented pairing dead-end — the pairing endpoint needs auth). Break it with a
**pairing code**, authenticated by the `shortcut_token` already in `.env`:

```bash
# 1. Mint a pairing code (valid 5 min). Needs the API up — uvicorn on :8000.
TOKEN=$(grep -E '^LEGACY_SHORTCUT_TOKEN=' .env | tail -1 | cut -d= -f2-)
curl -sS -X POST http://localhost:8000/api/v1/users/me/telegram/pairing-code \
  -H "X-Shortcut-Token: $TOKEN"
# → {"code":"ABC123","expires_in_seconds":300}

# 2. In Telegram, within 5 min, send:  /start ABC123
```

Then `/setup`. (For the native app instead, `/login` in Telegram and paste the
code.)

**Skip pairing entirely:** if you don't need to keep your real email/token, set
`LEGACY_USER_EMAIL` to a throwaway address before the upgrade, then just `/start`
in Telegram to cold-start a brand-new user — no pairing code needed.

---

## Notes

- **App JWTs are stateless** (HMAC, not stored in DB). After the keep-account
  wipe your existing app session usually still works; if a call 401s, run
  `/login` in Telegram and paste the new code.
- **Gmail** is disconnected by the wipe (`gmail_credentials` cleared) — reconnect
  in-app when you want ingestion back.
- This file lives under `docs/`, which the root `.gitignore` excludes. To commit
  it: `git add -f docs/RESET_DEV_DATA.md`.
```
