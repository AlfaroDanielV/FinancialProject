/**
 * withApplePayIntent — Expo config plugin (iOS only).
 *
 * Wires the Apple Pay zero-touch capture App Intent into the native build:
 *   1. Injects `LedgerApiBaseUrl` into Info.plist (from EXPO_PUBLIC_API_BASE_URL
 *      or the `apiBaseUrl` plugin prop) so the Swift Intent knows the backend.
 *   2. Copies ApplePayCaptureIntent.swift into the iOS project and adds it to
 *      the main app target's compile sources.
 *
 * The Intent compiles into the MAIN app target (no separate extension), so it
 * runs in the app's process and reads the app's OWN keychain — the session
 * token written by expo-secure-store (src/lib/appIntentToken.ts). An app can
 * always read its own default-access-group keychain items, so NO Keychain
 * Sharing capability / entitlement is needed → no new Apple capability, same
 * EAS/TestFlight deployment. (If a future block splits the Intent into a
 * separate App Intents extension, add a keychain-access-group entitlement then.)
 *
 * This runs at `expo prebuild` / EAS Build — never in Expo Go. There is no
 * native CI; verify on device (see docs/apple-pay-setup.md).
 */
const fs = require("fs");
const path = require("path");
const {
  withInfoPlist,
  withXcodeProject,
  withDangerousMod,
} = require("@expo/config-plugins");

const SWIFT_FILENAME = "ApplePayCaptureIntent.swift";

function resolveApiBaseUrl(props) {
  const raw =
    (props && props.apiBaseUrl) ||
    process.env.EXPO_PUBLIC_API_BASE_URL ||
    "http://localhost:8000";
  return raw.replace(/\/$/, "");
}

function withApiBaseUrl(config, apiBaseUrl) {
  return withInfoPlist(config, (cfg) => {
    cfg.modResults.LedgerApiBaseUrl = apiBaseUrl;
    return cfg;
  });
}

function withSwiftFileCopied(config) {
  return withDangerousMod(config, [
    "ios",
    (cfg) => {
      const src = path.join(
        cfg.modRequest.projectRoot,
        "plugins",
        "ios",
        SWIFT_FILENAME
      );
      const destDir = path.join(
        cfg.modRequest.platformProjectRoot,
        cfg.modRequest.projectName
      );
      fs.mkdirSync(destDir, { recursive: true });
      fs.copyFileSync(src, path.join(destDir, SWIFT_FILENAME));
      return cfg;
    },
  ]);
}

function withSwiftFileInTarget(config) {
  return withXcodeProject(config, (cfg) => {
    const proj = cfg.modResults;
    const projectName = cfg.modRequest.projectName;
    const relPath = `${projectName}/${SWIFT_FILENAME}`;
    if (proj.hasFile(relPath)) return cfg; // idempotent

    const group =
      proj.findPBXGroupKey({ name: projectName }) ||
      proj.getFirstProject().firstProject.mainGroup;
    proj.addSourceFile(
      relPath,
      { target: proj.getFirstTarget().uuid },
      group
    );
    return cfg;
  });
}

module.exports = function withApplePayIntent(config, props) {
  const apiBaseUrl = resolveApiBaseUrl(props);
  config = withApiBaseUrl(config, apiBaseUrl);
  config = withSwiftFileCopied(config);
  config = withSwiftFileInTarget(config);
  return config;
};
