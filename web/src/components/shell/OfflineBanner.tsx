import { useEffect, useState } from "react";

import { useOnlineState } from "../../lib/online-state";

// Phase 6e B13: persistent banner while the browser reports offline.
// Shows the date of the last successful render as a soft signal that
// data may be stale. Mutations are NOT blocked here (see decision §19) —
// failed POST/PATCH calls surface via the per-form error states.

function formatLastSeen(timestamp: number): string {
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

export function OfflineBanner() {
  const online = useOnlineState();
  const [lastOnline, setLastOnline] = useState<number>(Date.now());

  useEffect(() => {
    if (online) setLastOnline(Date.now());
  }, [online]);

  if (online) return null;
  return (
    <div
      role="status"
      className="fixed inset-x-0 top-0 z-30 bg-amber-100 px-4 py-2 text-center text-sm text-amber-900 shadow-sm"
    >
      Sin conexión. Mostrando datos del {formatLastSeen(lastOnline)}. Abrí
      con internet para actualizar.
    </div>
  );
}
