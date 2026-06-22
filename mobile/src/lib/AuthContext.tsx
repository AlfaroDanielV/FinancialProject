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
import { AppState, type AppStateStatus } from "react-native";

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
  const authenticatingRef = useRef(false); // suppress re-lock during a prompt
  const statusRef = useRef<AuthStatus>("loading");
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);

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

  // Re-lock when returning to the foreground with an unlocked, valid session.
  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      const prev = appStateRef.current;
      appStateRef.current = next;
      const returnedToForeground =
        next === "active" && (prev === "background" || prev === "inactive");
      if (
        returnedToForeground &&
        !authenticatingRef.current &&
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
    authenticatingRef.current = true;
    try {
      const ok = await promptBiometric();
      if (ok) setStatus("authenticated");
      return ok;
    } finally {
      // Keep the re-lock suppressed briefly: dismissing the iOS prompt fires
      // inactive→active, which would otherwise immediately re-lock the app.
      setTimeout(() => {
        authenticatingRef.current = false;
      }, 1200);
    }
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
