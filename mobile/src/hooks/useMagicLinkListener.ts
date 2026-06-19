import * as Linking from "expo-linking";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../lib/AuthContext";
import { parseMagicLink } from "../lib/deepLink";
import { exchangeMagicLink, ExchangeError } from "../lib/exchange";

type ExchangeState =
  | { kind: "idle" }
  | { kind: "exchanging" }
  | { kind: "error"; message: string };

/**
 * Watches Expo's Linking API for `ledgercr://exchange?token=...` and
 * runs the magic-link exchange. The hook is mounted once at the root —
 * higher up than the auth gate — so cold-start URLs are caught before
 * the gate decides to render Login.
 *
 * Also exposes `processManualInput(value)` for the Login screen's
 * paste-link fallback, so deep-link and manual paths share the same
 * exchange + error UX.
 */
export function useMagicLinkListener() {
  const { signIn } = useAuth();
  const [exchangeState, setExchangeState] = useState<ExchangeState>({ kind: "idle" });

  const processToken = useCallback(
    async (rawToken: string) => {
      setExchangeState({ kind: "exchanging" });
      try {
        const body = await exchangeMagicLink(rawToken);
        await signIn(body.token, body.expires_at);
        setExchangeState({ kind: "idle" });
      } catch (err) {
        const message =
          err instanceof ExchangeError ? err.message : "No pude completar el ingreso.";
        setExchangeState({ kind: "error", message });
      }
    },
    [signIn],
  );

  const processManualInput = useCallback(
    async (value: string) => {
      const token = parseMagicLink(value);
      if (!token) {
        setExchangeState({
          kind: "error",
          message: "No reconocí ese link. Pegá el link completo de /setup.",
        });
        return;
      }
      await processToken(token);
    },
    [processToken],
  );

  useEffect(() => {
    let cancelled = false;
    Linking.getInitialURL().then((url) => {
      if (cancelled || !url) return;
      const token = parseMagicLink(url);
      if (token) {
        void processToken(token);
      }
    });
    const subscription = Linking.addEventListener("url", ({ url }) => {
      const token = parseMagicLink(url);
      if (token) {
        void processToken(token);
      }
    });
    return () => {
      cancelled = true;
      subscription.remove();
    };
  }, [processToken]);

  const clearError = useCallback(() => {
    setExchangeState({ kind: "idle" });
  }, []);

  return {
    isExchanging: exchangeState.kind === "exchanging",
    error: exchangeState.kind === "error" ? exchangeState.message : null,
    processManualInput,
    clearError,
  };
}
