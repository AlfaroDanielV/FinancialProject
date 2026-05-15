import { Suspense, lazy } from "react";
import { Routes, Route, Link } from "react-router-dom";

import { BottomNav } from "./components/shell/BottomNav";
import { InstallBanner } from "./components/shell/InstallBanner";
import { OfflineBanner } from "./components/shell/OfflineBanner";
import { useAuth } from "./lib/auth";
import Dashboard from "./routes/Dashboard";
import Expired from "./routes/Expired";
import AccountsNew from "./routes/AccountsNew";
import IncomesNew from "./routes/IncomesNew";
import DebtsNew from "./routes/DebtsNew";
import BillsNew from "./routes/BillsNew";

const AccountsIndex = lazy(() => import("./routes/AccountsIndex"));
const AccountDetail = lazy(() => import("./routes/AccountDetail"));
const TransactionsIndex = lazy(() => import("./routes/TransactionsIndex"));
const BillsIndex = lazy(() => import("./routes/BillsIndex"));
const DebtsIndex = lazy(() => import("./routes/DebtsIndex"));
const DebtDetail = lazy(() => import("./routes/DebtDetail"));
const IncomesIndex = lazy(() => import("./routes/IncomesIndex"));
const GoalsIndex = lazy(() => import("./routes/GoalsIndex"));
const GoalDetail = lazy(() => import("./routes/GoalDetail"));
const GoalsNew = lazy(() => import("./routes/GoalsNew"));
const MemoryIndex = lazy(() => import("./routes/MemoryIndex"));
const CategoriesIndex = lazy(() => import("./routes/CategoriesIndex"));

export default function App() {
  const auth = useAuth();

  return (
    <div className="min-h-screen flex flex-col pb-[calc(env(safe-area-inset-bottom)+4rem)] sm:pb-0">
      <OfflineBanner />
      <header className="border-b border-slate-200 bg-white pt-[env(safe-area-inset-top)]">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold text-slate-900">
            Centro Financiero
          </Link>
          <span className="text-sm text-slate-500">Telegram primero</span>
        </div>
      </header>

      <main className="flex-1">
        <div className="max-w-6xl mx-auto px-4 py-6 space-y-4">
          {auth.kind === "authenticated" && <InstallBanner />}
          {auth.kind === "loading" && (
            <p className="text-slate-500">Validando tu sesión...</p>
          )}
          {auth.kind !== "loading" && (
            <Suspense
              fallback={<p className="text-slate-500">Cargando módulo...</p>}
            >
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/expired" element={<Expired />} />
                <Route path="/accounts" element={<AccountsIndex />} />
                <Route path="/accounts/new" element={<AccountsNew />} />
                <Route path="/accounts/:id" element={<AccountDetail />} />
                <Route path="/onboarding/cuentas" element={<AccountsNew />} />
                <Route path="/incomes" element={<IncomesIndex />} />
                <Route path="/incomes/new" element={<IncomesNew />} />
                <Route path="/onboarding/ingresos" element={<IncomesNew />} />
                <Route path="/debts" element={<DebtsIndex />} />
                <Route path="/debts/new" element={<DebtsNew />} />
                <Route path="/debts/:id" element={<DebtDetail />} />
                <Route path="/onboarding/deudas" element={<DebtsNew />} />
                <Route path="/bills" element={<BillsIndex />} />
                <Route path="/bills/new" element={<BillsNew />} />
                <Route path="/onboarding/gastos" element={<BillsNew />} />
                <Route path="/transactions" element={<TransactionsIndex />} />
                <Route path="/goals" element={<GoalsIndex />} />
                <Route path="/goals/new" element={<GoalsNew />} />
                <Route path="/goals/:id" element={<GoalDetail />} />
                <Route path="/memoria" element={<MemoryIndex />} />
                <Route path="/categories" element={<CategoriesIndex />} />
              </Routes>
            </Suspense>
          )}
        </div>
      </main>
      <BottomNav />
    </div>
  );
}

