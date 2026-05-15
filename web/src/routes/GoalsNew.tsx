import { AxiosError } from "axios";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchAccounts } from "../api/accounts";
import { createGoal } from "../api/goals";
import { formatMoneyInput, moneyToApiString } from "../lib/money";
import { Currency } from "../schemas/entities";

const CURRENCY_OPTIONS: Array<{ value: Currency; label: string }> = [
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export default function GoalsNew() {
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [targetAmount, setTargetAmount] = useState("");
  const [targetCurrency, setTargetCurrency] = useState<Currency>("CRC");
  const [targetDate, setTargetDate] = useState("");
  const [monthlyContribution, setMonthlyContribution] = useState("");
  const [priority, setPriority] = useState("3");
  const [linkedAccountId, setLinkedAccountId] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const accountsQuery = useQuery({
    queryKey: ["accounts", false],
    queryFn: () => fetchAccounts(false),
  });
  const accounts = accountsQuery.data ?? [];

  async function submit() {
    setServerError(null);
    if (name.trim().length < 2) {
      setServerError("Poné un nombre.");
      return;
    }
    const amount = moneyToApiString(targetAmount, targetCurrency);
    if (amount === null || Number(amount) <= 0) {
      setServerError("Poné un monto objetivo válido.");
      return;
    }

    const monthly = monthlyContribution
      ? moneyToApiString(monthlyContribution, targetCurrency)
      : null;
    const payload: Parameters<typeof createGoal>[0] = {
      name: name.trim(),
      target_amount: amount,
      target_currency: targetCurrency,
      priority: Number(priority) || 3,
      ...(targetDate ? { target_date: targetDate } : {}),
      ...(monthly && Number(monthly) > 0
        ? { monthly_contribution: monthly }
        : {}),
      ...(linkedAccountId ? { linked_account_id: linkedAccountId } : {}),
    };

    setSubmitting(true);
    try {
      const goal = await createGoal(payload);
      navigate(`/goals/${goal.id}`);
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos crear la meta."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <Link
          to="/goals"
          className="text-sm font-medium text-accent hover:text-accent-dark"
        >
          Volver a metas
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Nueva meta</h1>
          <p className="mt-1 text-slate-600">
            Ingresá el objetivo y, si querés, una cuenta vinculada para
            sanity-checks.
          </p>
        </div>
      </header>

      {serverError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </p>
      )}

      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="font-medium text-slate-700">Nombre</span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            minLength={2}
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Monto objetivo</span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={targetAmount}
            onChange={(event) => setTargetAmount(event.target.value)}
            onBlur={() =>
              setTargetAmount(formatMoneyInput(targetAmount, targetCurrency))
            }
            inputMode="decimal"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Moneda</span>
          <select
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={targetCurrency}
            onChange={(event) =>
              setTargetCurrency(event.target.value as Currency)
            }
          >
            {CURRENCY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Fecha objetivo (opcional)</span>
          <input
            type="date"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={targetDate}
            onChange={(event) => setTargetDate(event.target.value)}
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Aporte mensual sugerido (opcional)</span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={monthlyContribution}
            onChange={(event) => setMonthlyContribution(event.target.value)}
            onBlur={() =>
              monthlyContribution &&
              setMonthlyContribution(
                formatMoneyInput(monthlyContribution, targetCurrency),
              )
            }
            inputMode="decimal"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Prioridad (1-5)</span>
          <input
            type="number"
            min={1}
            max={5}
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </label>

        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="font-medium text-slate-700">
            Cuenta vinculada (opcional)
          </span>
          <select
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={linkedAccountId}
            onChange={(event) => setLinkedAccountId(event.target.value)}
          >
            <option value="">Sin cuenta vinculada</option>
            {accounts
              .filter((acc) => !acc.archived)
              .map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.name} ({acc.currency})
                </option>
              ))}
          </select>
          <span className="block text-xs text-slate-500">
            Si la vinculás, te mostramos el saldo actual de la cuenta como
            referencia. El progreso sigue contando contribuciones explícitas.
          </span>
        </label>

        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={submitting}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
          >
            {submitting ? "Guardando..." : "Crear meta"}
          </button>
        </div>
      </form>
    </div>
  );
}
