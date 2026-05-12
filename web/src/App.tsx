import { Routes, Route, Link } from "react-router-dom";

import { useAuth } from "./lib/auth";
import Dashboard from "./routes/Dashboard";
import Expired from "./routes/Expired";
import AccountsNew from "./routes/AccountsNew";
import IncomesNew from "./routes/IncomesNew";
import DebtsNew from "./routes/DebtsNew";
import BillsNew from "./routes/BillsNew";

export default function App() {
  const auth = useAuth();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold text-slate-900">
            Centro Financiero
          </Link>
          <span className="text-sm text-slate-500">Telegram primero</span>
        </div>
      </header>

      <main className="flex-1">
        <div className="max-w-6xl mx-auto px-4 py-6">
          {auth.kind === "loading" && (
            <p className="text-slate-500">Validando tu sesión...</p>
          )}
          {auth.kind !== "loading" && (
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/expired" element={<Expired />} />
              <Route path="/accounts" element={<AccountsNew />} />
              <Route path="/accounts/new" element={<AccountsNew />} />
              <Route path="/onboarding/cuentas" element={<AccountsNew />} />
              <Route path="/incomes" element={<IncomesNew />} />
              <Route path="/incomes/new" element={<IncomesNew />} />
              <Route path="/onboarding/ingresos" element={<IncomesNew />} />
              <Route path="/debts" element={<DebtsNew />} />
              <Route path="/debts/new" element={<DebtsNew />} />
              <Route path="/onboarding/deudas" element={<DebtsNew />} />
              <Route path="/bills" element={<BillsNew />} />
              <Route path="/bills/new" element={<BillsNew />} />
              <Route path="/onboarding/gastos" element={<BillsNew />} />
              <Route path="/transactions" element={<ModulePlaceholder title="Movimientos" />} />
              <Route path="/goals" element={<ModulePlaceholder title="Metas" />} />
              <Route path="/memoria" element={<ModulePlaceholder title="Memoria" />} />
            </Routes>
          )}
        </div>
      </main>
    </div>
  );
}

function ModulePlaceholder({ title }: { title: string }) {
  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
        Volver
      </Link>
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        <p className="mt-1 text-slate-600">Esta vista entra en un bloque próximo.</p>
      </section>
    </div>
  );
}
