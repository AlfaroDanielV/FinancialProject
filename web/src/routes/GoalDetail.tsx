import { AxiosError } from "axios";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { fetchAccount } from "../api/accounts";
import {
  abandonGoal,
  addGoalContribution,
  fetchGoal,
  fetchGoalContributions,
  fetchGoalForecast,
  markGoalAchieved,
  pauseGoal,
  resumeGoal,
} from "../api/goals";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import { GoalDetail as GoalDetailEntity } from "../schemas/entities";

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function progressPercent(goal: GoalDetailEntity) {
  const target = Number(goal.target_amount);
  const current = Number(goal.current_amount);
  if (!Number.isFinite(target) || target <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((current / target) * 100)));
}

function formatLongDate(iso: string | null) {
  if (!iso) return "Sin fecha";
  const [year, month, day] = iso.split("T")[0].split("-").map(Number);
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

export default function GoalDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [contributionOpen, setContributionOpen] = useState(false);

  const goalQuery = useQuery({
    queryKey: ["goal", id],
    queryFn: () => fetchGoal(id),
    enabled: Boolean(id),
  });
  const contributionsQuery = useQuery({
    queryKey: ["goal-contributions", id],
    queryFn: () => fetchGoalContributions(id),
    enabled: Boolean(id),
  });
  const forecastQuery = useQuery({
    queryKey: ["goal-forecast", id],
    queryFn: () => fetchGoalForecast(id),
    enabled: Boolean(id),
  });
  const linkedAccountQuery = useQuery({
    queryKey: ["account", goalQuery.data?.linked_account_id],
    queryFn: () =>
      goalQuery.data?.linked_account_id
        ? fetchAccount(goalQuery.data.linked_account_id)
        : null,
    enabled: Boolean(goalQuery.data?.linked_account_id),
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["goal", id] });
    queryClient.invalidateQueries({ queryKey: ["goal-contributions", id] });
    queryClient.invalidateQueries({ queryKey: ["goal-forecast", id] });
    queryClient.invalidateQueries({ queryKey: ["goals"] });
  };

  const achieveMutation = useMutation({
    mutationFn: () => markGoalAchieved(id),
    onSuccess: invalidateAll,
    onError: (err) =>
      setError(apiErrorMessage(err, "No pudimos marcar la meta.")),
  });
  const pauseMutation = useMutation({
    mutationFn: () => pauseGoal(id),
    onSuccess: invalidateAll,
    onError: (err) => setError(apiErrorMessage(err, "No pudimos pausar.")),
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeGoal(id),
    onSuccess: invalidateAll,
    onError: (err) => setError(apiErrorMessage(err, "No pudimos reanudar.")),
  });
  const abandonMutation = useMutation({
    mutationFn: () => abandonGoal(id),
    onSuccess: () => {
      invalidateAll();
      navigate("/goals");
    },
    onError: (err) =>
      setError(apiErrorMessage(err, "No pudimos abandonar la meta.")),
  });

  if (!id) {
    return <p className="text-slate-700">Meta inválida.</p>;
  }
  if (goalQuery.isLoading) {
    return <p className="text-slate-500">Cargando meta...</p>;
  }
  if (goalQuery.isError || !goalQuery.data) {
    return (
      <div className="space-y-3">
        <Link to="/goals" className="text-sm font-medium text-accent">
          Volver a metas
        </Link>
        <p className="text-red-700">No pudimos encontrar esta meta.</p>
      </div>
    );
  }

  const goal = goalQuery.data;
  const percent = progressPercent(goal);
  const remaining = Number(goal.target_amount) - Number(goal.current_amount);
  const linkedAccount = linkedAccountQuery.data;

  return (
    <div className="space-y-5">
      <Link
        to="/goals"
        className="text-sm font-medium text-accent hover:text-accent-dark"
      >
        Volver a metas
      </Link>

      {error && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">{goal.name}</h1>
            <p className="mt-1 text-sm text-slate-500">
              Estado: {goal.status} · Fecha objetivo:{" "}
              {formatLongDate(goal.target_date)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-3xl font-bold text-slate-900">{percent}%</p>
          </div>
        </div>

        <div className="mt-3 h-3 overflow-hidden rounded-full bg-slate-100">
          <div
            className="h-full rounded-full bg-accent"
            style={{ width: `${percent}%` }}
          />
        </div>
        <p className="mt-2 text-sm text-slate-600">
          {displayMoney(goal.current_amount, goal.target_currency)} de{" "}
          {displayMoney(goal.target_amount, goal.target_currency)}
          {remaining > 0 && (
            <>
              {" · "}
              <span className="text-slate-700">
                Falta {displayMoney(remaining, goal.target_currency)}
              </span>
            </>
          )}
        </p>

        {linkedAccount && (
          <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
            Cuenta vinculada{" "}
            <strong>{linkedAccount.name}</strong>
            {" · "}saldo actual:{" "}
            {displayMoney(
              linkedAccount.current_balance ?? linkedAccount.initial_balance,
              linkedAccount.currency,
            )}
            . Solo referencia: el progreso sigue contando contribuciones
            explícitas.
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setError(null);
              setContributionOpen(true);
            }}
            disabled={goal.status === "achieved" || goal.status === "abandoned"}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
          >
            Agregar contribución
          </button>
          {goal.status === "active" && (
            <button
              type="button"
              onClick={() => pauseMutation.mutate()}
              disabled={pauseMutation.isPending}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
            >
              Pausar
            </button>
          )}
          {goal.status === "paused" && (
            <button
              type="button"
              onClick={() => resumeMutation.mutate()}
              disabled={resumeMutation.isPending}
              className="min-h-11 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900 hover:bg-emerald-100"
            >
              Reanudar
            </button>
          )}
          {goal.status !== "achieved" && goal.status !== "abandoned" && (
            <button
              type="button"
              onClick={() => achieveMutation.mutate()}
              disabled={achieveMutation.isPending}
              className="min-h-11 rounded-md border border-emerald-300 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-50"
            >
              Marcar cumplida
            </button>
          )}
          {goal.status !== "abandoned" && (
            <button
              type="button"
              onClick={() => {
                if (window.confirm(`Abandonar ${goal.name}?`)) {
                  abandonMutation.mutate();
                }
              }}
              disabled={abandonMutation.isPending}
              className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50"
            >
              Abandonar
            </button>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-base font-semibold text-slate-900">Forecast</h2>
        {forecastQuery.isLoading && (
          <p className="text-sm text-slate-500">Calculando...</p>
        )}
        {forecastQuery.data && !forecastQuery.data.has_enough_data && (
          <p className="text-sm text-slate-600">
            Todavía no tengo suficiente historial. Agregá una contribución y
            volvé en un par de meses.
          </p>
        )}
        {forecastQuery.data &&
          forecastQuery.data.has_enough_data &&
          forecastQuery.data.months_to_target === 0 && (
            <p className="text-sm text-emerald-800">¡Ya llegaste a la meta!</p>
          )}
        {forecastQuery.data &&
          forecastQuery.data.has_enough_data &&
          forecastQuery.data.months_to_target !== null &&
          forecastQuery.data.months_to_target > 0 && (
            <p className="text-sm text-slate-700">
              A tu ritmo actual (
              {displayMoney(
                forecastQuery.data.avg_monthly_contribution,
                goal.target_currency,
              )}{" "}
              promedio los últimos 3 meses), llegás en{" "}
              <strong>{forecastQuery.data.months_to_target} meses</strong>
              {forecastQuery.data.projected_completion_date && (
                <>
                  {" "}
                  (~{formatLongDate(forecastQuery.data.projected_completion_date)}).
                </>
              )}
            </p>
          )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-base font-semibold text-slate-900">
          Histórico de contribuciones
        </h2>
        {contributionsQuery.isLoading && (
          <p className="mt-2 text-sm text-slate-500">Cargando...</p>
        )}
        {contributionsQuery.data && contributionsQuery.data.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">
            Sin contribuciones todavía.
          </p>
        )}
        {contributionsQuery.data && contributionsQuery.data.length > 0 && (
          <ul className="mt-2 divide-y divide-slate-100">
            {contributionsQuery.data.map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between py-2 text-sm"
              >
                <span className="text-slate-700">
                  {formatLongDate(row.occurred_at.slice(0, 10))}
                </span>
                <span className="font-semibold text-slate-900">
                  {displayMoney(row.amount, goal.target_currency)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {contributionOpen && (
        <AddContributionModal
          goal={goal}
          onClose={() => setContributionOpen(false)}
          onSaved={() => {
            invalidateAll();
            setContributionOpen(false);
          }}
        />
      )}
    </div>
  );
}

function AddContributionModal({
  goal,
  onClose,
  onSaved,
}: {
  goal: GoalDetailEntity;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [occurredAt, setOccurredAt] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [transactionId, setTransactionId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      const value = moneyToApiString(amount, goal.target_currency);
      if (value === null || Number(value) <= 0) {
        throw new Error("Monto inválido");
      }
      return addGoalContribution(goal.id, {
        amount: value,
        occurred_at: new Date(`${occurredAt}T12:00:00Z`).toISOString(),
        transaction_id: transactionId.trim() || undefined,
      });
    },
    onSuccess: () => onSaved(),
    onError: (err) =>
      setError(apiErrorMessage(err, "No pudimos guardar la contribución.")),
  });

  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center">
      <div className="w-full max-w-md space-y-3 rounded-lg bg-white p-5 shadow-lg">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">
            Agregar contribución
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 rounded-md px-2 text-slate-600"
          >
            Cerrar
          </button>
        </div>

        {error && (
          <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}

        <label className="block space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            Monto ({goal.target_currency})
          </span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            onBlur={() =>
              setAmount(formatMoneyInput(amount, goal.target_currency))
            }
            inputMode="decimal"
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span className="font-medium text-slate-700">Fecha</span>
          <input
            type="date"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={occurredAt}
            onChange={(event) => setOccurredAt(event.target.value)}
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            UUID de transacción (opcional)
          </span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={transactionId}
            onChange={(event) => setTransactionId(event.target.value)}
            placeholder="Pegá el id si la contribución salió de una transacción registrada"
          />
        </label>

        <div className="flex gap-2 pt-2">
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
          >
            {mutation.isPending ? "Guardando..." : "Guardar"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}
