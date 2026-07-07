import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from "axios";
import Constants from "expo-constants";

import { env } from "../lib/env";
import { clearSession, getSessionToken } from "../lib/auth";

/**
 * B2 — client build diagnosability. Every API call carries the JS bundle's
 * build identifier (`X-App-Build: "18"`) so server logs can tell client builds
 * apart (OTA vs store build, stale TestFlight, etc.). Sourced from app.json's
 * ios.buildNumber via expo-constants (already a dependency — zero new deps);
 * "-" when unavailable.
 */
export const APP_BUILD: string = Constants.expoConfig?.ios?.buildNumber ?? "-";

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = handler;
}

function attachAuthHeader(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  const token = getSessionToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
}

/**
 * Clear the session + fire the unauthorized handler. Shared so the SSE
 * streaming client (which bypasses the axios interceptor via expo/fetch) can
 * reuse the exact same 401 behavior on a stream response.
 */
export async function notifyUnauthorized(): Promise<void> {
  await clearSession();
  unauthorizedHandler?.();
}

async function handleResponseError(error: AxiosError): Promise<never> {
  if (error.response?.status === 401) {
    await notifyUnauthorized();
  }
  return Promise.reject(error);
}

function buildClient(): AxiosInstance {
  const instance = axios.create({
    baseURL: `${env.apiBaseUrl}${env.apiPrefix}`,
    timeout: 15000,
    headers: {
      Accept: "application/json",
      "X-App-Build": APP_BUILD,
    },
  });
  instance.interceptors.request.use(attachAuthHeader);
  instance.interceptors.response.use((response) => response, handleResponseError);
  return instance;
}

// LLM document/vision uploads (statement / card / debt PDF parse, receipt vision)
// run a multi-page-PDF extraction that takes ~50-90s server-side — far past the
// 15s default. They pass this per-call so a slow-but-successful parse isn't
// abandoned client-side (which surfaced as a false "No pude leer el estado").
// Comfortably exceeds the server's 120s statement timeout + a 2-pass retry.
export const LLM_UPLOAD_TIMEOUT_MS = 300_000;

// Chat text turns that reach the query dispatcher (Sonnet, up to 4 tool
// iterations — 8 on an advisory turn) legitimately run 20-90s server-side.
// The 15s client default abandoned them mid-answer and surfaced as a false
// "No pude conectar con el servidor" while the server finished fine
// (TestFlight repro 2026-07-04). A truly unreachable server still fails fast
// at connect; this only extends how long we wait for a slow ANSWER.
// P10.S: this is now the FALLBACK (non-stream) timeout — the streaming path
// (chatStream.ts) uses a stall timer + a ~210s cap instead. Raised above the
// server's 170s whole-turn deadline so a slow fallback answer isn't cut.
export const CHAT_TIMEOUT_MS = 180_000;

export const api = buildClient();
