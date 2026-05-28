const fallbackBaseUrl = "http://localhost:8000";

function readBaseUrl(): string {
  const value = process.env.EXPO_PUBLIC_API_BASE_URL;
  if (value && value.length > 0) {
    return value.replace(/\/$/, "");
  }
  return fallbackBaseUrl;
}

export const env = {
  apiBaseUrl: readBaseUrl(),
  apiPrefix: "/api/v1",
} as const;

export type Env = typeof env;
