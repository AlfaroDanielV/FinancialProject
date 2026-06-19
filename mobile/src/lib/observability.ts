/**
 * Phase 6f B15 — observability scaffold.
 *
 * This is a deliberate scaffold, NOT a live Sentry integration. The operator
 * runs the app through Expo Go, which cannot load `@sentry/react-native`'s
 * native module — eagerly importing it would red-screen the working app. So
 * the real SDK wiring is deferred to the EAS dev build at P8 (decision §3.8,
 * open question §6.6 in docs/phase-6f-decisions.md).
 *
 * What this gives us now: a single integration point (`initObservability` +
 * `captureError`) that the ErrorBoundary and catch-blocks call today. When the
 * dev build lands, swap the console implementation for `Sentry.init(...)` /
 * `Sentry.captureException(...)` here and nothing else changes. The DSN is read
 * from `EXPO_PUBLIC_SENTRY_DSN`; when unset (the Expo Go case) everything is a
 * console no-op.
 */
import { env } from "./env";

let initialized = false;

export function initObservability(): void {
  if (initialized) {
    return;
  }
  initialized = true;
  if (!env.sentryDsn) {
    // Expo Go / no-DSN path: console only. No native module touched.
    return;
  }
  // TODO(P8 dev build): replace with
  //   import * as Sentry from "@sentry/react-native";
  //   Sentry.init({ dsn: env.sentryDsn, enableNative: true });
  // Until then we only acknowledge that a DSN was provided.
  // eslint-disable-next-line no-console
  console.info("[observability] DSN configured; live Sentry pending dev build.");
}

export function captureError(error: unknown, context?: Record<string, unknown>): void {
  // eslint-disable-next-line no-console
  console.error("[observability] captured error", error, context ?? {});
  // TODO(P8 dev build): Sentry.captureException(error, { extra: context });
}
