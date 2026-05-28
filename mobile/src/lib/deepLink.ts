/**
 * Parse a magic-link URL into its raw `<selector>.<verifier>` token.
 *
 * Accepts both of these shapes:
 *   - `ledgercr://exchange?token=<selector.verifier>` (native deep link)
 *   - `https://<spa-host>/?token=<selector.verifier>&path=<safe-path>` (legacy SPA link)
 *   - `<selector>.<verifier>` raw token (manual paste fallback)
 *
 * Returns the raw token if found, otherwise null.
 */
export function parseMagicLink(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // Raw token (selector.verifier, no URL scheme) — fallback path.
  if (!trimmed.includes("://") && !trimmed.startsWith("?") && trimmed.includes(".")) {
    if (looksLikeOpaqueToken(trimmed)) {
      return trimmed;
    }
  }

  // URL form — pull `?token=...` out of the query string. URL constructor
  // in React Native handles ledgercr:// scheme too as long as the rest is
  // well-formed.
  try {
    const url = new URL(trimmed);
    const token = url.searchParams.get("token");
    if (token && looksLikeOpaqueToken(token)) {
      return token;
    }
  } catch {
    // Try fallback: bare query string like "?token=..."
    if (trimmed.startsWith("?")) {
      const params = new URLSearchParams(trimmed.substring(1));
      const token = params.get("token");
      if (token && looksLikeOpaqueToken(token)) {
        return token;
      }
    }
  }

  return null;
}

function looksLikeOpaqueToken(value: string): boolean {
  // Opaque token format: <selector>.<verifier>. Both halves are
  // secrets.token_urlsafe(N) on the backend — base64url chars only,
  // with the period as the separator. Reject obvious garbage early so
  // we don't fire the exchange endpoint on every random clipboard paste.
  if (value.length < 12 || value.length > 256) return false;
  const parts = value.split(".");
  if (parts.length !== 2) return false;
  const isB64Url = (s: string) => /^[A-Za-z0-9_-]+$/.test(s);
  return isB64Url(parts[0]) && parts[0].length >= 4 && isB64Url(parts[1]) && parts[1].length >= 8;
}
