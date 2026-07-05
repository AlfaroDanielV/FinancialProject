#!/usr/bin/env bash
#
# deploy_testflight.sh — Build the production iOS binary with EAS and submit it
# to TestFlight (Part B of the P8 Beta Launch Runbook).
#
# Source of truth:
#   ~/Finance_project/30_Projects/Finance-Agent/09_Operations/
#       Beta-Launch-Runbook - Azure + TestFlight.md  (sections B3–B6, D3)
#
# What it does:
#   1. Pre-flight: verify eas-cli, login, the production profile, the baked
#      EXPO_PUBLIC_API_BASE_URL, the prod bundle id, and the ASC API key.   [B3]
#   2. eas build  -p ios --profile production   (cloud build, ~20–40 min)   [B5]
#   3. eas submit -p ios --profile production --latest  → TestFlight        [B6]
#
# Hard rules from the runbook this enforces / warns about:
#   • EXPO_PUBLIC_API_BASE_URL MUST be baked into the production profile, else
#     every tester's app silently calls localhost → dead beta (the #1 trap).
#   • Native changes (new dep, config plugin, Swift/AppIntent, Info.plist) need
#     a full build+submit — autoIncrement bumps the iOS build number.
#   • TS-only changes do NOT need this script — ship them with `eas update`
#     (OTA) on the existing build's channel. P10.S chat streaming
#     (mobile/src/api/chatStream.ts + Chat.tsx) is such a change: it uses
#     `expo/fetch` + `TextDecoder`, both already compiled into any SDK-54 build
#     (expo core), so no new native binary is required. It is also gated by the
#     backend CHAT_STREAMING_ENABLED flag, so an OTA'd client is a safe no-op
#     (falls back to /chat/message) until that flag flips.
#   • The backend at that API URL must already be deployed+migrated FIRST,
#     otherwise new endpoints (e.g. /transactions/apple-pay) 4xx silently.
#
# Usage:
#   ./scripts/deploy/deploy_testflight.sh                 # build then submit
#   ./scripts/deploy/deploy_testflight.sh --build-only    # just eas build
#   ./scripts/deploy/deploy_testflight.sh --submit-only   # submit the latest build
#   ./scripts/deploy/deploy_testflight.sh --bump-version 0.1.1   # set app.json version first
#   ./scripts/deploy/deploy_testflight.sh --yes
#
# Prereqs: Node 20 LTS, eas-cli (npm i -g eas-cli), an Expo account, the Apple
# Developer account + ASC API .p8, run from the repo root.
set -euo pipefail

MOBILE_DIR="${MOBILE_DIR:-mobile}"
PROFILE="${EAS_PROFILE:-production}"
EXPECTED_API_URL="${EXPECTED_API_URL:-https://api.keystonefinance-atemporal.com}"
EXPECTED_BUNDLE_ID="${EXPECTED_BUNDLE_ID:-com.danielalfaro.ledgercr}"

BUILD_ONLY=0; SUBMIT_ONLY=0; ASSUME_YES=0; BUMP_VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only)   BUILD_ONLY=1; shift ;;
    --submit-only)  SUBMIT_ONLY=1; shift ;;
    --bump-version) BUMP_VERSION="$2"; shift 2 ;;
    --yes|-y)       ASSUME_YES=1; shift ;;
    -h|--help)      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]
}

# ── Locate the mobile workspace ──────────────────────────────────────────────
[[ -d "$MOBILE_DIR" ]] || die "mobile dir '$MOBILE_DIR' not found — run from repo root"
cd "$MOBILE_DIR"
[[ -f app.json && -f eas.json ]] || die "app.json / eas.json missing in $MOBILE_DIR"

# ── Tooling ──────────────────────────────────────────────────────────────────
command -v node >/dev/null || die "node not found (need Node 20 LTS)"
if ! command -v eas >/dev/null; then
  die "eas-cli not found — install with: npm i -g eas-cli"
fi
log "eas-cli $(eas --version 2>/dev/null | head -1)"

# Confirm logged in (eas whoami exits non-zero / prints when not authed)
if ! eas whoami >/dev/null 2>&1; then
  warn "Not logged into Expo. Launching 'eas login'…"
  eas login
fi
ok "Expo user: $(eas whoami 2>/dev/null || echo '?')"

# ── Pre-flight config checks (jq if available, else grep fallbacks) ──────────
have_jq=0; command -v jq >/dev/null && have_jq=1

# 1) EAS project linked?
if [[ $have_jq -eq 1 ]]; then
  PROJECT_ID=$(jq -r '.expo.extra.eas.projectId // empty' app.json)
else
  PROJECT_ID=$(grep -oE '"projectId"[[:space:]]*:[[:space:]]*"[^"]+"' app.json | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
fi
[[ -n "${PROJECT_ID:-}" ]] || die "app.json has no extra.eas.projectId — run 'eas init' first (B3)"
ok "EAS projectId: $PROJECT_ID"

# 2) Prod bundle id (the .dev suffix must be gone — B2)
if [[ $have_jq -eq 1 ]]; then
  BUNDLE_ID=$(jq -r '.expo.ios.bundleIdentifier // empty' app.json)
else
  BUNDLE_ID=$(grep -oE '"bundleIdentifier"[[:space:]]*:[[:space:]]*"[^"]+"' app.json | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
fi
log "bundleIdentifier: ${BUNDLE_ID:-<unset>}"
if [[ "$BUNDLE_ID" != "$EXPECTED_BUNDLE_ID" ]]; then
  warn "bundleIdentifier '$BUNDLE_ID' != expected '$EXPECTED_BUNDLE_ID'."
  warn "TestFlight builds must use the prod id (no .dev). Continue only if you know why."
  confirm "Proceed anyway?" || die "Aborted on bundle-id mismatch."
fi

# 3) THE #1 TRAP: production profile must bake EXPO_PUBLIC_API_BASE_URL
if [[ $have_jq -eq 1 ]]; then
  BAKED_URL=$(jq -r ".build.${PROFILE}.env.EXPO_PUBLIC_API_BASE_URL // empty" eas.json)
  DISTRIBUTION=$(jq -r ".build.${PROFILE}.distribution // empty" eas.json)
else
  BAKED_URL=$(grep -oE '"EXPO_PUBLIC_API_BASE_URL"[[:space:]]*:[[:space:]]*"[^"]+"' eas.json | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
  DISTRIBUTION="store?"
fi
[[ -n "${BAKED_URL:-}" ]] \
  || die "eas.json build.${PROFILE}.env.EXPO_PUBLIC_API_BASE_URL is UNSET → the binary would call localhost. Set it (B3)."
log "baked EXPO_PUBLIC_API_BASE_URL: $BAKED_URL  (distribution: ${DISTRIBUTION:-?})"
if [[ "$BAKED_URL" != "$EXPECTED_API_URL" ]]; then
  warn "Baked API URL '$BAKED_URL' != expected '$EXPECTED_API_URL'."
  confirm "Proceed with this API URL?" || die "Aborted on API-URL mismatch."
fi

# 4) Submit creds present (only needed when we will submit)
if [[ $SUBMIT_ONLY -eq 1 || $BUILD_ONLY -eq 0 ]]; then
  if [[ $have_jq -eq 1 ]]; then
    P8_PATH=$(jq -r ".submit.${PROFILE}.ios.ascApiKeyPath // empty" eas.json)
  else
    P8_PATH=$(grep -oE '"ascApiKeyPath"[[:space:]]*:[[:space:]]*"[^"]+"' eas.json | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
  fi
  if [[ -n "${P8_PATH:-}" ]]; then
    # Path is relative to mobile/ in eas.json
    if [[ "$P8_PATH" = /* ]]; then RESOLVED="$P8_PATH"; else RESOLVED="$(pwd)/$P8_PATH"; fi
    [[ -f "$RESOLVED" ]] && ok "ASC API key present: $P8_PATH" \
      || warn "ascApiKeyPath '$P8_PATH' not found at $RESOLVED — eas submit will fail (B2/B3)."
  else
    warn "submit.${PROFILE}.ios.ascApiKeyPath unset — EAS will prompt for Apple credentials interactively."
  fi
fi

echo
log "Plan: profile=$PROFILE  build=$([[ $SUBMIT_ONLY -eq 1 ]] && echo no || echo yes)  submit=$([[ $BUILD_ONLY -eq 1 ]] && echo no || echo yes)"
confirm "Proceed?" || die "Aborted."

# ── Optional version bump (D3: bump app.json version; autoIncrement handles build #) ──
if [[ -n "$BUMP_VERSION" ]]; then
  log "Bumping app.json version → $BUMP_VERSION"
  if [[ $have_jq -eq 1 ]]; then
    tmp=$(mktemp)
    jq --arg v "$BUMP_VERSION" '.expo.version = $v' app.json > "$tmp" && mv "$tmp" app.json
    ok "app.json version set to $BUMP_VERSION (commit this — EAS archives from git)."
  else
    die "jq required for --bump-version; edit app.json 'version' manually."
  fi
fi

# ── B5. Build ────────────────────────────────────────────────────────────────
if [[ $SUBMIT_ONLY -eq 0 ]]; then
  log "B5 · eas build -p ios --profile $PROFILE  (cloud, ~20–40 min; autoIncrement bumps build #)"
  eas build --platform ios --profile "$PROFILE"
  ok "Build finished."
fi

# ── B6. Submit to TestFlight ─────────────────────────────────────────────────
if [[ $BUILD_ONLY -eq 0 ]]; then
  log "B6 · eas submit -p ios --profile $PROFILE --latest  → App Store Connect"
  eas submit --platform ios --profile "$PROFILE" --latest
  ok "Submitted. Binary 'processes' ~10–30 min before it's usable in TestFlight."
fi

cat <<EOF

$(ok "TestFlight deploy step complete.")

  Next (manual, App Store Connect → TestFlight — runbook B7):
    • External group "Beta CR" → add the build → Beta App Review (~24h, first build).
    • Test Information: reviewer Notes block + demo code BETACR in Sign-In fields.
      (Requires APPLE_REVIEW_DEMO_CODE/_EMAIL set on the backend — runbook B7.)
    • Invite the 15 testers by email.
    • Builds expire after 90 days — re-submit before then (D3).
    • Update [[Deployment-State]] with the new build number (D4).
EOF
