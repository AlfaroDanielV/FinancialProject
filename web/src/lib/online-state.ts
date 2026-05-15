import { useEffect, useState } from "react";

// Phase 6e B13: lightweight wrapper over navigator.onLine for the
// "Sin conexión" banner. Edits are intentionally NOT blocked (decision
// 4 in §19): we just surface the state and let existing form error UX
// handle failed mutations.
export function useOnlineState(): boolean {
  const initial = typeof navigator !== "undefined" ? navigator.onLine : true;
  const [online, setOnline] = useState<boolean>(initial);

  useEffect(() => {
    function handleOnline() {
      setOnline(true);
    }
    function handleOffline() {
      setOnline(false);
    }
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return online;
}
