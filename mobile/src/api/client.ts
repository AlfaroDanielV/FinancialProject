import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from "axios";

import { env } from "../lib/env";
import { clearSession, getSessionToken } from "../lib/auth";

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

async function handleResponseError(error: AxiosError): Promise<never> {
  if (error.response?.status === 401) {
    await clearSession();
    unauthorizedHandler?.();
  }
  return Promise.reject(error);
}

function buildClient(): AxiosInstance {
  const instance = axios.create({
    baseURL: `${env.apiBaseUrl}${env.apiPrefix}`,
    timeout: 15000,
    headers: {
      Accept: "application/json",
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

export const api = buildClient();
