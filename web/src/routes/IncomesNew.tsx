import { zodResolver } from "@hookform/resolvers/zod";
import { AxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  Currency,
  DerivedIncomeTypes,
  IncomeForm,
  IncomeFrequency,
  IncomeType,
  RecurringIncome,
  RecurringIncomeList,
} from "../schemas/entities";

const INCOME_TYPE_OPTIONS: Array<{ value: IncomeType; label: string }> = [
  { value: "salary", label: "Salario" },
  { value: "aguinaldo", label: "Aguinaldo" },
  { value: "salario_escolar", label: "Salario escolar" },
  { value: "freelance", label: "Freelance" },
  { value: "other", label: "Otro" },
];

const FREQUENCY_OPTIONS: Array<{ value: IncomeFrequency; label: string }> = [
  { value: "weekly", label: "Semanal" },
  { value: "biweekly", label: "Quincenal" },
  { value: "monthly", label: "Mensual" },
  { value: "annual", label: "Anual" },
];

const CURRENCY_OPTIONS: Array<{ value: Currency; label: string }> = [
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function nextCycleDate(type: IncomeType) {
  const now = new Date();
  const year = now.getFullYear();
  if (type === "aguinaldo") return `${year}-12-15`;
  if (type === "salario_escolar") return `${year + 1}-01-15`;
  return todayIso();
}

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function incomeTypeLabel(type: IncomeType) {
  return INCOME_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type;
}

function frequencyLabel(frequency: IncomeFrequency) {
  return FREQUENCY_OPTIONS.find((option) => option.value === frequency)?.label ?? frequency;
}

function toFormDefaults(income?: RecurringIncome): IncomeForm {
  return {
    name: income?.name ?? "",
    income_type: income?.income_type ?? "salary",
    currency: income?.currency ?? "CRC",
    amount: income?.amount ? displayMoney(income.amount, income.currency) : "",
    frequency: income?.frequency ?? "monthly",
    next_payment_date: income?.next_payment_date ?? todayIso(),
    base_salary_link_id: income?.base_salary_link_id ?? "",
    notes: income?.notes ?? "",
  };
}

export default function IncomesNew() {
  const navigate = useNavigate();
  const [incomes, setIncomes] = useState<RecurringIncome[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const form = useForm<IncomeForm>({
    resolver: zodResolver(IncomeForm),
    defaultValues: toFormDefaults(),
  });
  const editForm = useForm<IncomeForm>({
    resolver: zodResolver(IncomeForm),
    defaultValues: toFormDefaults(),
  });

  const incomeType = form.watch("income_type");
  const currency = form.watch("currency");
  const selectedSalaryId = form.watch("base_salary_link_id");
  const editCurrency = editForm.watch("currency");

  const salaries = useMemo(
    () => incomes.filter((income) => income.income_type === "salary"),
    [incomes],
  );
  const selectedSalary = salaries.find((salary) => salary.id === selectedSalaryId);
  const isDerived = DerivedIncomeTypes.has(incomeType);

  const sortedIncomes = useMemo(
    () => [...incomes].sort((a, b) => a.next_payment_date.localeCompare(b.next_payment_date)),
    [incomes],
  );

  async function loadIncomes() {
    setLoading(true);
    setLoadError(null);
    try {
      const resp = await api.get("/recurring-incomes");
      const parsed = RecurringIncomeList.parse(resp.data);
      setIncomes(parsed);
    } catch {
      setLoadError("No pudimos cargar tus ingresos.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadIncomes();
  }, []);

  useEffect(() => {
    if (!isDerived) return;
    form.setValue("frequency", "annual", { shouldValidate: true });
    form.setValue("next_payment_date", nextCycleDate(incomeType), {
      shouldValidate: true,
    });
    if (selectedSalary) {
      form.setValue("currency", selectedSalary.currency, { shouldValidate: true });
    }
  }, [form, incomeType, isDerived, selectedSalary]);

  async function createIncome(values: IncomeForm) {
    setServerError(null);
    const derived = DerivedIncomeTypes.has(values.income_type);
    const amount = derived
      ? null
      : moneyToApiString(values.amount ?? "", values.currency);
    if (!derived && amount === null) return;

    const salary = salaries.find((item) => item.id === values.base_salary_link_id);
    const payload = {
      name: values.name.trim(),
      income_type: values.income_type,
      currency: derived ? salary?.currency ?? values.currency : values.currency,
      amount: derived ? undefined : amount,
      frequency: values.frequency,
      next_payment_date: values.next_payment_date,
      base_salary_link_id: derived ? values.base_salary_link_id : undefined,
      notes: values.notes?.trim() || undefined,
    };

    try {
      await api.post("/recurring-incomes", payload);
      navigate(`/?created=income&name=${encodeURIComponent(values.name.trim())}`);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos guardar el ingreso. Revisá los datos."),
      );
    }
  }

  function beginEdit(income: RecurringIncome) {
    setEditingId(income.id);
    editForm.reset(toFormDefaults(income));
  }

  async function saveEdit(income: RecurringIncome, values: IncomeForm) {
    setServerError(null);
    const derived = DerivedIncomeTypes.has(income.income_type);
    const amount = derived
      ? undefined
      : moneyToApiString(values.amount ?? "", values.currency);
    if (!derived && amount === null) return;

    try {
      const resp = await api.patch(`/recurring-incomes/${income.id}`, {
        name: values.name.trim(),
        amount,
        currency: values.currency,
        frequency: values.frequency,
        next_payment_date: values.next_payment_date,
        notes: values.notes?.trim() || null,
      });
      const updated = RecurringIncome.parse(resp.data);
      setIncomes((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEditingId(null);
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos guardar los cambios."));
    }
  }

  async function deleteIncome(income: RecurringIncome) {
    setDeletingId(income.id);
    setServerError(null);
    try {
      await api.delete(`/recurring-incomes/${income.id}`);
      setIncomes((current) => current.filter((item) => item.id !== income.id));
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos eliminar el ingreso."));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            Ingresos recurrentes
          </h1>
          <p className="mt-1 text-slate-600">
            Registrá lo que entra con frecuencia para que el bot tenga una base real.
          </p>
        </div>
      </header>

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </div>
      )}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Nuevo ingreso</h2>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={form.handleSubmit(createIncome)}
        >
          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">Nombre</span>
            <input
              {...form.register("name")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="Salario empresa"
            />
            {form.formState.errors.name && (
              <span className="text-sm text-red-700">
                {form.formState.errors.name.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Tipo</span>
            <select
              {...form.register("income_type")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {INCOME_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Frecuencia</span>
            <select
              {...form.register("frequency")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {FREQUENCY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          {isDerived ? (
            <div className="space-y-1 sm:col-span-2">
              <label className="space-y-1">
                <span className="text-sm font-medium text-slate-700">
                  Salario base
                </span>
                <select
                  {...form.register("base_salary_link_id")}
                  disabled={salaries.length === 0}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent disabled:bg-slate-100"
                >
                  <option value="">Escogé un salario</option>
                  {salaries.map((salary) => (
                    <option key={salary.id} value={salary.id}>
                      {salary.name} · {displayMoney(salary.amount, salary.currency)}{" "}
                      {salary.currency}
                    </option>
                  ))}
                </select>
              </label>
              {form.formState.errors.base_salary_link_id && (
                <span className="text-sm text-red-700">
                  {form.formState.errors.base_salary_link_id.message}
                </span>
              )}
              {salaries.length === 0 && (
                <p className="text-sm text-slate-600">
                  Primero registrá tu salario base para poder calcular este ciclo.
                </p>
              )}
              {incomeType === "aguinaldo" && (
                <p className="text-sm text-slate-600">
                  El aguinaldo es el mes 13 y se paga en diciembre; el backend lo
                  deriva del salario que escojás.
                </p>
              )}
              {incomeType === "salario_escolar" && (
                <p className="text-sm text-slate-600">
                  El salario escolar se paga en enero; el backend lo deriva con la
                  fórmula CR registrada.
                </p>
              )}
            </div>
          ) : (
            <>
              <label className="space-y-1">
                <span className="text-sm font-medium text-slate-700">Moneda</span>
                <select
                  {...form.register("currency")}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                >
                  {CURRENCY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-sm font-medium text-slate-700">Monto</span>
                <input
                  {...form.register("amount")}
                  inputMode="decimal"
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                  onBlur={() => {
                    const value = form.getValues("amount") ?? "";
                    form.setValue("amount", formatMoneyInput(value, currency), {
                      shouldValidate: true,
                    });
                  }}
                />
                {form.formState.errors.amount && (
                  <span className="text-sm text-red-700">
                    {form.formState.errors.amount.message}
                  </span>
                )}
              </label>
            </>
          )}

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Próximo pago</span>
            <input
              {...form.register("next_payment_date")}
              type="date"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.next_payment_date && (
              <span className="text-sm text-red-700">
                {form.formState.errors.next_payment_date.message}
              </span>
            )}
          </label>

          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">Notas</span>
            <textarea
              {...form.register("notes")}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
          </label>

          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={form.formState.isSubmitting || (isDerived && salaries.length === 0)}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {form.formState.isSubmitting ? "Guardando..." : "Guardar ingreso"}
            </button>
          </div>
        </form>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">
          Ingresos registrados
        </h2>
        {loading && <p className="text-slate-500">Cargando ingresos...</p>}
        {loadError && <p className="text-red-700">{loadError}</p>}
        {!loading && !loadError && sortedIncomes.length === 0 && (
          <p className="text-slate-500">Todavía no tenés ingresos registrados.</p>
        )}

        <div className="space-y-3">
          {sortedIncomes.map((income) => {
            const derived = DerivedIncomeTypes.has(income.income_type);
            return (
              <article
                key={income.id}
                className="rounded-lg border border-slate-200 bg-white p-4"
              >
                {editingId === income.id ? (
                  <form
                    className="grid gap-3 sm:grid-cols-2"
                    onSubmit={editForm.handleSubmit((values) =>
                      saveEdit(income, values),
                    )}
                  >
                    <input
                      {...editForm.register("name")}
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                    />
                    {!derived && (
                      <>
                        <select
                          {...editForm.register("currency")}
                          className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                        >
                          {CURRENCY_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <input
                          {...editForm.register("amount")}
                          inputMode="decimal"
                          className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                          onBlur={() => {
                            const value = editForm.getValues("amount") ?? "";
                            editForm.setValue(
                              "amount",
                              formatMoneyInput(value, editCurrency),
                              { shouldValidate: true },
                            );
                          }}
                        />
                      </>
                    )}
                    <select
                      {...editForm.register("frequency")}
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                    >
                      {FREQUENCY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <input
                      {...editForm.register("next_payment_date")}
                      type="date"
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                    />
                    <textarea
                      {...editForm.register("notes")}
                      rows={2}
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                    />
                    <div className="flex gap-2 sm:col-span-2">
                      <button
                        type="submit"
                        className="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent-dark"
                      >
                        Guardar
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        Cancelar
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <h3 className="font-semibold text-slate-900">{income.name}</h3>
                      <p className="text-sm text-slate-500">
                        {incomeTypeLabel(income.income_type)} ·{" "}
                        {displayMoney(income.amount, income.currency)}{" "}
                        {income.currency} · {frequencyLabel(income.frequency)}
                      </p>
                      <p className="text-sm text-slate-500">
                        Próximo pago: {income.next_payment_date}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => beginEdit(income)}
                        className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        disabled={deletingId === income.id}
                        onClick={() => {
                          if (window.confirm(`¿Eliminar ${income.name}?`)) {
                            void deleteIncome(income);
                          }
                        }}
                        className="rounded-md border border-red-200 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-slate-400"
                      >
                        Eliminar
                      </button>
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
