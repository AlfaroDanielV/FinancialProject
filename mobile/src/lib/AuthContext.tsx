import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AppState } from "react-native";

import {
  clearSession,
  getSessionToken,
  hydrateSession,
  setSession,
  subscribeSession,
} from "./auth";
import { biometricGateAvailable, promptBiometric } from "./biometric";

// How long the app may sit in the background before it re-locks on return. A
// quick app-switch under this stays unlocked — no re-prompt and no lost UI
// state (forms, the screen you were on). A cold start always locks (the mount
// effect), independent of this.
const LOCK_AFTER_BACKGROUND_MS = 5 * 60 * 1000; // 5 minutes

// "locked" = a valid session exists but the app is gated behind Face ID /
// passcode (cold start with a persisted token, or returning from background).
type AuthStatus = "loading" | "anonymous" | "locked" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  token: string | null;
  signIn: (token: string, expiresAt: number) => Promise<void>;
  signOut: () => Promise<void>;
  /** Prompt Face ID / passcode. On success the app unlocks. Returns success. */
  unlock: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [token, setToken] = useState<string | null>(null);

  // Refs that the long-lived listeners read without re-subscribing.
  const initializedRef = useRef(false); // ignore hydrate's notify; mount owns it
  const statusRef = useRef<AuthStatus>("loading");
  // Timestamp (ms) of the last REAL background (home / app switch). The
  // biometric prompt / Control Center / banners only cause "inactive" (never
  // recorded here), so the prompt's own active→inactive→active blip can't
  // re-lock. 0 = not currently backgrounded.
  const backgroundedAtRef = useRef(0);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      await hydrateSession();
      if (cancelled) return;
      const current = getSessionToken();
      setToken(current);
      if (!current) {
        setStatus("anonymous");
      } else {
        // A persisted token on cold start starts LOCKED (the biometric gate
        // runs) — unless the device has no biometrics/passcode at all, in which
        // case we must not lock the user out.
        const gate = await biometricGateAvailable();
        if (!cancelled) setStatus(gate ? "locked" : "authenticated");
      }
      initializedRef.current = true;
    })();

    // Post-init session changes: a token appearing is a fresh sign-in (device
    // code or magic-link listener) → authenticated, no lock (identity just
    // proven); a token vanishing (401 / signOut) → anonymous. The hydrate
    // notify is ignored via initializedRef so a restored token still locks.
    const unsubscribe = subscribeSession((next) => {
      if (!initializedRef.current) return;
      setToken(next);
      setStatus(next ? "authenticated" : "anonymous");
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  // Re-lock on return from a background that lasted at least
  // LOCK_AFTER_BACKGROUND_MS. "inactive" (the biometric prompt / Control Center
  // / notification banners) is ignored, so the prompt can't trigger a re-lock —
  // only a real home-press / app-switch records a background timestamp. A quick
  // switch back under the threshold stays unlocked, so in-progress UI state is
  // never thrown away.
  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "background") {
        backgroundedAtRef.current = Date.now();
        return;
      }
      if (next !== "active") return; // ignore "inactive"
      const bgAt = backgroundedAtRef.current;
      backgroundedAtRef.current = 0;
      if (
        bgAt > 0 &&
        Date.now() - bgAt >= LOCK_AFTER_BACKGROUND_MS &&
        statusRef.current === "authenticated" &&
        getSessionToken()
      ) {
        void biometricGateAvailable().then((gate) => {
          if (gate) setStatus("locked");
        });
      }
    });
    return () => sub.remove();
  }, []);

  const signIn = useCallback(async (newToken: string, expiresAt: number) => {
    await setSession(newToken, expiresAt);
  }, []);

  const signOut = useCallback(async () => {
    await clearSession();
  }, []);

  const unlock = useCallback(async () => {
    const ok = await promptBiometric();
    if (ok) setStatus("authenticated");
    return ok;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, token, signIn, signOut, unlock }),
    [status, token, signIn, signOut, unlock],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() must be used inside <AuthProvider>");
  }
  return ctx;
}
