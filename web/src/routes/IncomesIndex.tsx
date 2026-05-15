import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  archiveRecurringIncome,
  deriveCRCycles,
  fetchRecurringIncomes,
  pauseRecurringIncome,
  RecurringIncomeUpdatePayload,
  restoreRecurringIncome,
  resumeRecurringIncome,
  updateRecurringIncome,
} from "../api/incomes";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  IncomeFrequency,
  IncomeType,
  RecurringIncome,
} from "../schemas/entities";

const FREQ_LABELS: Record<IncomeFrequency, string> = {
  weekly: "Semanal",
  biweekly: "Quincenal",
  monthly: "Mensual",
  annual: "Anual",
};

const INCOME_TYPE_LABELS: Record<IncomeType, string> = {
  salary: "Salario",
  aguinaldo: "Aguinaldo",
  salario_escolar: "Salario escolar",
  freelance: "Freelance",
  other: "Otro",
};

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function formatShortDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

export default function IncomesIndex() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const created = searchParams.get("created");
  const createdName = searchParams.get("name");

  const [showArchived, setShowArchived] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [bannerStatus, setBannerStatus] = useState<string | null>(null);

  const incomesQuery = useQuery({
    queryKey: ["recurring-incomes", showArchived],
    queryFn: () => fetchRecurringIncomes(showArchived),
  });

  const incomes = incomesQuery.data ?? [];

  const visible = useMemo(() => {
    return [...incomes].sort((a, b) => {
      const aActive = !a.archived && a.is_active;
      const bActive = !b.archived && b.is_active;
      if (aActive !== bActive) return aActive ? -1 : 1;
      return a.next_payment_date.localeCompare(b.next_payment_date);
    });
  }, [incomes]);

  // Banner: user has a CRC salary but no aguinaldo / salario_escolar yet.
  const salaryForBanner = useMemo(() => {
    return incomes.find(
      (inc) =>
        inc.income_type === "salary" &&
        inc.is_active &&
        !inc.archived &&
        inc.currency === "CRC",
    );
  }, [incomes]);

  const hasAguinaldo = useMemo(
    () =>
      incomes.some(
        (inc) =>
          inc.income_type === "aguinaldo" &&
          !inc.archived &&
          inc.base_salary_link_id === (salaryForBanner?.id ?? ""),
      ),
    [incomes, salaryForBanner],
  );
  const hasSalarioEscolar = useMemo(
    () =>
      incomes.some(
        (inc) =>
          inc.income_type === "salario_escolar" &&
          !inc.archived &&
          inc.base_salary_link_id === (salaryForBanner?.id ?? ""),
      ),
    [incomes, salaryForBanner],
  );
  const showCycleBanner =
    Boolean(salaryForBanner) && (!hasAguinaldo || !hasSalarioEscolar);

  const deriveMutation = useMutation({
    mutationFn: (salaryId: string) => deriveCRCycles(salaryId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
      const created = [
        data.created_aguinaldo ? "aguinaldo" : null,
        data.created_salario_escolar ? "salario escolar" : null,
      ].filter(Boolean) as string[];
      setBannerStatus(
        created.length === 0
          ? "Ya tenías ambos cargados."
          : `Agregamos ${created.join(" y ")}.`,
      );
    },
    onError: (err) => {
      setBannerStatus(null);
      setServerError(
        apiErrorMessage(err, "No pudimos derivar los ciclos."),
      );
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: RecurringIncomeUpdatePayload }) =>
      updateRecurringIncome(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
      setEditingId(null);
    },
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos guardar.")),
  });

  const pauseMutation = useMutation({
    mutationFn: (id: string) => pauseRecurringIncome(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] }),
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos pausar.")),
  });

  const resumeMutation = useMutation({
    mutationFn: (id: string) => resumeRecurringIncome(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] }),
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos reanudar.")),
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archiveRecurringIncome(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] }),
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos archivar.")),
  });

  const restoreMutation = useMutation({
    mutationFn: (id: string) => restoreRecurringIncome(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] }),
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos restaurar.")),
  });

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link
          to="/"
          className="text-sm font-medium text-accent hover:text-accent-dark"
        >
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Ingresos</h1>
            <p className="mt-1 text-slate-600">
              Tus ingresos recurrentes y los ciclos CR (aguinaldo, salario
              escolar).
            </p>
          </div>
          <Link
            to="/incomes/new"
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
          >
            Nuevo ingreso
          </Link>
        </div>
      </header>

      {created === "income" && (
        <div className="flex items-start justify-between gap-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          <span>
            Ingreso guardado{createdName ? `: ${createdName}` : ""}.
          </span>
          <button
            type="button"
            className="min-h-11 rounded-md px-2 font-semibold text-green-900"
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.delete("created");
              next.delete("name");
              setSearchParams(next, { replace: true });
            }}
          >
            Cerrar
          </button>
        </div>
      )}

      {serverError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </p>
      )}

      {showCycleBanner && salaryForBanner && (
        <section className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
          <p className="font-semibold">
            Detectamos tu salario en colones. ¿Querés que agreguemos aguinaldo
            y salario escolar automáticamente?
          </p>
          <p className="mt-1 text-blue-800">
            Calculamos los montos con base en tu salario actual
            ({displayMoney(salaryForBanner.amount, salaryForBanner.currency)}{" "}
            mensual) usando la fórmula de la Ley 2412.
          </p>
          {bannerStatus && (
            <p className="mt-2 rounded-md bg-white px-3 py-2 text-blue-900">
              {bannerStatus}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => deriveMutation.mutate(salaryForBanner.id)}
              disabled={deriveMutation.isPending}
              className="min-h-11 rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-60"
            >
              {deriveMutation.isPending ? "Agregando..." : "Agregar ambos"}
            </button>
            <Link
              to="/incomes/new"
              className="min-h-11 rounded-md border border-blue-300 bg-white px-4 py-2 text-sm font-semibold text-blue-900 hover:bg-blue-50"
            >
              Manual
            </Link>
          </div>
        </section>
      )}

      <div className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-4 py-3">
        <p className="text-sm text-slate-600">
          {visible.filter((i) => !i.archived).length} visible
          {visible.filter((i) => !i.archived).length === 1 ? "" : "s"}
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Mostrar archivados
        </label>
      </div>

      {incomesQuery.isLoading && <p className="text-slate-500">Cargando...</p>}
      {incomesQuery.isError && (
        <p className="text-red-700">No pudimos cargar tus ingresos.</p>
      )}
      {!incomesQuery.isLoading && visible.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>Todavía no tenés ingresos cargados.</p>
          <Link
            to="/incomes/new"
            className="mt-3 inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark"
          >
            Cargar el primero
          </Link>
        </div>
      )}

      <div className="space-y-3">
        {visible.map((income) => (
          <IncomeRow
            key={income.id}
            income={income}
            editing={editingId === income.id}
            onBeginEdit={() => {
              setEditingId(income.id);
              setServerError(null);
            }}
            onCancelEdit={() => setEditingId(null)}
            onSave={(payload) =>
              updateMutation.mutate({ id: income.id, payload })
            }
            saving={updateMutation.isPending}
            onPause={() => pauseMutation.mutate(income.id)}
            onResume={() => resumeMutation.mutate(income.id)}
            onArchive={() => {
              if (
                window.confirm(`Archivar ${income.name}? Se sacará del resumen.`)
              ) {
                archiveMutation.mutate(income.id);
              }
            }}
            onRestore={() => restoreMutation.mutate(income.id)}
          />
        ))}
      </div>
    </div>
  );
}

function IncomeRow({
  income,
  editing,
  onBeginEdit,
  onCancelEdit,
  onSave,
  saving,
  onPause,
  onResume,
  onArchive,
  onRestore,
}: {
  income: RecurringIncome;
  editing: boolean;
  onBeginEdit: () => void;
  onCancelEdit: () => void;
  onSave: (payload: RecurringIncomeUpdatePayload) => void;
  saving: boolean;
  onPause: () => void;
  onResume: () => void;
  onArchive: () => void;
  onRestore: () => void;
}) {
  const [name, setName] = useState(income.name);
  const [amount, setAmount] = useState(
    income.amount ? formatMoneyInput(String(income.amount), income.currency as "CRC" | "USD") : "",
  );
  const [frequency, setFrequency] = useState<IncomeFrequency>(income.frequency);
  const [nextDate, setNextDate] = useState(income.next_payment_date);
  const [notes, setNotes] = useState(income.notes ?? "");

  if (editing) {
    return (
      <form
        className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
        onSubmit={(event) => {
          event.preventDefault();
          const payload: RecurringIncomeUpdatePayload = {
            name: name.trim(),
            frequency,
            next_payment_date: nextDate,
            notes: notes.trim() || undefined,
          };
          if (amount) {
            const value = moneyToApiString(amount, income.currency as "CRC" | "USD");
            if (value !== null) payload.amount = value;
          }
          onSave(payload);
        }}
      >
        <div className="grid gap-2 sm:grid-cols-2">
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
            <span className="font-medium text-slate-700">
              Monto ({income.currency})
            </span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              onBlur={() =>
                setAmount(
                  formatMoneyInput(amount, income.currency as "CRC" | "USD"),
                )
              }
              inputMode="decimal"
              disabled={income.income_type !== "salary" && income.income_type !== "freelance" && income.income_type !== "other"}
              title={
                income.income_type === "aguinaldo" ||
                income.income_type === "salario_escolar"
                  ? "Monto derivado del salario base — no editable."
                  : ""
              }
            />
            {(income.income_type === "aguinaldo" ||
              income.income_type === "salario_escolar") && (
              <span className="text-xs text-slate-500">
                Derivado del salario base. Cambiá el salario para recalcular.
              </span>
            )}
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Frecuencia</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={frequency}
              onChange={(event) =>
                setFrequency(event.target.value as IncomeFrequency)
              }
            >
              {Object.entries(FREQ_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Próximo pago</span>
            <input
              type="date"
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={nextDate}
              onChange={(event) => setNextDate(event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span className="font-medium text-slate-700">Notas</span>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              rows={2}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>
        </div>
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={saving}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
          >
            {saving ? "Guardando..." : "Guardar"}
          </button>
          <button
            type="button"
            onClick={onCancelEdit}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Cancelar
          </button>
        </div>
      </form>
    );
  }

  return (
    <article
      className={`flex flex-col gap-3 rounded-lg border bg-white p-4 shadow-sm sm:flex-row sm:items-start sm:justify-between ${
        income.archived ? "border-slate-200 opacity-70" : "border-slate-200"
      }`}
    >
      <div>
        <p className="font-semibold text-slate-900">
          {income.name}
          {!income.is_active && !income.archived && (
            <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
              Pausado
            </span>
          )}
          {income.archived && (
            <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
              Archivado
            </span>
          )}
        </p>
        <p className="text-sm text-slate-500">
          {INCOME_TYPE_LABELS[income.income_type] ?? income.income_type} ·{" "}
          {FREQ_LABELS[income.frequency]} · próximo:{" "}
          {formatShortDate(income.next_payment_date)}
        </p>
      </div>
      <div className="flex flex-col items-end gap-2">
        <p className="text-lg font-semibold text-slate-950">
          {income.amount === null
            ? "Sin monto"
            : displayMoney(income.amount, income.currency)}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          {!income.archived && (
            <button
              type="button"
              onClick={onBeginEdit}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              Editar
            </button>
          )}
          {income.is_active && !income.archived && (
            <button
              type="button"
              onClick={onPause}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              Pausar
            </button>
          )}
          {!income.is_active && !income.archived && (
            <button
              type="button"
              onClick={onResume}
              className="min-h-11 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-900 hover:bg-emerald-100"
            >
              Reanudar
            </button>
          )}
          {!income.archived && (
            <button
              type="button"
              onClick={onArchive}
              className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50"
            >
              Archivar
            </button>
          )}
          {income.archived && (
            <button
              type="button"
              onClick={onRestore}
              className="min-h-11 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-50"
            >
              Restaurar
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
