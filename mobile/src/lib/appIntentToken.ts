import * as SecureStore from "expo-secure-store";

/**
 * Shares the session bearer token with the native Apple Pay App Intent
 * (ApplePayCaptureIntent.swift), which captures contactless purchases in the
 * background and POSTs them to the backend.
 *
 * The Intent reads the keychain by SERVICE alone, so we just store the token
 * under this dedicated service. `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` so the
 * headless Intent can read it when the app isn't foregrounded (the device is
 * unlocked at tap time), without syncing the credential off-device.
 *
 * `requireAuthentication: false` is REQUIRED: (a) the headless Intent must read
 * the token without a biometric prompt, and (b) expo-secure-store appends
 * ":no-auth" to the keychainService when requireAuthentication is false — the
 * Swift Intent queries `ledgercr.appintent:no-auth` (see keychainServiceCandidates
 * in ApplePayCaptureIntent.swift). Keep these two in sync.
 */
const APPINTENT_SERVICE = "ledgercr.appintent";
const APPINTENT_KEY = "session_token";

const WRITE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainService: APPINTENT_SERVICE,
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  requireAuthentication: false,
};

const SERVICE_OPTION: SecureStore.SecureStoreOptions = {
  keychainService: APPINTENT_SERVICE,
  requireAuthentication: false,
};

export async function writeAppIntentToken(token: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(APPINTENT_KEY, token, WRITE_OPTIONS);
  } catch {
    // Best-effort: a keychain hiccup must never break login.
  }
}

export async function clearAppIntentToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(APPINTENT_KEY, SERVICE_OPTION);
  } catch {
    // ignore — nothing to clear / keychain unavailable.
  }
}
