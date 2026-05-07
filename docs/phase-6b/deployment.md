# Phase 6b — Deployment of the daily Gmail worker

The daily Gmail scan runs as an Azure Container Apps **Job** — not a
long-lived Container App. Jobs are designed exactly for this pattern:
spin up, run a script, exit, get billed only for the runtime.

> **Audience**: Daniel (or whoever owns the Azure subscription for the
> personal MVP).

## What the worker does

`workers/gmail_daily.py` iterates every active `gmail_credentials` row,
runs `scan_user_inbox(mode="daily")` per user, and lets the notifier
(C.1) produce shadow / batch / per-transaction Telegram messages.

Cron: **`0 9 * * * UTC`** = 3am Costa Rica (UTC-6, no DST).

## One-time setup

Assumed pre-existing:
- A resource group (e.g. `finance-rg`).
- A Container Apps Environment (e.g. `finance-env`).
- A Key Vault with secrets pre-populated (see list in
  `infra/azure/container-apps-job.yaml`).
- An Azure Container Registry with the worker image.

### 1. Build and push the image

```bash
# from project root
az acr login --name <your-acr>
docker build -f Dockerfile.worker -t <acr>.azurecr.io/finance-worker:0.1.0 .
docker push <acr>.azurecr.io/finance-worker:0.1.0
```

### 2. Edit the job spec

Open `infra/azure/container-apps-job.yaml` and replace placeholders:

- `<sub>` — Azure subscription id
- `<rg>` — resource group name
- `<env>` — Container Apps Environment name
- `<kv-name>` — Key Vault name (no `.vault.azure.net` suffix)
- `<registry>/<image>:<tag>` — full ACR image reference matching step 1

### 3. Create the Job

```bash
az containerapp job create \
    --resource-group finance-rg \
    --name finance-gmail-daily \
    --environment finance-env \
    --yaml infra/azure/container-apps-job.yaml
```

This creates the job in **scheduled** mode. It does NOT run yet — it
runs at the next cron tick.

### 4. Grant the managed identity Key Vault access

Get the Job's managed identity principal id:

```bash
az containerapp job show -g finance-rg -n finance-gmail-daily \
    --query identity.principalId -o tsv
```

Grant it `Get` on KV secrets:

```bash
az keyvault set-policy --name <kv-name> \
    --object-id <principal-id> \
    --secret-permissions get list
```

If using RBAC instead of access policies:

```bash
az role assignment create \
    --assignee <principal-id> \
    --role "Key Vault Secrets User" \
    --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<kv-name>
```

### 5. Grant ACR pull

```bash
az role assignment create \
    --assignee <principal-id> \
    --role AcrPull \
    --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ContainerRegistry/registries/<acr>
```

## Manual triggers

### From the API (admin endpoint)

`POST /api/v1/gmail/admin/run-daily` runs the same code path
in-process (auth via `current_user`, see `api/routers/gmail.py`).
Useful when you want to test without waiting for cron, but note that
this fires inside the API process — long scans will tie up an event
loop slot. For prod-realistic testing, use the Azure CLI trigger below.

### From the Azure CLI

```bash
az containerapp job start \
    --resource-group finance-rg \
    --name finance-gmail-daily
```

That kicks off a one-shot replica with the same env / image as the
scheduled invocation. See its logs with:

```bash
az containerapp job execution list \
    -g finance-rg -n finance-gmail-daily \
    --query "[0].name" -o tsv
# then:
az containerapp job logs show \
    -g finance-rg -n finance-gmail-daily \
    --execution <execution-name>
```

## Updating the cron schedule

Edit `cronExpression` in the YAML and re-apply:

```bash
az containerapp job update \
    -g finance-rg -n finance-gmail-daily \
    --yaml infra/azure/container-apps-job.yaml
```

## Suspending the job

```bash
az containerapp job update \
    -g finance-rg -n finance-gmail-daily \
    --triggers schedule.suspend=true
```

(Or set `parallelism: 0` in the YAML and re-apply.)

## Monitoring

The worker writes structured INFO logs:

- `daily_run_started users=N`
- `daily_done user=<uuid> scanned=N created=N matched=N skipped=N`
- `daily_scan_error user=<uuid>` (on per-user exception)
- `daily_run_completed users=N`

Container Apps captures stdout to Log Analytics by default. A simple
KQL query for the last run:

```kql
ContainerAppConsoleLogs_CL
| where ContainerJobName_s == "finance-gmail-daily"
| where TimeGenerated > ago(24h)
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

## Rolling back

Each invocation is independent — there's no migration to roll back. If
a worker version is shipping bad data:

1. Suspend the cron (above).
2. Investigate via the admin endpoint or local `uv run python -m workers.gmail_daily`.
3. Roll image tag back: `az containerapp job update --image <registry>/<image>:<previous-tag>`.

The `gmail_messages_seen` table records `outcome` for every processed
message; bad ingests can be quarantined with
`UPDATE ... SET outcome='rejected_by_user'` and the reconciler will not
re-process them on the next run.
