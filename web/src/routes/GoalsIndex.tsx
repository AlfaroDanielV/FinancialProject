import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchGoals } from "../api/goals";
import { displayMoney } from "../lib/money";
import { GoalDetail } from "../schemas/entities";

const STATUS_LABELS: Record<string, string> = {
  active: "Activas",
  paused: "Pausadas",
  achieved: "Cumplidas",
  abandoned: "Abandonadas",
};

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "Todas" },
  { value: "active", label: "Activas" },
  { value: "paused", label: "Pausadas" },
  { value: "achieved", label: "Cumplidas" },
  { value: "abandoned", label: "Abandonadas" },
];

const CURRENCY_OPTIONS = [
  { value: "", label: "Todas" },
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

function progressPercent(goal: GoalDetail) {
  const target = Number(goal.target_amount);
  const current = Number(goal.current_amount);
  if (!Number.isFinite(target) || target <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((current / target) * 100)));
}

function daysUntil(targetDate: string | null) {
  if (!targetDate) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const [year, month, day] = targetDate.split("-").map(Number);
  const target = new Date(year, month - 1, day);
  return Math.ceil((target.getTime() - today.getTime()) / 86_400_000);
}

function timeRemainingLabel(targetDate: string | null) {
  const days = daysUntil(targetDate);
  if (days === null) return null;
  if (days < 0) return `${Math.abs(days)} días pasados`;
  if (days === 0) return "Hoy";
  if (days < 31) return `${days} días`;
  const months = Math.round(days / 30.44);
  return `~${months} mes${months === 1 ? "" : "es"}`;
}

export default function GoalsIndex() {
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [currencyFilter, setCurrencyFilter] = useState<string>("");

  const goalsQuery = useQuery({
    queryKey: ["goals", statusFilter || "all"],
    queryFn: () => fetchGoals(statusFilter || undefined),
  });

  const goals = goalsQuery.data ?? [];

  const filtered = useMemo(() => {
    return goals.filter((goal) => {
      if (currencyFilter && goal.target_currency !== currencyFilter) return false;
      return true;
    });
  }, [goals, currencyFilter]);

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Metas</h1>
            <p className="mt-1 text-slate-600">
              Lo que estás juntando, con progreso y forecast a tu ritmo.
            </p>
          </div>
          <Link
            to="/goals/new"
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
          >
            Nueva meta
          </Link>
        </div>
      </header>

      <section className="grid gap-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-2">
        <label className="text-xs text-slate-500">
          Estado
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-500">
          Moneda
          <select
            value={currencyFilter}
            onChange={(event) => setCurrencyFilter(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            {CURRENCY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      {goalsQuery.isLoading && <p className="text-slate-500">Cargando metas...</p>}
      {!goalsQuery.isLoading && filtered.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>No tenés metas con estos filtros.</p>
          <Link
            to="/goals/new"
            className="mt-3 inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark"
          >
            Crear la primera
          </Link>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {filtered.map((goal) => (
          <GoalCard key={goal.id} goal={goal} />
        ))}
      </div>
    </div>
  );
}

function GoalCard({ goal }: { goal: GoalDetail }) {
  const percent = progressPercent(goal);
  const remaining = timeRemainingLabel(goal.target_date);
  const statusLabel = STATUS_LABELS[goal.status] ?? goal.status;
  return (
    <Link
      to={`/goals/${goal.id}`}
      className="block space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:shadow"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{goal.name}</p>
          <p className="text-xs text-slate-500">
            {statusLabel}
            {remaining ? ` · ${remaining}` : ""}
          </p>
        </div>
        <p className="text-sm font-semibold text-slate-700">{percent}%</p>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="text-sm text-slate-500">
        {displayMoney(goal.current_amount, goal.target_currency)} de{" "}
        {displayMoney(goal.target_amount, goal.target_currency)}
      </p>
    </Link>
  );
}
