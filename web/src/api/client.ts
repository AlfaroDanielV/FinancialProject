import axios, { AxiosError } from "axios";

// Phase 6d B4: HTTP client.
//
// `withCredentials: true` is required so the browser sends the
// `fa_session` httpOnly cookie set by `/api/v1/auth/magic-link/exchange`.
// The cookie is host-only in dev (Vite proxy keeps origin `localhost:5173`
// for both SPA and API requests).
//
// 401 interceptor: any authenticated call returning 401 means the cookie
// is gone or expired. Redirect to /expired, which prompts the user to
// /setup again in Telegram.
// VITE_API_URL is set at build time for production (absolute URL to the API
// Container App). In dev the Vite proxy forwards /api/* to localhost:8000 so
// the empty string fallback keeps the relative path working there.
const API_BASE = (import.meta.env.VITE_API_URL ?? "") + "/api/v1";

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401 && onUnauthorized) {
      onUnauthorized();
    }
    return Promise.reject(error);
  },
);
