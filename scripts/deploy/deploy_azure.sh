#!/usr/bin/env bash
#
# deploy_azure.sh — Build, migrate, and redeploy the Ledger CR backend to Azure
# Container Apps (Part A of the P8 Beta Launch Runbook).
#
# Source of truth:
#   ~/Finance_project/30_Projects/Finance-Agent/09_Operations/
#       Beta-Launch-Runbook - Azure + TestFlight.md  (sections A1–A9)
#
# What it does (in the load-bearing order the runbook insists on):
#   1. Build + push centro-api and centro-worker images (amd64) to ACR        [A1]
#   2. Point the migrate job at the new image and run it; verify alembic head [A2]
#      *** migrations run BEFORE the API is redeployed — a new evaluator/
#          column against an un-migrated DB poisons per-user transactions ***
#   3. Redeploy the api/bot Container App with a NEW revision (--revision-suffix
#      is MANDATORY; env-only updates do NOT restart the container)           [A4]
#   4. Update the Gmail daily worker job image                                [A7]
#   5. Smoke /health, /health/ready, and the Telegram webhook                 [A5/A9]
#
# Usage:
#   AZ_SUB=<subscription-id> ./scripts/deploy/deploy_azure.sh [--skip-build]
#                                                             [--skip-migrate]
#                                                             [--skip-worker]
#                                                             [--tag ]
#                                                             [--yes]
#
# Prereqs: az CLI >= 2.60 (logged in), docker w/ buildx, jq, run from repo root.
set -euo pipefail

# ── Resources (the REAL prod resources from [[Deployment-State]]) ────────────


# ── Flags ────────────────────────────────────────────────────────────────────
SKIP_BUILD=0; SKIP_MIGRATE=0; SKIP_WORKER=0; ASSUME_YES=0
TAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)   SKIP_BUILD=1; shift ;;
    --skip-migrate) SKIP_MIGRATE=1; shift ;;
    --skip-worker)  SKIP_WORKER=1; shift ;;
    --tag)          TAG="$2"; shift 2 ;;
    --yes|-y)       ASSUME_YES=1; shift ;;
    -h|--help)      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]
}

# ── Sanity ───────────────────────────────────────────────────────────────────
command -v az  >/dev/null || die "az CLI not found"
command -v jq  >/dev/null || die "jq not found"
[[ -f Dockerfile.prod && -f Dockerfile.worker ]] \
  || die "Run from the repo root (Dockerfile.prod / Dockerfile.worker missing)"

if [[ -n "${AZ_SUB:-}" ]]; then
  az account set --subscription "$AZ_SUB"
fi
SUB_NAME=$(az account show --query name -o tsv 2>/dev/null) \
  || die "az not logged in — run 'az login'"

# Tag: stable, traceable to the commit (matches runbook A1 'stmtfix-<sha>' style)
if [[ -z "$TAG" ]]; then
  SHA=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
  TAG="p8-$(date +%Y%m%d)-${SHA}"
fi
export TAG
API_IMAGE="$ACR_LOGIN/centro-api:$TAG"
WORKER_IMAGE="$ACR_LOGIN/centro-worker:$TAG"

cat <<EOF

  Subscription : $SUB_NAME
  Resource grp : $RG   ($REGION)
  ACR          : $ACR_LOGIN
  Container App: $CA_NAME
  Migrate job  : $JOB_MIGRATE
  Gmail worker : $JOB_GMAIL
  API FQDN     : https://$DOMAIN_API
  Image tag    : $TAG
  api image    : $API_IMAGE
  worker image : $WORKER_IMAGE

EOF
confirm "Proceed with this Azure deploy?" || die "Aborted."

# ── A1. Build & push images (amd64 — Container Apps are amd64) ───────────────
if [[ $SKIP_BUILD -eq 0 ]]; then
  log "A1 · az acr login"
  az acr login -n "$ACR"

  log "A1 · build+push centro-api ($TAG)"
  docker buildx build --platform linux/amd64 -f Dockerfile.prod \
    -t "$API_IMAGE" --push .

  # NOTE: Dockerfile.worker MUST build with --extra azure (commit 5d7e786) so the
  # daily Gmail worker can read gmail-refresh-{user_id} from Key Vault under
  # SECRET_STORE_BACKEND=azure_kv. That extra lives inside the Dockerfile.
  log "A1 · build+push centro-worker ($TAG)"
  docker buildx build --platform linux/amd64 -f Dockerfile.worker \
    -t "$WORKER_IMAGE" --push .
  ok "Images pushed."
else
  log "A1 · skipped (--skip-build); assuming $TAG already in ACR"
fi

# ── A2. Migrate FIRST (one-shot job; never migrate at container boot) ────────
if [[ $SKIP_MIGRATE -eq 0 ]]; then
  log "A2 · point migrate job at $TAG and run it"
  az containerapp job update -g "$RG" -n "$JOB_MIGRATE" --image "$API_IMAGE" >/dev/null
  az containerapp job start  -g "$RG" -n "$JOB_MIGRATE" >/dev/null

  log "A2 · waiting for migrate execution to finish…"
  # Poll the latest execution until it leaves Running.
  for _ in $(seq 1 40); do
    STATUS=$(az containerapp job execution list -g "$RG" -n "$JOB_MIGRATE" \
      --query "sort_by([].{s:properties.status,t:properties.startTime}, &t)[-1].s" -o tsv 2>/dev/null || true)
    [[ "$STATUS" == "Succeeded" ]] && { ok "Migrate job Succeeded."; break; }
    [[ "$STATUS" == "Failed" ]]    && die "Migrate job FAILED — inspect Log Analytics before redeploying the API."
    printf '   status=%s …\n' "${STATUS:-pending}"; sleep 15
  done
  [[ "${STATUS:-}" == "Succeeded" ]] || die "Migrate job did not finish in time; verify manually before continuing."

  # Verify the alembic head chain via Log Analytics (NOT 'containerapp exec' — needs a TTY, L2)
  log "A2 · verifying alembic head in Log Analytics (expect '… -> 0041')"
  LA_WS=$(az containerapp env show -g "$RG" -n "$ENV_CAE" \
    --query "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId" -o tsv 2>/dev/null || true)
  if [[ -n "$LA_WS" ]]; then
    az monitor log-analytics query -w "$LA_WS" \
      --analytics-query "ContainerAppConsoleLogs_CL | where ContainerGroupName_s contains 'migrate' | order by TimeGenerated desc | take 15 | project TimeGenerated, Log_s" \
      -o table 2>/dev/null || log "  (Log Analytics query failed; check the portal — ingestion can lag ~1–2 min)"
    echo "  ↑ confirm the 'Running upgrade … -> 0041' chain above before trusting the redeploy."
  else
    log "  (could not resolve Log Analytics workspace; verify head manually)"
  fi
else
  log "A2 · skipped (--skip-migrate) — only safe if no new migrations landed"
fi

# ── A4. Redeploy api/bot with a NEW revision (suffix is MANDATORY) ───────────
log "A4 · redeploy $CA_NAME on a new revision"
REV_SUFFIX="p8$(date +%H%M%S)"
# LLM_STATEMENT_MODEL pins bank-statement reconciliation to Haiku (faster + more
# accurate than Opus on real CR statements, 2026-06-28 loan-guardrails work). Set
# explicitly so prod is deterministic even if a prior manual "instant env flip"
# left an opposing LLM_STATEMENT_MODEL override on the app. The per-transaction
# extractor / query models stay unpinned on their code defaults, as before.
az containerapp update -g "$RG" -n "$CA_NAME" \
  --image "$API_IMAGE" \
  --revision-suffix "$REV_SUFFIX" \
  --set-env-vars \
    ENVIRONMENT=production \
    LOG_LEVEL=INFO \
    DATABASE_URL=secretref:database-url \
    REDIS_URL=secretref:redis-url \
    ANTHROPIC_API_KEY=secretref:anthropic-api-key \
    LLM_STATEMENT_MODEL=claude-haiku-4-5 \
    TELEGRAM_MODE=webhook \
    TELEGRAM_BOT_TOKEN=secretref:telegram-bot-token \
    TELEGRAM_WEBHOOK_SECRET=secretref:telegram-webhook-secret \
    TELEGRAM_WEBHOOK_URL="https://${DOMAIN_API}/api/v1/telegram/webhook" \
    SECRET_KEY=secretref:secret-key \
    MAGIC_LINK_SESSION_SECRET=secretref:magic-link-session-secret \
    SESSION_TTL_S=2592000 \
    MAGIC_LINK_TTL_S=1800 \
    NATIVE_APP_SCHEME=ledgercr \
    SECRET_STORE_BACKEND=azure_kv \
    AZURE_KEY_VAULT_URL="https://${KV}.vault.azure.net/" \
    GMAIL_CLIENT_ID=secretref:gmail-client-id \
    GMAIL_CLIENT_SECRET=secretref:gmail-client-secret \
    GMAIL_OAUTH_STATE_SECRET=secretref:gmail-oauth-state-secret \
    GMAIL_REDIRECT_URI="https://${DOMAIN_API}/api/v1/gmail/oauth/callback" \
    INSIGHTS_EXTRACTOR_ENABLED=true \
    INSIGHTS_DISPATCHER_ENABLED=true \
    NUDGE_SCHEDULER_ENABLED=true \
    NUDGE_SCHEDULER_INTERVAL_S=21600 \
    NUDGE_SCHEDULER_INITIAL_DELAY_S=60 \
  >/dev/null
ok "Container App updated → revision suffix '$REV_SUFFIX'."

# ── A7. Gmail daily worker job → new image ───────────────────────────────────
if [[ $SKIP_WORKER -eq 0 ]]; then
  log "A7 · update Gmail daily worker job image"
  if az containerapp job show -g "$RG" -n "$JOB_GMAIL" >/dev/null 2>&1; then
    az containerapp job update -g "$RG" -n "$JOB_GMAIL" --image "$WORKER_IMAGE" >/dev/null
    ok "Gmail worker job points at $TAG (start it manually to smoke: az containerapp job start -g $RG -n $JOB_GMAIL)."
  else
    log "  Gmail worker job '$JOB_GMAIL' not found — confirm exact name in the portal."
  fi
fi

# ── A5/A9. Smoke tests ───────────────────────────────────────────────────────
log "A9 · health smoke (allow ~30s for the new revision to come up)"
sleep 20
for path in /health /health/ready; do
  printf '  GET %s → ' "$path"
  curl -s --max-time 15 "https://${DOMAIN_API}${path}" | jq -c . 2>/dev/null \
    || echo "(no/invalid JSON — check 'az containerapp logs show -n $CA_NAME --tail 200')"
done

log "A5 · Telegram webhook check"
BOT_TOKEN=$(az keyvault secret show --vault-name "$KV" -n TELEGRAM-BOT-TOKEN --query value -o tsv 2>/dev/null || true)
if [[ -n "$BOT_TOKEN" ]]; then
  curl -s --max-time 15 "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" \
    | jq '{url:.result.url, pending:.result.pending_update_count, err:.result.last_error_message}' 2>/dev/null \
    || echo "  (webhook query failed)"
  echo "  ↑ url must end in /api/v1/telegram/webhook with err:null (else re-register — runbook A5)."
else
  log "  (could not read TELEGRAM-BOT-TOKEN from KV; skip webhook check)"
fi

cat <<EOF

$(ok "Azure deploy complete.")
  Live revision suffix : $REV_SUFFIX
  Image tag            : $TAG

  Next (manual, per runbook):
    • Confirm alembic head = 0041 in the A2 Log Analytics output above.
    • If demo reviewer login is needed, set APPLE_REVIEW_DEMO_CODE / _EMAIL (B7).
    • Smoke /login → device-code exchange end to end.
    • Update [[Deployment-State]] with tag $TAG + head 0041 (D4).
EOF
