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

// Phase 6e B12: defensive client-side check on the `?path=` query param.
// The backend already validates `target_path` shape at mint time, but the
// SPA should also refuse anything that looks like a protocol-relative
// URL, an absolute URL, or a non-relative path — that defends against a
// future change in the magic-link URL builder.
function isSafeRelativePath(value: string | null): boolean {
  if (!value) return false;
  if (!value.startsWith("/")) return false;
  if (value.startsWith("//")) return false;
  if (value.includes("://")) return false;
  return true;
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
    const targetPath = searchParams.get("path");
    let cancelled = false;

    async function boot() {
      if (token) {
        try {
          const resp = await api.post("/auth/magic-link/exchange", { token });
          const user = ExchangeResponse.parse(resp.data);
          if (cancelled) return;
          // Phase 6e B12: honour `?path=` when present and valid. Server
          // already validates `target_path` shape; this is a defensive
          // client-side check so we never navigate to a foreign URL or
          // protocol-relative location.
          const safePath = isSafeRelativePath(targetPath) ? targetPath! : null;
          setState({ kind: "authenticated", user });
          if (safePath) {
            // Replace the URL atomically with the target; this drops
            // `?token` and `?path` in one go.
            navigate(safePath, { replace: true });
          } else {
            // Strip `?token` (and `?path` if present-but-invalid) so a
            // refresh doesn't re-attempt the now-consumed exchange.
            const next = new URLSearchParams(searchParams);
            next.delete("token");
            next.delete("path");
            setSearchParams(next, { replace: true });
          }
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
