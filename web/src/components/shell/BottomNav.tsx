import { NavLink } from "react-router-dom";

// Phase 6e B13: mobile-only persistent bottom navigation. Hidden at
// the `sm:` breakpoint (≥640px) so desktop keeps its quick-link grid
// on the dashboard. Each tab is a 44px-tall touch target with safe-
// area padding so it clears the iPhone home indicator.

const TABS: Array<{ to: string; label: string }> = [
  { to: "/", label: "Inicio" },
  { to: "/accounts", label: "Cuentas" },
  { to: "/debts", label: "Deudas" },
  { to: "/transactions", label: "Movimientos" },
  { to: "/memoria", label: "Más" },
];

export function BottomNav() {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] shadow-[0_-4px_12px_rgba(15,23,42,0.06)] sm:hidden"
      aria-label="Navegación principal"
    >
      <ul className="grid grid-cols-5">
        {TABS.map((tab) => (
          <li key={tab.to} className="text-center">
            <NavLink
              to={tab.to}
              end={tab.to === "/"}
              className={({ isActive }) =>
                `flex min-h-[3rem] items-center justify-center px-2 py-2 text-xs font-medium ${
                  isActive
                    ? "text-accent"
                    : "text-slate-600 hover:text-slate-900"
                }`
              }
            >
              {tab.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
