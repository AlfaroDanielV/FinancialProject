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
  // Re-lock only after a REAL background (home / app switch). The biometric
  // prompt / Control Center / banners only cause "inactive", so this flag stays
  // false through the prompt — that's what prevents the unlock→relock loop.
  const wasBackgroundedRef = useRef(false);
  const lastUnlockAtRef = useRef(0); // skip re-lock briefly after an unlock

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

  // Re-lock when returning from a GENUINE background with an unlocked session.
  // iOS reports the biometric prompt / Control Center / notification banners as
  // "inactive" (ignored here); only a home-press / app-switch reaches
  // "background". Gating on that — never on "inactive" — is what stops the
  // Face ID unlock→relock loop (the prompt's own active→inactive→active blip
  // never sets wasBackgrounded).
  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "background") {
        wasBackgroundedRef.current = true;
        return;
      }
      if (next !== "active") return; // ignore "inactive" (the prompt blip)
      const wasBackgrounded = wasBackgroundedRef.current;
      wasBackgroundedRef.current = false;
      if (
        wasBackgrounded &&
        statusRef.current === "authenticated" &&
        getSessionToken() &&
        Date.now() - lastUnlockAtRef.current > 1500
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
    if (ok) {
      // Stamp the unlock so a background that happens to fire right around the
      // prompt teardown can't immediately re-lock (belt-and-suspenders on top
      // of the "ignore inactive" rule above).
      lastUnlockAtRef.current = Date.now();
      setStatus("authenticated");
    }
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
