import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, setUnauthorizedHandler } from "../api/client";
import { ExchangeResponse } from "../schemas/onboarding";

// Auth lifecycle states.
//   loading      — startup; either exchanging a magic-link token or
//                  validating an existing cookie via /onboarding/status.
//   authenticated — backend accepts our cookie; SPA can render.
//   unauthenticated — redirect to /expired (router-driven; the context
//                  state is just authoritative for guards).
type AuthState =
  | { kind: "loading" }
  | { kind: "authenticated"; user: ExchangeResponse }
  | { kind: "unauthenticated" };

const AuthContext = createContext<AuthState>({ kind: "loading" });

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" });
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  // 401 anywhere in the app = redirect to /expired.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState({ kind: "unauthenticated" });
      navigate("/expired", { replace: true });
    });
  }, [navigate]);

  // Boot: if there's a `?token` in the URL, run the exchange. Otherwise
  // probe /onboarding/status to verify the cookie is still valid.
  useEffect(() => {
    const token = searchParams.get("token");
    let cancelled = false;

    async function boot() {
      if (token) {
        try {
          const resp = await api.post("/auth/magic-link/exchange", { token });
          const user = ExchangeResponse.parse(resp.data);
          if (cancelled) return;
          // Strip ?token from the URL so a refresh doesn't re-attempt
          // the exchange (which would 401 since the link is single-use).
          const next = new URLSearchParams(searchParams);
          next.delete("token");
          setSearchParams(next, { replace: true });
          setState({ kind: "authenticated", user });
        } catch {
          if (cancelled) return;
          setState({ kind: "unauthenticated" });
          navigate("/expired", { replace: true });
        }
        return;
      }

      // No token in URL — try the cookie.
      try {
        await api.get("/onboarding/status");
        if (cancelled) return;
        // Cookie is valid; we don't have user info yet (no /me endpoint
        // wired to the cookie auth in B3). For B4 the landing page just
        // needs onboarding/status; user info is nice-to-have for B5+.
        setState({
          kind: "authenticated",
          user: { user_id: "", email: "", full_name: "" },
        });
      } catch {
        if (cancelled) return;
        setState({ kind: "unauthenticated" });
        // Don't auto-redirect on initial unauthenticated state — the
        // /expired route handles its own messaging when user lands there
        // directly. Routes that require auth can call useRequireAuth().
      }
    }

    boot();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}
