import * as SecureStore from "expo-secure-store";

import { clearAppIntentToken, writeAppIntentToken } from "./appIntentToken";

const TOKEN_KEY = "ledger_cr_session_token";
const EXPIRES_KEY = "ledger_cr_session_expires_at";

type Listener = (token: string | null) => void;
const listeners = new Set<Listener>();

let cachedToken: string | null = null;
let cachedExpiresAt: number | null = null;
let hydrated = false;

export async function hydrateSession(): Promise<void> {
  if (hydrated) return;
  const [token, expires] = await Promise.all([
    SecureStore.getItemAsync(TOKEN_KEY),
    SecureStore.getItemAsync(EXPIRES_KEY),
  ]);
  cachedToken = token;
  cachedExpiresAt = expires ? Number.parseInt(expires, 10) : null;
  hydrated = true;
  notify();
  // Self-heal the Apple Pay App Intent keychain: writeAppIntentToken otherwise
  // only runs on a fresh setSession(), so a session that's merely restored at
  // boot (or was established before the App Intent shipped) never seeds the slot
  // the Swift Intent reads — the "Abrí Ledger e ingresá con /login" error. Mirror
  // a still-valid token here too. Best-effort and self-contained (never throws).
  const valid = getSessionToken();
  if (valid) {
    void writeAppIntentToken(valid);
  }
}

export function getSessionToken(): string | null {
  if (cachedToken && cachedExpiresAt && cachedExpiresAt <= Date.now() / 1000) {
    return null;
  }
  return cachedToken;
}

export async function setSession(token: string, expiresAtUnix: number): Promise<void> {
  cachedToken = token;
  cachedExpiresAt = expiresAtUnix;
  await Promise.all([
    SecureStore.setItemAsync(TOKEN_KEY, token),
    SecureStore.setItemAsync(EXPIRES_KEY, String(expiresAtUnix)),
    // Share the token with the native Apple Pay App Intent (keychain).
    writeAppIntentToken(token),
  ]);
  notify();
}

export async function clearSession(): Promise<void> {
  cachedToken = null;
  cachedExpiresAt = null;
  await Promise.all([
    SecureStore.deleteItemAsync(TOKEN_KEY),
    SecureStore.deleteItemAsync(EXPIRES_KEY),
    // Revoke the Intent's copy too — on logout/401 it must stop capturing.
    clearAppIntentToken(),
  ]);
  notify();
}

export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify() {
  for (const listener of listeners) {
    listener(cachedToken);
  }
}
