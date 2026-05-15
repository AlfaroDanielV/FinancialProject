import { useInstallPrompt } from "../../lib/install-prompt";

// Phase 6e B13: install-app banner gated to the 3rd or later visit
// (see `useInstallPrompt`). Hidden on iOS Safari (no
// `beforeinstallprompt`); users get the native add-to-home from the
// share menu instead. Dismissing is sticky in localStorage.

export function InstallBanner() {
  const { shouldShow, install, dismiss } = useInstallPrompt();
  if (!shouldShow) return null;
  return (
    <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span>
          Instalá Centro Financiero como app para acceder más rápido.
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              void install();
            }}
            className="min-h-11 rounded-md bg-blue-700 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-800"
          >
            Instalar
          </button>
          <button
            type="button"
            onClick={dismiss}
            className="min-h-11 rounded-md px-3 py-2 text-sm font-semibold text-blue-900"
          >
            Ahora no
          </button>
        </div>
      </div>
    </div>
  );
}
