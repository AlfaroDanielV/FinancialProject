import { Routes, Route, Link } from "react-router-dom";

import { useAuth } from "./lib/auth";
import Landing from "./routes/Landing";
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
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold text-slate-900">
            Finance Assistant
          </Link>
          <span className="text-sm text-slate-500">Setup</span>
        </div>
      </header>

      <main className="flex-1">
        <div className="max-w-3xl mx-auto px-4 py-8">
          {auth.kind === "loading" && (
            <p className="text-slate-500">Validando tu sesión...</p>
          )}
          {auth.kind !== "loading" && (
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/expired" element={<Expired />} />
              <Route path="/accounts/new" element={<AccountsNew />} />
              <Route path="/onboarding/cuentas" element={<AccountsNew />} />
              <Route path="/incomes/new" element={<IncomesNew />} />
              <Route path="/onboarding/ingresos" element={<IncomesNew />} />
              <Route path="/debts/new" element={<DebtsNew />} />
              <Route path="/onboarding/deudas" element={<DebtsNew />} />
              <Route path="/bills/new" element={<BillsNew />} />
              <Route path="/onboarding/gastos" element={<BillsNew />} />
            </Routes>
          )}
        </div>
      </main>
    </div>
  );
}
