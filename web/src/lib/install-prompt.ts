import { useEffect, useState } from "react";

// Phase 6e B13: capture the browser's `beforeinstallprompt` event and
// expose a hook that gates the install banner to the 3rd or later
// session (anti-friction per the spec). A "session" is a distinct
// load of the SPA tab; we bump a localStorage counter on mount.

const SESSION_COUNTER_KEY = "centro:session_count";
const DISMISSED_KEY = "centro:install_dismissed";
const MIN_SESSIONS_BEFORE_PROMPT = 3;

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

function readSessionCount(): number {
  try {
    const raw = window.localStorage.getItem(SESSION_COUNTER_KEY);
    const parsed = raw ? Number.parseInt(raw, 10) : 0;
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  } catch {
    return 0;
  }
}

function bumpSessionCount(): number {
  try {
    const next = readSessionCount() + 1;
    window.localStorage.setItem(SESSION_COUNTER_KEY, String(next));
    return next;
  } catch {
    return MIN_SESSIONS_BEFORE_PROMPT; // assume eligible if storage is broken
  }
}

function isDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

function markDismissed(): void {
  try {
    window.localStorage.setItem(DISMISSED_KEY, "1");
  } catch {
    // ignore
  }
}

export interface InstallPromptHandle {
  shouldShow: boolean;
  install: () => Promise<"accepted" | "dismissed" | "unavailable">;
  dismiss: () => void;
}

export function useInstallPrompt(): InstallPromptHandle {
  const [event, setEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [sessionCount, setSessionCount] = useState<number>(0);
  const [dismissed, setDismissed] = useState<boolean>(false);

  useEffect(() => {
    // Bump once on mount.
    setSessionCount(bumpSessionCount());
    setDismissed(isDismissed());

    function handler(rawEvent: Event) {
      // Cast: the global event type doesn't carry `prompt`.
      const e = rawEvent as BeforeInstallPromptEvent;
      e.preventDefault();
      setEvent(e);
    }
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const shouldShow =
    event !== null &&
    !dismissed &&
    sessionCount >= MIN_SESSIONS_BEFORE_PROMPT;

  return {
    shouldShow,
    install: async () => {
      if (event === null) return "unavailable";
      try {
        await event.prompt();
        const choice = await event.userChoice;
        if (choice.outcome === "dismissed") {
          markDismissed();
          setDismissed(true);
        }
        setEvent(null);
        return choice.outcome;
      } catch {
        return "unavailable";
      }
    },
    dismiss: () => {
      markDismissed();
      setDismissed(true);
    },
  };
}
