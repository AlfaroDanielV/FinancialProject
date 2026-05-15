import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchDebtOverview, fetchDebts } from "../api/debts";
import { fetchUpcomingFeed } from "../api/dashboard";
import { api } from "../api/client";
import { displayMoney } from "../lib/money";
import { DebtSummary } from "../schemas/entities";

const DEBT_TYPE_LABELS: Record<string, string> = {
  mortgage: "Hipoteca",
  credit_card_balance: "Tarjeta de crédito",
  credit_card: "Tarjeta de crédito",
  auto_loan: "Préstamo de auto",
  personal_loan: "Préstamo personal",
  student_loan: "Préstamo estudiantil",
  other: "Otro",
};

async function fetchTotalMonthlyIncome(): Promise<number> {
  // Phase 6d recurring incomes endpoint; we just sum amounts for active rows.
  try {
    const resp = await api.get("/recurring-incomes");
    if (!Array.isArray(resp.data)) return 0;
    return resp.data
      .filter((row: { is_active?: boolean }) => row?.is_active !== false)
      .reduce((sum: number, row: { amount?: number | string | null }) => {
        const value = row.amount === null || row.amount === undefined ? 0 : Number(row.amount);
        return sum + (Number.isFinite(value) ? value : 0);
      }, 0);
  } catch {
    return 0;
  }
}

function dtiLabel(monthlyDebtService: number, monthlyIncome: number) {
  if (monthlyIncome <= 0) return "—";
  const ratio = monthlyDebtService / monthlyIncome;
  return `${(ratio * 100).toFixed(1)}%`;
}

function dtiTone(monthlyDebtService: number, monthlyIncome: number) {
  if (monthlyIncome <= 0) return "text-slate-500";
  const ratio = monthlyDebtService / monthlyIncome;
  if (ratio > 0.4) return "text-red-700";
  if (ratio > 0.3) return "text-amber-700";
  return "text-emerald-700";
}

export default function DebtsIndex() {
  const [showArchived, setShowArchived] = useState(false);

  const debtsQuery = useQuery({
    queryKey: ["debts", showArchived],
    queryFn: () => fetchDebts(showArchived),
  });
  const overviewQuery = useQuery({
    queryKey: ["debts", "overview"],
    queryFn: fetchDebtOverview,
  });
  const incomesQuery = useQuery({
    queryKey: ["recurring-incomes", "total"],
    queryFn: fetchTotalMonthlyIncome,
  });
  const upcomingQuery = useQuery({
    queryKey: ["upcoming-feed", "debts"],
    queryFn: () => {
      const today = new Date().toISOString().slice(0, 10);
      const horizon = new Date();
      horizon.setDate(horizon.getDate() + 30);
      return fetchUpcomingFeed(today, horizon.toISOString().slice(0, 10));
    },
  });

  const debts = debtsQuery.data ?? [];
  const overview = overviewQuery.data;
  const monthlyIncome = incomesQuery.data ?? 0;

  const sortedDebts = useMemo(() => {
    return [...debts].sort((a, b) => {
      const aActive = !a.archived;
      const bActive = !b.archived;
      if (aActive !== bActive) return aActive ? -1 : 1;
      return b.current_balance - a.current_balance;
    });
  }, [debts]);

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Deudas</h1>
            <p className="mt-1 text-slate-600">
              Lista completa, métricas mensuales y enlace a la calculadora.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/debts/new"
              className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
            >
              Nueva deuda
            </Link>
          </div>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs text-slate-500">Deuda total</p>
          <p className="mt-1 text-xl font-semibold text-slate-950">
            {overview ? displayMoney(overview.total_debt, "CRC") : "—"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs text-slate-500">Servicio mensual</p>
          <p className="mt-1 text-xl font-semibold text-slate-950">
            {overview ? displayMoney(overview.total_minimum_monthly, "CRC") : "—"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs text-slate-500">DTI estimado</p>
          <p
            className={`mt-1 text-xl font-semibold ${dtiTone(
              overview?.total_minimum_monthly ?? 0,
              monthlyIncome,
            )}`}
          >
            {overview
              ? dtiLabel(overview.total_minimum_monthly, monthlyIncome)
              : "—"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Servicio mensual sobre ingreso recurrente.
          </p>
        </div>
      </section>

      <div className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-4 py-3">
        <p className="text-sm text-slate-600">
          {debts.filter((d) => !d.archived).length} visible
          {debts.filter((d) => !d.archived).length === 1 ? "" : "s"}
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Mostrar archivadas
        </label>
      </div>

      {debtsQuery.isLoading && <p className="text-slate-500">Cargando deudas...</p>}
      {!debtsQuery.isLoading && sortedDebts.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>Todavía no tenés deudas registradas.</p>
          <Link
            to="/debts/new"
            className="mt-3 inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark"
          >
            Registrar la primera
          </Link>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {sortedDebts.map((debt) => (
          <DebtCard key={debt.id} debt={debt} />
        ))}
      </div>

      {upcomingQuery.data && upcomingQuery.data.items.length > 0 && (
        <p className="text-xs text-slate-500">
          Próximos pagos también aparecen en{" "}
          <Link to="/bills" className="font-medium text-accent">
            Gastos fijos
          </Link>
          .
        </p>
      )}
    </div>
  );
}

function DebtCard({ debt }: { debt: DebtSummary }) {
  const typeLabel = DEBT_TYPE_LABELS[debt.debt_type] ?? debt.debt_type;
  return (
    <Link
      to={`/debts/${debt.id}`}
      className={`block rounded-lg border bg-white p-4 shadow-sm transition hover:shadow ${
        debt.archived ? "border-slate-200 opacity-70" : "border-slate-200"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{debt.name}</p>
          <p className="text-sm text-slate-500">{typeLabel}</p>
          {!debt.is_active && !debt.archived && (
            <span className="mt-1 inline-block rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
              Pausada
            </span>
          )}
          {debt.archived && (
            <span className="mt-1 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
              Archivada
            </span>
          )}
        </div>
        <div className="text-right">
          <p className="text-xs text-slate-500">Saldo</p>
          <p className="text-lg font-semibold text-slate-950">
            {displayMoney(debt.current_balance, "CRC")}
          </p>
          <p className="text-xs text-slate-500">
            Cuota: {displayMoney(debt.minimum_payment, "CRC")}
          </p>
          <p className="text-xs text-slate-500">
            Tasa: {(debt.interest_rate * 100).toFixed(2)}%
          </p>
        </div>
      </div>
    </Link>
  );
}
