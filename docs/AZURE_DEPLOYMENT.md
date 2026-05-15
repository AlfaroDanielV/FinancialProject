# Azure Deployment Cookbook — Centro Financiero

> **Context (per `~/Finance_project/30_Projects/Finance-Agent/09_Operations/Deployment-State.md`):**
> Your Phase-5 deploy is live but **Phases 6a-6e have never been
> deployed.** This cookbook walks the first Phase-6 production push on
> top of the existing Phase-5 Azure footprint. The base stack
> (Postgres, Redis, ACR, Container Apps Environment, the api/bot
> Container App) is already there; the bot has been running locally in
> `TELEGRAM_MODE=polling`, so the webhook needs a fresh secret + URL
> on cutover.
>
> No CI/CD assumed; everything in §1–§14 is the manual path. §15 is the
> CI/CD blueprint when you're ready to wire it.
>
> Operational ground truth lives in the vault note above — update it
> whenever this cookbook moves. Save the names you pick in §3; every
> resource is referenced from multiple steps.

---

## 1. At a glance

What you're standing up:

```
┌──────────────────────┐     ┌──────────────────────┐
│  Telegram (webhook)  │     │  iPhone / Android    │
│  /start, /setup, …   │     │  (PWA, /memoria, …)  │
└──────────┬───────────┘     └──────────┬───────────┘
           │ HTTPS                       │ HTTPS
           ▼                             ▼
┌──────────────────────────────────────────────────┐
│   Azure Container Apps  —  api + bot (1 image)   │
│   uvicorn api.main:app   (Dockerfile.prod)       │
└──────┬──────────┬──────────────┬─────────────────┘
       │          │              │
       │      ┌───▼─────┐   ┌────▼────┐
       │      │ Redis   │   │ Postgres │
       │      │ Basic C0│   │ Flexible │
       │      └─────────┘   │ B1ms     │
       │                    └──────────┘
       │
       │  (read-only static, separate origin)
       ▼
┌──────────────────────────┐     ┌──────────────────────┐
│  Static Web Apps         │     │  Container Apps Job  │
│  Centro Financiero SPA   │     │  Gmail daily worker  │
│  (web/dist + sw.js)      │     │  cron 0 9 * * * UTC  │
└──────────────────────────┘     └──────────────────────┘
                  │
                  └───── shares parent domain with API
                         (for `fa_session` cookie)
```

Resources you'll touch:

| Resource | SKU | Phase 5 had it? | Notes |
|---|---|---|---|
| Resource Group | — | yes | Reuse |
| Container Registry | Basic | yes | Reuse |
| Container Apps Environment | Consumption | yes | Reuse |
| Container App: api/bot | minScale=1, maxScale=3 | yes | **redeploy** |
| Container Apps Job: gmail-daily | cron 0 9 * * * UTC | **new** | Phase 6b |
| Container Apps Job: migrate | manual | **new** | Pre-deploy migrations |
| PostgreSQL Flexible Server | Burstable B1ms | yes | **run migrations** |
| Redis Cache | Basic C0 | yes | Reuse |
| Key Vault | Standard | yes | **add new secrets** |
| Static Web Apps | Free | **new** | Phase 6d SPA |
| Managed Identity (Container Apps) | System or User | yes | Add Key Vault role |

**Cost ballpark** (with traffic from you only): ~$50–70/mo. Postgres
B1ms is the floor (~$15), Redis C0 (~$16), Container Apps Consumption
billing is ~$5–15 with minScale=1, ACR Basic (~$5), Static Web Apps
free, Key Vault (~$1). Anthropic API spend is separate and depends on
extraction + query traffic; cache discipline keeps it under $5/mo at
your usage.

---

## 2. Delta since Phase 5

If your Phase 5 deploy is still up, **most resources stay**. These are
the diffs that matter operationally.

**What carries over from Phase 5d:** Postgres Flexible Server, Redis
Cache, Container Registry, Container Apps Environment, the api/bot
Container App (running an old image — you'll replace it in §7), Key
Vault. Phase 5d shipped the nudges evaluator and delivery job; that
infrastructure is reused.

**What's net-new and being deployed for the first time:**

- Gmail daily Container Apps Job (Phase 6b)
- Migrate Container Apps Job (Phase 6b convention; was ad-hoc before)
- Static Web Apps + custom domain for the SPA (Phase 6d B4 onward)
- New Key Vault secrets for magic-link auth, Gmail OAuth, insights
- Materialized views in Postgres (Phase 6e B2, migration `0017`)

**Cutover hazard (per the vault deployment note):** the bot has been
running locally in `TELEGRAM_MODE=polling`, so any previously-set
webhook on Telegram's side is stale. Before pushing the new image:

```bash
# Regenerate the webhook secret so polling sessions can't leak it
TELEGRAM_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
az keyvault secret set --vault-name "$KV" \
  --name TELEGRAM-WEBHOOK-SECRET --value "$TELEGRAM_WEBHOOK_SECRET"

# Optional: tell Telegram to drop the old webhook first. Not strictly
# required — set_webhook() on lifespan will overwrite it anyway.
TG_TOKEN=$(az keyvault secret show --vault-name "$KV" \
  --name TELEGRAM-BOT-TOKEN --query value -o tsv)
curl -s "https://api.telegram.org/bot${TG_TOKEN}/deleteWebhook?drop_pending_updates=true" | jq
```

Once `TELEGRAM_MODE=webhook` + `TELEGRAM_WEBHOOK_URL` + the new secret
are set in the Container App (§7), the bot calls `set_webhook()`
automatically on lifespan startup (see `bot/app.py:131`). No manual
register endpoint to hit.

### Schema (Alembic head: `0020`)

Run migrations after redeploy. Heads added since Phase 5:

- `0006` — multi-tenant users (Phase 5a)
- `0007–0008` — LLM extractions, nudges (Phase 5b/5d)
- `0009–0010` — query dispatcher telemetry (Phase 6a)
- `0011–0012` — Gmail ingestion (Phase 6b)
- `0013–0015` — user insights (Phase 6c) + Gmail discovery
- `0016` — magic links + recurring incomes (Phase 6d)
- `0017` — Centro Financiero base (Phase 6e B2): goals, transfers,
  user_categories, currency_rates, materialized views
- `0018` — `transactions.archived` (Phase 6e B5)
- `0019` — `debts.archived` (Phase 6e B7)
- `0020` — `recurring_incomes.archived` (Phase 6e B8)

### New env vars (all in `api/config.py`)

| Key | Required? | Phase | What |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | 5b | Haiku + Sonnet |
| `LLM_EXTRACTION_MODEL` | no | 5b | default `claude-haiku-4-5` |
| `LLM_QUERY_MODEL` | no | 6a | default `claude-sonnet-4-5` |
| `LLM_DAILY_TOKEN_BUDGET_PER_USER` | no | 6a | default `100000` |
| `INSIGHTS_EXTRACTOR_ENABLED` | no | 6c | default `false` |
| `INSIGHTS_DISPATCHER_ENABLED` | no | 6c | default `false`; flip true after Daniel's shadow review |
| `TELEGRAM_MODE` | yes | 5b | `webhook` in prod |
| `TELEGRAM_BOT_TOKEN` | yes | 5b | from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | yes | 5b | random 32-byte token |
| `TELEGRAM_WEBHOOK_URL` | yes | 5b | `https://<api-fqdn>/api/v1/telegram/webhook` |
| `GMAIL_CLIENT_ID` | yes (Phase 6b) | 6b | GCP OAuth client |
| `GMAIL_CLIENT_SECRET` | yes | 6b | |
| `GMAIL_REDIRECT_URI` | yes | 6b | `https://<api-fqdn>/api/v1/gmail/oauth/callback` |
| `GMAIL_OAUTH_STATE_SECRET` | yes | 6b | random 32-byte token |
| `SECRET_STORE_BACKEND` | yes | 6b | `azure_kv` in prod |
| `AZURE_KEY_VAULT_URL` | yes | 6b | `https://<vault>.vault.azure.net/` |
| `SPA_BASE_URL` | yes | 6d | SWA URL (no trailing slash) |
| `SPA_CORS_ORIGINS` | no | 6d | comma list; defaults to `SPA_BASE_URL` |
| `MAGIC_LINK_SESSION_SECRET` | yes | 6d | random 64-byte token |
| `MAGIC_LINK_TTL_S` | no | 6d | default `1800` (30 min) |
| `SESSION_COOKIE_NAME` | no | 6d | default `fa_session` |
| `SESSION_COOKIE_TTL_S` | no | 6d | default `14400` (4 h) |
| `SESSION_COOKIE_DOMAIN` | **prod-yes** | 6d | parent domain shared by API + SPA, e.g. `.centro.tudominio.cr` |
| `SESSION_COOKIE_SECURE` | **prod-yes** | 6d | `true` |
| `BCRYPT_ROUNDS` | no | 6d | default `12` |
| `ENVIRONMENT` | yes | 5a | `production` |
| `SECRET_KEY` | yes | 5a | random 64-byte token |

### New container image: Gmail daily worker

Phase 6b ships a second image (`Dockerfile.worker`) that runs
`workers.gmail_daily` once per invocation. It's deployed as a Container
Apps **Job**, not an App.

### New SPA: Centro Financiero

Phase 6d + 6e ship a React/Vite/Tailwind SPA under `web/`. Deployed to
**Azure Static Web Apps**, separate origin from the API. They MUST share
a parent domain (e.g. `centro.tudominio.cr` + `api.centro.tudominio.cr`)
so the `fa_session` cookie set by `/api/v1/auth/magic-link/exchange` is
sent on SPA requests.

---

## 3. Names you'll need

Pick before you start. Fill in your real values; the rest of this doc
uses these placeholders.

```bash
# Edit these once. Re-source this file in any shell that runs az/gh.
export AZ_SUB="<your-subscription-id>"
export RG="ledger-cr-prod"
export REGION="eastus2"
export ACR="ledgercrcrprod"           # 5–50 alphanumeric, globally unique
export PG="ledger-cr-pg"
export PG_DB="finance"
export PG_ADMIN="financeadmin"
export REDIS="ledger-cr-redis"
export ENV_CAE="ledger-cr-env"        # Container Apps Environment
export APP_API="ledger-cr-api"        # the api/bot Container App
export JOB_GMAIL="ledger-cr-gmail-daily"
export JOB_MIGRATE="ledger-cr-migrate"
export KV="ledger-cr-kv"              # 3–24 chars, globally unique
export SWA="ledger-cr-spa"            # Static Web App
export DOMAIN_ROOT="tudominio.cr"     # your parent domain
export DOMAIN_API="api.centro.${DOMAIN_ROOT}"
export DOMAIN_SPA="centro.${DOMAIN_ROOT}"
export DOMAIN_COOKIE=".centro.${DOMAIN_ROOT}"  # parent for cross-origin cookie
```

```bash
az account set --subscription "$AZ_SUB"
```

---

## 4. Provision the new resources (idempotent)

If a resource already exists from Phase 5, `az` returns the existing
one — these commands are safe to re-run. Only the **bolded** lines are
new since Phase 5.

```bash
# Resource group (reuse)
az group create -n "$RG" -l "$REGION"

# Container Registry (reuse)
az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true

# Postgres Flexible Server + DB (reuse — but check version + extensions)
# pg_trgm is used by recon (Phase 6b). gen_random_uuid() needs pgcrypto.
az postgres flexible-server create -g "$RG" -n "$PG" -l "$REGION" \
  --tier Burstable --sku-name Standard_B1ms --version 16 \
  --admin-user "$PG_ADMIN" --admin-password "$(openssl rand -base64 24)" \
  --database-name "$PG_DB" --public-access 0.0.0.0   # tighten in §6

az postgres flexible-server execute -n "$PG" --admin-user "$PG_ADMIN" \
  -d "$PG_DB" \
  --querytext 'CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;'

# Redis (reuse)
az redis create -g "$RG" -n "$REDIS" -l "$REGION" --sku Basic --vm-size C0

# Key Vault (reuse — but you'll add new secrets in §6)
az keyvault create -g "$RG" -n "$KV" -l "$REGION"

# Container Apps Environment (reuse)
az containerapp env create -g "$RG" -n "$ENV_CAE" -l "$REGION"

# ───────── NEW since Phase 5 ─────────

# Static Web App (Free tier)
az staticwebapp create -g "$RG" -n "$SWA" -l "$REGION" --sku Free \
  --source "" --branch ""   # we'll deploy from CI/CD later, free of repo

# Capture the SWA deploy token — you'll need this in §10 (CI/CD)
SWA_TOKEN=$(az staticwebapp secrets list -g "$RG" -n "$SWA" \
  --query "properties.apiKey" -o tsv)
echo "AZURE_STATIC_WEB_APPS_API_TOKEN=$SWA_TOKEN"
```

---

## 5. Build and push images

```bash
# Login to ACR
az acr login -n "$ACR"

# API + bot image
docker build -f Dockerfile.prod -t $ACR.azurecr.io/centro-api:phase6e-b13 .
docker push $ACR.azurecr.io/centro-api:phase6e-b13

# Gmail daily worker image
docker build -f Dockerfile.worker -t $ACR.azurecr.io/centro-worker:phase6e-b13 .
docker push $ACR.azurecr.io/centro-worker:phase6e-b13
```

If you're on an M-series Mac, force amd64 (Container Apps runs amd64):

```bash
docker buildx build --platform linux/amd64 -f Dockerfile.prod \
  -t $ACR.azurecr.io/centro-api:phase6e-b13 --push .
docker buildx build --platform linux/amd64 -f Dockerfile.worker \
  -t $ACR.azurecr.io/centro-worker:phase6e-b13 --push .
```

Tag convention: `<phase>-<block>`. Bumps with every block.

---

## 6. Secrets in Key Vault

Fresh secrets first (`openssl` on Linux/macOS):

```bash
SECRET_KEY=$(openssl rand -base64 64)
MAGIC_LINK_SESSION_SECRET=$(openssl rand -base64 64)
TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)
GMAIL_OAUTH_STATE_SECRET=$(openssl rand -hex 32)
```

Drop everything in Key Vault:

```bash
az keyvault secret set --vault-name "$KV" --name "DATABASE-URL" \
  --value "postgresql+asyncpg://${PG_ADMIN}:<pg_pwd>@${PG}.postgres.database.azure.com:5432/${PG_DB}?sslmode=require"

REDIS_PRIMARY=$(az redis list-keys -g "$RG" -n "$REDIS" --query "primaryKey" -o tsv)
REDIS_HOST=$(az redis show -g "$RG" -n "$REDIS" --query "hostName" -o tsv)
az keyvault secret set --vault-name "$KV" --name "REDIS-URL" \
  --value "rediss://:${REDIS_PRIMARY}@${REDIS_HOST}:6380/0"

az keyvault secret set --vault-name "$KV" --name "ANTHROPIC-API-KEY"        --value "<your-anthropic-key>"
az keyvault secret set --vault-name "$KV" --name "TELEGRAM-BOT-TOKEN"       --value "<from-botfather>"
az keyvault secret set --vault-name "$KV" --name "TELEGRAM-WEBHOOK-SECRET"  --value "$TELEGRAM_WEBHOOK_SECRET"
az keyvault secret set --vault-name "$KV" --name "SECRET-KEY"               --value "$SECRET_KEY"
az keyvault secret set --vault-name "$KV" --name "MAGIC-LINK-SESSION-SECRET" --value "$MAGIC_LINK_SESSION_SECRET"
az keyvault secret set --vault-name "$KV" --name "GMAIL-CLIENT-ID"          --value "<from-gcp>"
az keyvault secret set --vault-name "$KV" --name "GMAIL-CLIENT-SECRET"      --value "<from-gcp>"
az keyvault secret set --vault-name "$KV" --name "GMAIL-OAUTH-STATE-SECRET" --value "$GMAIL_OAUTH_STATE_SECRET"
```

Grant the Container App's managed identity `Key Vault Secrets User`:

```bash
# Enable system-assigned identity on the app first (after §7)
APP_PRINCIPAL=$(az containerapp identity assign \
  -g "$RG" -n "$APP_API" --system-assigned \
  --query "principalId" -o tsv)
KV_ID=$(az keyvault show -g "$RG" -n "$KV" --query "id" -o tsv)
az role assignment create --role "Key Vault Secrets User" \
  --assignee "$APP_PRINCIPAL" --scope "$KV_ID"
```

Repeat for the worker Job's managed identity (after §8).

---

## 7. Deploy the api/bot Container App

```bash
ACR_LOGIN=$(az acr show -n "$ACR" --query "loginServer" -o tsv)

az containerapp create \
  -g "$RG" -n "$APP_API" --environment "$ENV_CAE" \
  --image "$ACR_LOGIN/centro-api:phase6e-b13" \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 3 \
  --cpu 0.5 --memory 1.0Gi \
  --registry-server "$ACR_LOGIN" \
  --registry-identity system

# Switch to Key-Vault-backed secrets (run AFTER the role assignment in §6)
az containerapp secret set -g "$RG" -n "$APP_API" --secrets \
  "database-url=keyvaultref:https://${KV}.vault.azure.net/secrets/DATABASE-URL,identityref:system" \
  "redis-url=keyvaultref:https://${KV}.vault.azure.net/secrets/REDIS-URL,identityref:system" \
  "anthropic-api-key=keyvaultref:https://${KV}.vault.azure.net/secrets/ANTHROPIC-API-KEY,identityref:system" \
  "telegram-bot-token=keyvaultref:https://${KV}.vault.azure.net/secrets/TELEGRAM-BOT-TOKEN,identityref:system" \
  "telegram-webhook-secret=keyvaultref:https://${KV}.vault.azure.net/secrets/TELEGRAM-WEBHOOK-SECRET,identityref:system" \
  "secret-key=keyvaultref:https://${KV}.vault.azure.net/secrets/SECRET-KEY,identityref:system" \
  "magic-link-session-secret=keyvaultref:https://${KV}.vault.azure.net/secrets/MAGIC-LINK-SESSION-SECRET,identityref:system" \
  "gmail-client-id=keyvaultref:https://${KV}.vault.azure.net/secrets/GMAIL-CLIENT-ID,identityref:system" \
  "gmail-client-secret=keyvaultref:https://${KV}.vault.azure.net/secrets/GMAIL-CLIENT-SECRET,identityref:system" \
  "gmail-oauth-state-secret=keyvaultref:https://${KV}.vault.azure.net/secrets/GMAIL-OAUTH-STATE-SECRET,identityref:system"

# Wire env vars (some are secretrefs, some literal)
az containerapp update -g "$RG" -n "$APP_API" --set-env-vars \
  ENVIRONMENT=production \
  LOG_LEVEL=INFO \
  DATABASE_URL=secretref:database-url \
  REDIS_URL=secretref:redis-url \
  ANTHROPIC_API_KEY=secretref:anthropic-api-key \
  TELEGRAM_MODE=webhook \
  TELEGRAM_BOT_TOKEN=secretref:telegram-bot-token \
  TELEGRAM_WEBHOOK_SECRET=secretref:telegram-webhook-secret \
  TELEGRAM_WEBHOOK_URL="https://${DOMAIN_API}/api/v1/telegram/webhook" \
  SECRET_KEY=secretref:secret-key \
  MAGIC_LINK_SESSION_SECRET=secretref:magic-link-session-secret \
  SPA_BASE_URL="https://${DOMAIN_SPA}" \
  SPA_CORS_ORIGINS="https://${DOMAIN_SPA}" \
  SESSION_COOKIE_DOMAIN="${DOMAIN_COOKIE}" \
  SESSION_COOKIE_SECURE=true \
  SECRET_STORE_BACKEND=azure_kv \
  AZURE_KEY_VAULT_URL="https://${KV}.vault.azure.net/" \
  GMAIL_CLIENT_ID=secretref:gmail-client-id \
  GMAIL_CLIENT_SECRET=secretref:gmail-client-secret \
  GMAIL_OAUTH_STATE_SECRET=secretref:gmail-oauth-state-secret \
  GMAIL_REDIRECT_URI="https://${DOMAIN_API}/api/v1/gmail/oauth/callback" \
  INSIGHTS_EXTRACTOR_ENABLED=true \
  INSIGHTS_DISPATCHER_ENABLED=false

# Custom domain (gets you the cookie domain you set above)
az containerapp hostname add -g "$RG" -n "$APP_API" \
  --hostname "$DOMAIN_API"
# Add the TXT + CNAME at your registrar, then:
az containerapp hostname bind -g "$RG" -n "$APP_API" \
  --hostname "$DOMAIN_API" --environment "$ENV_CAE"
```

**`INSIGHTS_DISPATCHER_ENABLED` stays `false`** until Daniel approves the
7-day shadow review (Phase 6c). Flip to `true` later via
`az containerapp update --set-env-vars`.

---

## 8. Migrations as a pre-deploy Container Apps Job

Migrations live in `migrations/versions/`. **Don't** run them at
container boot — production runs `alembic upgrade head` from a
one-shot Job so a botched migration can't take down the API.

```bash
# Create the job — same image as the api, different command
az containerapp job create \
  -g "$RG" -n "$JOB_MIGRATE" --environment "$ENV_CAE" \
  --image "$ACR_LOGIN/centro-api:phase6e-b13" \
  --trigger-type Manual \
  --replica-timeout 600 --replica-retry-limit 0 \
  --command "alembic upgrade head" \
  --cpu 0.25 --memory 0.5Gi \
  --registry-server "$ACR_LOGIN" --registry-identity system \
  --secrets \
    "database-url=keyvaultref:https://${KV}.vault.azure.net/secrets/DATABASE-URL,identityref:system" \
  --env-vars \
    DATABASE_URL=secretref:database-url \
    ENVIRONMENT=production

# Run it (blocks until done)
az containerapp job start -g "$RG" -n "$JOB_MIGRATE"
az containerapp job execution list -g "$RG" -n "$JOB_MIGRATE" -o table
```

Verify head:

```bash
az containerapp exec -g "$RG" -n "$APP_API" --command \
  "alembic current"
# expect: 0020 (head)
```

**Refresh the materialized views once after the first 0020 deploy**
(Phase 6e B2 added them; the nightly insights worker maintains them
afterwards):

```bash
az containerapp exec -g "$RG" -n "$APP_API" --command \
  "python -c 'import asyncio; from api.services.dashboard.materialized import refresh_dashboard_materialized_views; from api.database import AsyncSessionLocal; \
   async def r():\
     async with AsyncSessionLocal() as s:\
       await refresh_dashboard_materialized_views(s); await s.commit()\
   asyncio.run(r())'"
```

---

## 9. Gmail daily worker Container Apps Job

```bash
az containerapp job create \
  -g "$RG" -n "$JOB_GMAIL" --environment "$ENV_CAE" \
  --image "$ACR_LOGIN/centro-worker:phase6e-b13" \
  --trigger-type Schedule \
  --cron-expression "0 9 * * *" \
  --replica-timeout 1800 --replica-retry-limit 1 \
  --cpu 0.5 --memory 1.0Gi \
  --registry-server "$ACR_LOGIN" --registry-identity system

# Wire its own secrets + identity (mirror §6 for this job)
JOB_PRINCIPAL=$(az containerapp job identity assign \
  -g "$RG" -n "$JOB_GMAIL" --system-assigned \
  --query "principalId" -o tsv)
az role assignment create --role "Key Vault Secrets User" \
  --assignee "$JOB_PRINCIPAL" --scope "$KV_ID"

az containerapp job secret set -g "$RG" -n "$JOB_GMAIL" --secrets \
  "database-url=keyvaultref:https://${KV}.vault.azure.net/secrets/DATABASE-URL,identityref:system" \
  "redis-url=keyvaultref:https://${KV}.vault.azure.net/secrets/REDIS-URL,identityref:system" \
  "anthropic-api-key=keyvaultref:https://${KV}.vault.azure.net/secrets/ANTHROPIC-API-KEY,identityref:system" \
  "gmail-client-id=keyvaultref:https://${KV}.vault.azure.net/secrets/GMAIL-CLIENT-ID,identityref:system" \
  "gmail-client-secret=keyvaultref:https://${KV}.vault.azure.net/secrets/GMAIL-CLIENT-SECRET,identityref:system"

az containerapp job update -g "$RG" -n "$JOB_GMAIL" --set-env-vars \
  ENVIRONMENT=production \
  DATABASE_URL=secretref:database-url \
  REDIS_URL=secretref:redis-url \
  ANTHROPIC_API_KEY=secretref:anthropic-api-key \
  SECRET_STORE_BACKEND=azure_kv \
  AZURE_KEY_VAULT_URL="https://${KV}.vault.azure.net/" \
  GMAIL_CLIENT_ID=secretref:gmail-client-id \
  GMAIL_CLIENT_SECRET=secretref:gmail-client-secret
```

Smoke it once before waiting for the cron:

```bash
az containerapp job start -g "$RG" -n "$JOB_GMAIL"
az containerapp job execution list -g "$RG" -n "$JOB_GMAIL" -o table
```

---

## 10. Deploy the SPA

The repo already has `.github/workflows/azure-static-web-apps.yml`. The
shortest path is to wire it up:

```bash
# Save the SWA token as a GitHub Actions repo secret
gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN --body "$SWA_TOKEN"

# Trigger a deploy by pushing main (or via the workflow_dispatch UI)
git push origin main
```

The workflow runs `npm install` + `npm run build` in `web/` (which uses
`cross-env NODE_OPTIONS=--experimental-global-webcrypto` — works fine on
the GitHub-hosted Node 20+ runner) and uploads `web/dist/` to SWA.

**Custom domain** for the SPA so it shares the parent domain with the
API (required for the cookie):

```bash
az staticwebapp hostname set -g "$RG" -n "$SWA" \
  --hostname "$DOMAIN_SPA"
# Add CNAME at your registrar; SWA validates automatically.
```

After the SPA is at `https://centro.tudominio.cr` and the API is at
`https://api.centro.tudominio.cr`, the `fa_session` cookie with domain
`.centro.tudominio.cr` is sent on both, and `SPA_BASE_URL` /
`SPA_CORS_ORIGINS` line up.

---

## 11. First-deploy smoke checklist

Run from your laptop after §7 + §8 are green.

```bash
API="https://${DOMAIN_API}"
SPA="https://${DOMAIN_SPA}"

# Health
curl -s "$API/health" | jq
# {"status":"ok","environment":"production"}

curl -s "$API/health/ready" | jq
# {"status":"ok","db":true,"redis":true}

# Migrations
az containerapp exec -g "$RG" -n "$APP_API" --command "alembic current"
# expect: 0020 (head)

# Telegram webhook registration — happens automatically on lifespan
# startup when TELEGRAM_MODE=webhook + TELEGRAM_WEBHOOK_URL are set
# (bot/app.py calls set_webhook()). Verify by asking Telegram directly:
TG_TOKEN=$(az keyvault secret show \
  --vault-name "$KV" --name TELEGRAM-BOT-TOKEN --query value -o tsv)
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo" | jq
# expect: .result.url ends with /api/v1/telegram/webhook
#         .result.last_error_date is null or stale

# SPA shell
curl -sI "$SPA/" | head -5
# expect: 200 + service worker headers
curl -s "$SPA/manifest.webmanifest" | jq .name
# expect: "Centro Financiero"

# PWA precheck
curl -sI "$SPA/sw.js" | head -3
# expect: 200, Content-Type: application/javascript
```

If anything fails, jump to §15.

---

## 12. Register your user and pair Telegram

You're the only user yet. Register and copy the shortcut token (returned
**once**):

```bash
curl -s -X POST "$API/api/v1/users/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"dalfaroviquez@gmail.com","full_name":"Daniel Alfaro"}' | jq
# Save the `shortcut_token` somewhere safe — you can rotate later.
```

Pair Telegram:

1. Open Telegram, search for your bot, hit `/start`.
2. Bot replies with a pairing code request, or pair via:

   ```bash
   curl -s -X POST "$API/api/v1/telegram/pairing/create" \
     -H "X-Shortcut-Token: <your-shortcut-token>" | jq
   # → {"code":"123456","expires_at":"…"}
   ```

3. In Telegram, send the 6-digit code. Bot confirms.
4. Hit `/setup` in the bot — it should reply with an "Abrir setup web"
   button containing a magic-link URL pointing at `$SPA`.

---

## 13. Test from your phone (PWA install)

### iPhone (Safari)

1. Open the magic-link URL from `/setup` in Safari.
2. The SPA exchanges the token, sets the `fa_session` cookie, lands you
   on the dashboard `/`.
3. Tap the **Share** icon → **Añadir a inicio** → confirm.
4. App opens standalone (no Safari chrome) from your home screen. Title
   shows "Centro" because of `apple-mobile-web-app-title` in
   `web/index.html`.

iOS Safari does not fire `beforeinstallprompt`, so the in-app
`InstallBanner` stays hidden — that's expected.

### Android (Chrome)

1. Open the magic-link URL in Chrome.
2. After 3 visits, the in-app install banner shows ("Instalá Centro
   Financiero como app"). Tap **Instalar**.
3. The PWA installs to the app drawer, opens standalone.

### Verifying the deep-link path (Phase 6e B12)

Inside Telegram:

1. Send a transaction like "gasté 5000 en el super", confirm with "sí".
2. Bot replies with a "Ver en Centro Financiero" button → deep link to
   `/transactions?highlight=<id>` via `purpose=edit_session` magic link.
3. Tap → SPA opens (or focuses) on the transaction row.

Also try `/memoria` → "Editar en SPA" button.

---

## 14. Daily verification

After every block deploy, walk through this in ~3 minutes:

```bash
# 1. Migrations
az containerapp exec -g "$RG" -n "$APP_API" --command "alembic current"

# 2. Health + readiness
curl -s "$API/health/ready" | jq

# 3. The bot sees the webhook
curl -s "https://api.telegram.org/bot$(az keyvault secret show \
  --vault-name "$KV" --name TELEGRAM-BOT-TOKEN --query value -o tsv)/getWebhookInfo" | jq

# 4. SPA loads + manifest valid
curl -sI "$SPA/" | head -1
curl -s  "$SPA/manifest.webmanifest" | jq .icons

# 5. Gmail daily ran (Phase 6b)
az containerapp job execution list -g "$RG" -n "$JOB_GMAIL" \
  --query "[0].{status:properties.status,ended:properties.endTime}" -o tsv

# 6. Tail logs for the last 5 minutes
az containerapp logs show -g "$RG" -n "$APP_API" --follow false --tail 200
```

In Telegram, do one round-trip per block area: log a transaction
(write dispatcher), ask "cuánto gasté esta semana" (query dispatcher),
`/memoria` (Phase 6c), `/setup` (Phase 6d magic link).

In the SPA, do one round-trip per route: Dashboard `/`,
`/accounts/:id`, `/transactions`, `/bills`, `/debts/:id`, `/incomes`,
`/goals/:id`, `/memoria`, `/categories`. Each should render without
console errors.

---

## 15. Optional: GitHub Actions CI/CD blueprint

Repo already has `.github/workflows/azure-static-web-apps.yml` for the
SPA. The backend has none. Drop this in
`.github/workflows/azure-backend-deploy.yml` when you're ready:

```yaml
name: Azure backend deploy

on:
  push:
    branches: [main]
    paths:
      - "api/**"
      - "app/**"
      - "bot/**"
      - "workers/**"
      - "migrations/**"
      - "pyproject.toml"
      - "uv.lock"
      - "Dockerfile.prod"
      - "Dockerfile.worker"
      - ".github/workflows/azure-backend-deploy.yml"
  workflow_dispatch:

env:
  AZ_SUB: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
  RG:     ledger-cr-prod
  ACR:    ledgercrcrprod
  TAG:    ${{ github.sha }}

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      - name: ACR login
        run: az acr login -n "$ACR"
      - name: Build + push API
        run: |
          docker buildx build --platform linux/amd64 \
            -f Dockerfile.prod \
            -t "$ACR.azurecr.io/centro-api:${TAG}" --push .
      - name: Build + push worker
        run: |
          docker buildx build --platform linux/amd64 \
            -f Dockerfile.worker \
            -t "$ACR.azurecr.io/centro-worker:${TAG}" --push .

  migrate:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      - name: Update migrate job image + run
        run: |
          az containerapp job update -g "$RG" -n ledger-cr-migrate \
            --image "$ACR.azurecr.io/centro-api:${TAG}"
          az containerapp job start -g "$RG" -n ledger-cr-migrate
          # Block until success; fail the workflow on non-zero exit.

  deploy:
    runs-on: ubuntu-latest
    needs: migrate
    steps:
      - uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      - name: Roll the api/bot Container App
        run: |
          az containerapp update -g "$RG" -n ledger-cr-api \
            --image "$ACR.azurecr.io/centro-api:${TAG}"
      - name: Update the Gmail daily Job image
        run: |
          az containerapp job update -g "$RG" -n ledger-cr-gmail-daily \
            --image "$ACR.azurecr.io/centro-worker:${TAG}"
      - name: Verify Telegram webhook re-registered on lifespan
        # bot/app.py calls set_webhook() during FastAPI lifespan startup
        # when TELEGRAM_MODE=webhook. Nothing to POST — just verify
        # Telegram sees the new revision's URL.
        run: |
          FQDN=$(az containerapp show -g "$RG" -n ledger-cr-api \
            --query "properties.configuration.ingress.fqdn" -o tsv)
          TG_TOKEN=$(az keyvault secret show \
            --vault-name "$KV" --name TELEGRAM-BOT-TOKEN \
            --query value -o tsv)
          for _ in 1 2 3 4 5; do
            URL=$(curl -fsS "https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo" \
              | jq -r .result.url)
            if [[ "$URL" == *"${FQDN}"* ]]; then exit 0; fi
            sleep 6
          done
          echo "Telegram webhook URL did not converge to ${FQDN}" >&2; exit 1
```

Secrets you'll need on the repo:

| GH secret | Value |
|---|---|
| `AZURE_CREDENTIALS` | `az ad sp create-for-rbac --json-auth` output |
| `AZURE_SUBSCRIPTION_ID` | Your sub id |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | Already set in §4 |
| `SHORTCUT_TOKEN` | Your user's shortcut token (rotate first) |

Don't add this workflow until you've done one manual deploy following
§1–§14. CI/CD on top of an unverified deploy hides too much.

---

## 16. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health/ready` returns `degraded` with `db_error` | Postgres firewall / `?sslmode=require` missing | Verify the DATABASE-URL secret has `?sslmode=require` |
| `/health/ready` returns `degraded` with `redis_error` | Redis using `redis://` instead of `rediss://` | Azure Redis requires TLS on port 6380; use `rediss://` |
| Magic link opens SPA but exchange 401s with "Link inválido" | `MAGIC_LINK_SESSION_SECRET` differs across replicas | Check Key Vault value, redeploy |
| SPA loads but every API call 401s | Cookie domain mismatch between API and SPA | API and SPA must share parent domain; check `SESSION_COOKIE_DOMAIN` |
| iOS install doesn't show "Añadir a inicio" | Missing `apple-touch-icon` / `viewport-fit=cover` | Both are in `web/index.html`; rebuild SPA |
| Telegram inline-button URLs reject `localhost` | You tried to test from local dev | Use HTTPS tunnel (`cloudflared` / `ngrok`) for `SPA_BASE_URL` in dev |
| `alembic upgrade head` errors on `pgcrypto` | Extension not enabled | Re-run the `CREATE EXTENSION` from §4 |
| Gmail OAuth callback redirects to wrong host | `GMAIL_REDIRECT_URI` doesn't match the Google Console URI | Update either side; Google Console must list every prod + dev callback |
| `vite build` fails locally with `crypto is not defined` | Node 18 without the env flag | `npm run build` already wraps with `cross-env NODE_OPTIONS=--experimental-global-webcrypto`; use it |
| Workbox SW serves stale API responses | `NetworkFirst` failed to revalidate within 4s | Tap the in-app refresh / hard-reload; check `centro-api-cache` quota |
| Container App constantly restarting | Lifespan crash on missing env var | `az containerapp logs show … --tail 100` to find the missing key |
| Materialized views show no data | Never refreshed after migration `0017` | Run the refresh command in §8 once; the nightly insights worker takes over after |

---

## 17. Tear-down (when you're between blocks for a week)

```bash
# Scale the API down — keeps the URL/cert but stops the meter
az containerapp update -g "$RG" -n "$APP_API" \
  --min-replicas 0 --max-replicas 0

# Pause the Gmail daily job
az containerapp job update -g "$RG" -n "$JOB_GMAIL" \
  --cron-expression "0 0 1 1 0"   # never matches; effectively off

# Postgres B1ms can be stopped (not deleted)
az postgres flexible-server stop -g "$RG" -n "$PG"

# Redis Basic has no stop — delete or keep ($16/mo)
```

To resume: invert each command, then run `/setup` in Telegram to get a
fresh magic link.

---

## 18. Block-by-block deploy notes

Each future block tells you what to redeploy. The repeating recipe:

1. Build + push `centro-api:<phase>-<block>` (and `centro-worker` if
   workers changed — see `Dockerfile.worker` or the `workers/`
   directory).
2. Run `JOB_MIGRATE` if `migrations/versions/` gained a file.
3. `az containerapp update --image` on `APP_API`.
4. SPA changes auto-deploy via the existing GitHub Action.
5. Walk §14.

Blocks that ship without migrations: pure-SPA blocks (B3, B4 partial,
B6, B9, B10 SPA, B11, B13). Blocks with migrations: B2 (`0017`), B5
(`0018`), B7 (`0019`), B8 (`0020`).

---

## 19. Things this cookbook intentionally leaves out

- **Multi-tenant hardening** — Phase 9 territory (Supabase RLS,
  billing, observability stack). You're a tenant of one for now.
- **Gmail OAuth verification** — currently in GCP Testing mode (100
  testers). Required before P8 beta.
- **PWA real-device install + Lighthouse ≥ 80** — that's the pending
  B13 approval gate. Do it once the SPA is live at `$SPA`.
- **High-DTI nudge wiring** — Phase 6e B12 left the infra ready
  (`NudgeButton.url`) but the evaluator type doesn't exist yet. Add it
  with a new entry in `api/services/nudges/evaluators/` when you want
  that nudge to fire.
- **Backups** — Postgres Flexible Server takes daily automated backups
  with 7-day retention by default. Restore drill is your call.

After this cookbook lands in real Azure state, **update
`~/Finance_project/30_Projects/Finance-Agent/09_Operations/Deployment-State.md`**
with: the live `TELEGRAM_MODE`, the resource names you actually picked,
any deviations from the recipe, and the date of the cutover. That vault
note is the canonical "what's running right now" source — this cookbook
is the recipe, not the state.

---

## Quick reference card

| What | Where |
|---|---|
| API URL | `https://api.centro.<your-domain>` |
| SPA URL | `https://centro.<your-domain>` |
| Telegram webhook | `https://api.centro.<your-domain>/api/v1/telegram/webhook` |
| Gmail OAuth callback | `https://api.centro.<your-domain>/api/v1/gmail/oauth/callback` |
| Cookie domain | `.centro.<your-domain>` |
| Health checks | `/health`, `/health/ready` |
| Alembic head (right now) | `0020` |
| Bot image tag (right now) | `centro-api:phase6e-b13` |
| Worker image tag (right now) | `centro-worker:phase6e-b13` |
