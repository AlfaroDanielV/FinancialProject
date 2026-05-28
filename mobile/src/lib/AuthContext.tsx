import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  clearSession,
  getSessionToken,
  hydrateSession,
  setSession,
  subscribeSession,
} from "./auth";

type AuthStatus = "loading" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  token: string | null;
  signIn: (token: string, expiresAt: number) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await hydrateSession();
      if (cancelled) return;
      const current = getSessionToken();
      setToken(current);
      setStatus(current ? "authenticated" : "anonymous");
    })();
    const unsubscribe = subscribeSession((next) => {
      setToken(next);
      setStatus(next ? "authenticated" : "anonymous");
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (newToken: string, expiresAt: number) => {
    await setSession(newToken, expiresAt);
  }, []);

  const signOut = useCallback(async () => {
    await clearSession();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, token, signIn, signOut }),
    [status, token, signIn, signOut],
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
