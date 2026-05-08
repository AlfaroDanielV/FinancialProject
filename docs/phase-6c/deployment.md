# Phase 6c — Deployment

Deploys the user-memory layer to production. Builds on the Phase 6b
deployment (Azure Container Apps, Key Vault, Postgres Flexible Server,
ACR). Most of the surface is already present; B10 adds one new ACA Job
and one new admin endpoint.

---

## Components shipping in Phase 6c

| Component | Where it runs | Trigger |
|---|---|---|
| `user_insights` + `user_insights_audit` schema | PostgreSQL | Migrations 0013, 0014 |
| Computed writer + persister + lifecycle | Co-located with API | Imported by worker + admin endpoint |
| Haiku extractor (B4) | API process (post-query, post-clarification hook) | `INSIGHTS_EXTRACTOR_ENABLED=true` |
| `get_user_context` tool (B6) | Inside the Sonnet dispatcher | Tool-use loop |
| `/memoria`, `/olvidar`, `/editar_memoria` (B7) | Bot process | Telegram commands |
| Privacy endpoints (B8) | API process | DELETE / GET on `/api/v1/users/me/insights*` |
| Redaction logging filter (B8) | API process | Installed in lifespan |
| **Nightly worker (B10)** | **Azure Container Apps Job** | **Cron `30 9 * * * UTC`** |
| **Admin endpoint (B10)** | **API process** | **`POST /api/v1/admin/insights/run-nightly`** |

The lifecycle-only worker (`workers/insights_lifecycle.py`) is kept for
ad-hoc lifecycle reruns; in production the nightly worker subsumes it.

---

## Deploy steps

### 1. Apply migrations

The 6c migrations should already be present from earlier blocks; verify:

```bash
az containerapp job execute \
    --resource-group <rg> \
    --name finance-migrate \
    --image <registry>/<image>:<tag>
# or run alembic from a one-shot pod / locally with prod DATABASE_URL
alembic upgrade head
```

Expected head: includes `0014_phase6c_insight_extractor_tracking`.

### 2. Deploy API + bot image

API + bot share the same image. Push a new build that includes:

- `api/routers/admin_insights.py`
- `api/routers/privacy_insights.py`
- `api/middleware/sensitive_redaction.py`
- `bot/memory_handlers.py`

```bash
az acr build --registry <acr> --image finance-api:<tag> -f Dockerfile.prod .
az containerapp update \
    --resource-group <rg> \
    --name finance-api \
    --image <acr>.azurecr.io/finance-api:<tag>
```

Re-register the Telegram webhook on deploy if `TELEGRAM_MODE=webhook`.

### 3. Build the worker image

The worker image is built from `Dockerfile.worker`. Same Dockerfile
backs both the Gmail daily worker and the new insights nightly worker;
the per-job YAML overrides `command/args`.

```bash
az acr build --registry <acr> --image finance-worker:<tag> -f Dockerfile.worker .
```

### 4. Create the nightly ACA Job

Replace placeholders (`<sub>`, `<rg>`, `<env>`, `<kv-name>`,
`<registry>/<image>:<tag>`) in
`infra/azure/container-apps-job-insights.yaml`, then:

```bash
az containerapp job create \
    --resource-group <rg> \
    --name finance-insights-nightly \
    --environment <ca-env> \
    --yaml infra/azure/container-apps-job-insights.yaml
```

Grant the job's system-assigned identity:

- `Get` and `List` on Key Vault secrets (`database-url`, `redis-url`).
- `AcrPull` on the container registry.

Verify the job is registered:

```bash
az containerapp job show \
    --resource-group <rg> \
    --name finance-insights-nightly \
    --query "properties.configuration.scheduleTriggerConfig.cronExpression"
# expected: "30 9 * * *"
```

### 5. Smoke-test the new job manually

Trigger one execution before the first scheduled fire:

```bash
az containerapp job start \
    --resource-group <rg> \
    --name finance-insights-nightly
```

Tail logs:

```bash
az containerapp job execution list \
    --resource-group <rg> \
    --name finance-insights-nightly \
    --query "[0].name" -o tsv
# then:
az containerapp job logs show \
    --resource-group <rg> \
    --name finance-insights-nightly \
    --execution <execution-id>
```

Expected closing line:

```
insights_nightly_done users=N ok=N failed=0 created=... updated=... ...
```

### 6. Smoke-test the admin endpoint

```bash
curl -X POST \
    -H "X-Shortcut-Token: $SHORTCUT_TOKEN" \
    "https://<api-host>/api/v1/admin/insights/run-nightly" | jq
```

Expected shape:

```json
{
  "user_id": "...",
  "insights_proposed": 6,
  "persisted": { "created": 6, "updated": 0, "reinforced": 0, "skipped_locked": 0, "skipped_user_override": 0 },
  "gaps":      { "emitted": 1, "created": 1, "updated": 0, "reinforced": 0, "expired_unsupported": 0 }
}
```

### 7. Smoke-test `/recalcular_memoria` from Telegram

Send `/recalcular_memoria` to the bot. Expected sequence:

1. `Empecé a recalcular tu memoria. Te aviso cuando termine.`
2. `Listo, recalculé tu memoria. Insights nuevos o actualizados: N · Banderas detectadas: M · Mandá /memoria para verla.`

Cooldown: a second `/recalcular_memoria` within an hour replies with
`Hace poco que recalculé tu memoria. Probá de nuevo en X minutos…`.

---

## Operational runbook

### How to verify nightly is firing

The job appears in execution history with `status=Succeeded` once per
day:

```bash
az containerapp job execution list \
    --resource-group <rg> \
    --name finance-insights-nightly \
    --query "[].{name:name,status:properties.status,startTime:properties.startTime}" -o table
```

If you see `Failed`, pull the logs (step 5) and look for the per-user
error stack — the worker logs each failed user_id but keeps going.

### How to skip a night (maintenance window)

```bash
az containerapp job stop \
    --resource-group <rg> \
    --name finance-insights-nightly
# resume:
az containerapp job start \
    --resource-group <rg> \
    --name finance-insights-nightly
```

### Order of operations relative to Gmail

| UTC | Local CR | Job |
|---|---|---|
| 09:00 | 03:00 | `finance-gmail-daily` (cron `0 9 * * *`) |
| 09:30 | 03:30 | `finance-insights-nightly` (cron `30 9 * * *`) |

The 30-minute gap is intentional: the insights worker reads the same
ledger the Gmail scan just refreshed. If the Gmail run finishes faster
than 30 min (it usually does), the gap is wasted; if it runs long, the
insights run still has fresh data because they share the DB. Either way
the order is preserved.

### Cache verification

The B9 cache hit rate can drift after this deploy if the prompt was
modified — see `docs/phase-6c/cache-verification.md` for the SQL query
and the 48h rollback threshold.

---

## What NOT to do

- **Do not give the worker job an Anthropic key.** The nightly path is
  pure SQL aggregates; Decision #2 forbids the LLM extractor running
  here. Keeping the secret out enforces it.
- **Do not raise `parallelism` above 1.** The worker iterates users
  sequentially; running two replicas just doubles the per-user
  contention without reducing wall-clock time.
- **Do not flip `INSIGHTS_DISPATCHER_ENABLED=true` as part of B10.**
  That gate belongs to B11/B12 after the 7-day shadow window.
