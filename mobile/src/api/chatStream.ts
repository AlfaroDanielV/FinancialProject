import { fetch } from "expo/fetch";

import { getSessionToken } from "../lib/auth";
import { env } from "../lib/env";
import { APP_BUILD, notifyUnauthorized } from "./client";
import type { ChatMessageResponse } from "./chat";

/**
 * P10.S — token-streaming chat client (Server-Sent Events over `expo/fetch`).
 *
 * Streams `event: delta` text frames + a neutral `event: status` while the
 * server runs read tools, then exactly ONE terminal `event: final` carrying the
 * full ChatMessageResponse (buttons / open_screen / error_class). Uses
 * `expo/fetch` (bundled in Expo core — no new dep, Expo-Go-safe) because axios
 * buffers the whole body in React Native.
 *
 * Fallback contract (write-safety): a SILENT fallback to the blocking
 * `/chat/message` is triggered ONLY when the stream route is capability-missing
 * (HTTP 404 / 405 / 501 — an old server build or the flag off). Any other
 * failure (network drop, 5xx, a stream that ended without `final`) is
 * NON-retryable: the turn may already have committed a write server-side, so we
 * surface an error rather than blindly re-sending.
 */

const STALL_MS = 25_000; // abort if no bytes (incl. heartbeats) for this long
const HARD_CAP_MS = 210_000; // absolute ceiling, above the server's 170s deadline

/**
 * Structured cause of a ChatStreamError — drives honest client copy:
 * - "connect": pre-response fetch/connect failure (the POST may or may not
 *   have reached the server — no bytes ever came back).
 * - "http": the server answered with a non-2xx HTTP response (including the
 *   capability-missing 404/405/501 and the 401 path).
 * - "interrupted": the request WAS accepted (2xx) and the stream died mid-turn
 *   — stall timer, hard-cap abort, a mid-read network error, or a stream that
 *   ended without `final`. The server is likely still working on the turn, so
 *   this reads as a timeout, never a connectivity failure.
 */
export type ChatStreamErrorKind = "connect" | "http" | "interrupted";

export class ChatStreamError extends Error {
  /** Structured cause — see ChatStreamErrorKind. */
  kind: ChatStreamErrorKind;
  /** HTTP 404/405/501 — the stream route is missing/disabled; safe to fall back. */
  capabilityMissing: boolean;
  /** Whether any stream bytes arrived before the failure (diagnostic only). */
  receivedAny: boolean;

  constructor(
    message: string,
    opts: {
      kind: ChatStreamErrorKind;
      capabilityMissing?: boolean;
      receivedAny?: boolean;
    },
  ) {
    super(message);
    this.name = "ChatStreamError";
    this.kind = opts.kind;
    this.capabilityMissing = opts.capabilityMissing ?? false;
    this.receivedAny = opts.receivedAny ?? false;
  }
}

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onStatus: (label: string) => void;
}

interface SseFrame {
  event: string;
  data: string;
}

function parseFrame(raw: string): SseFrame | null {
  const frame = raw.replace(/^\n+/, "");
  if (!frame || frame.startsWith(":")) return null; // heartbeat / comment
  let event: string | null = null;
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data += line.slice("data:".length).trim();
  }
  if (!event) return null;
  return { event, data };
}

function safeJson(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

export async function streamChatMessage(
  text: string,
  handlers: StreamHandlers,
): Promise<ChatMessageResponse> {
  const token = getSessionToken();
  const url = `${env.apiBaseUrl}${env.apiPrefix}/chat/message/stream`;
  const controller = new AbortController();
  const hardCap = setTimeout(() => controller.abort(), HARD_CAP_MS);
  let stallTimer: ReturnType<typeof setTimeout> | null = null;
  const armStall = () => {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => controller.abort(), STALL_MS);
  };
  const clearTimers = () => {
    if (stallTimer) clearTimeout(stallTimer);
    clearTimeout(hardCap);
  };

  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "X-App-Build": APP_BUILD,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
  } catch {
    clearTimers();
    // Pre-response failure (connect/DNS/abort). NOT capability-missing — the
    // POST may have reached the server, so do NOT auto-fall-back.
    throw new ChatStreamError("stream_connect_failed", { kind: "connect" });
  }

  if (response.status === 404 || response.status === 405 || response.status === 501) {
    clearTimers();
    throw new ChatStreamError("stream_unsupported", {
      kind: "http",
      capabilityMissing: true,
    });
  }
  if (response.status === 401) {
    clearTimers();
    await notifyUnauthorized();
    throw new ChatStreamError("stream_unauthorized", { kind: "http" });
  }
  if (!response.ok || !response.body) {
    clearTimers();
    throw new ChatStreamError(`stream_http_${response.status}`, { kind: "http" });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder(); // UTF-8; {stream:true} goes on decode()
  let buffer = "";
  let receivedAny = false;
  let final: ChatMessageResponse | null = null;
  armStall();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      receivedAny = true;
      armStall(); // any bytes (incl. heartbeats) reset the stall timer
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? ""; // retain the incomplete tail
      for (const raw of frames) {
        const f = parseFrame(raw);
        if (!f) continue;
        if (f.event === "delta") {
          const t = safeJson(f.data)?.text;
          if (typeof t === "string") handlers.onDelta(t);
        } else if (f.event === "status") {
          const l = safeJson(f.data)?.label;
          if (typeof l === "string") handlers.onStatus(l);
        } else if (f.event === "final") {
          final = safeJson(f.data) as ChatMessageResponse | null;
        }
        // "started" + comments: ignored.
      }
      if (final != null) break; // terminal frame in hand — stop promptly
    }
  } catch {
    clearTimers();
    // Abort (stall / hard cap) or a mid-stream read error. If we already have
    // the final frame, honor it; otherwise it's a non-retryable interruption.
    if (final != null) return final;
    throw new ChatStreamError("stream_interrupted", {
      kind: "interrupted",
      receivedAny,
    });
  } finally {
    clearTimers();
  }

  if (final == null) {
    throw new ChatStreamError("stream_ended_without_final", {
      kind: "interrupted",
      receivedAny,
    });
  }
  return final;
}
