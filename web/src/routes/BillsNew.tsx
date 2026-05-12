import { zodResolver } from "@hookform/resolvers/zod";
import { AxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  BillForm,
  BillFormFrequency,
  BillFrequency,
  CategoriesResponse,
  Currency,
  RecurringBill,
  RecurringBillList,
  wholeNumberToApi,
} from "../schemas/entities";

const DEFAULT_CATEGORIES = [
  "alimentación",
  "transporte",
  "servicios",
  "salud",
  "ocio",
  "vivienda",
  "ingreso_salario",
  "ingreso_extra",
  "deudas",
  "otros",
];

const CATEGORY_LABELS: Record<string, string> = {
  alimentación: "Alimentación",
  transporte: "Transporte",
  servicios: "Servicios",
  salud: "Salud",
  ocio: "Ocio",
  vivienda: "Vivienda",
  ingreso_salario: "Ingreso salario",
  ingreso_extra: "Ingreso extra",
  deudas: "Deudas",
  otros: "Otros",
};

const FREQUENCY_OPTIONS: Array<{ value: BillFormFrequency; label: string }> = [
  { value: "monthly", label: "Mensual" },
  { value: "biweekly", label: "Quincenal" },
  { value: "annual", label: "Anual" },
  { value: "custom", label: "Personalizada" },
];

const FREQUENCY_LABELS: Record<BillFrequency, string> = {
  weekly: "Semanal",
  biweekly: "Quincenal",
  monthly: "Mensual",
  bimonthly: "Bimestral",
  quarterly: "Trimestral",
  semiannual: "Semestral",
  annual: "Anual",
  custom: "Personalizada",
};

const CURRENCY_OPTIONS: Array<{ value: Currency; label: string }> = [
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function defaultDay() {
  return String(new Date().getDate());
}

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] ?? category.replaceAll("_", " ");
}

function toSupportedFrequency(frequency?: BillFrequency): BillFormFrequency {
  if (
    frequency === "monthly" ||
    frequency === "biweekly" ||
    frequency === "annual" ||
    frequency === "custom"
  ) {
    return frequency;
  }
  return "monthly";
}

function toFormDefaults(bill?: RecurringBill): BillForm {
  return {
    name: bill?.name ?? "",
    provider: bill?.provider ?? "",
    category: bill?.category ?? "servicios",
    currency: bill?.currency ?? "CRC",
    amount_expected:
      bill?.amount_expected === null || bill?.amount_expected === undefined
        ? ""
        : displayMoney(bill.amount_expected, bill.currency),
    is_variable_amount: bill?.is_variable_amount ?? false,
    frequency: toSupportedFrequency(bill?.frequency),
    day_of_month: bill?.day_of_month ? String(bill.day_of_month) : defaultDay(),
    recurrence_rule: bill?.recurrence_rule ?? "",
    start_date: bill?.start_date ?? todayIso(),
    lead_time_days: bill ? String(bill.lead_time_days) : "3",
    notes: bill?.notes ?? "",
  };
}

function optionalText(value: string | undefined) {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function buildPayload(values: BillForm, { patch }: { patch: boolean }) {
  const amount = values.is_variable_amount
    ? null
    : moneyToApiString(values.amount_expected ?? "", values.currency);
  const day = wholeNumberToApi(values.day_of_month);
  const lead = wholeNumberToApi(values.lead_time_days || "0") ?? 0;
  const usesDay = values.frequency !== "custom" && values.frequency !== "biweekly";

  return {
    name: values.name.trim(),
    provider: patch
      ? optionalText(values.provider) ?? null
      : optionalText(values.provider),
    category: values.category,
    amount_expected: values.is_variable_amount ? null : Number(amount),
    currency: values.currency,
    is_variable_amount: values.is_variable_amount,
    frequency: values.frequency,
    day_of_month: usesDay ? day : patch ? null : undefined,
    recurrence_rule:
      values.frequency === "custom"
        ? optionalText(values.recurrence_rule)
        : patch
          ? null
          : undefined,
    start_date: values.start_date,
    lead_time_days: lead,
    notes: patch ? optionalText(values.notes) ?? null : optionalText(values.notes),
  };
}

export default function BillsNew() {
  const [bills, setBills] = useState<RecurringBill[]>([]);
  const [categories, setCategories] = useState<string[]>(DEFAULT_CATEGORIES);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const form = useForm<BillForm>({
    resolver: zodResolver(BillForm),
    defaultValues: toFormDefaults(),
  });
  const editForm = useForm<BillForm>({
    resolver: zodResolver(BillForm),
    defaultValues: toFormDefaults(),
  });

  const currency = form.watch("currency");
  const isVariableAmount = form.watch("is_variable_amount");
  const frequency = form.watch("frequency");
  const editCurrency = editForm.watch("currency");
  const editIsVariableAmount = editForm.watch("is_variable_amount");
  const editFrequency = editForm.watch("frequency");

  const categoryOptions = useMemo(() => {
    const values = new Set([...categories, ...bills.map((bill) => bill.category)]);
    return Array.from(values);
  }, [bills, categories]);

  const sortedBills = useMemo(
    () => [...bills].sort((a, b) => a.start_date.localeCompare(b.start_date)),
    [bills],
  );

  async function loadBills() {
    setLoading(true);
    setLoadError(null);
    try {
      const [billsResp, categoriesResp] = await Promise.all([
        api.get("/recurring-bills", { params: { is_active: true } }),
        api.get("/onboarding/categories"),
      ]);
      setBills(RecurringBillList.parse(billsResp.data));
      const parsedCategories = CategoriesResponse.safeParse(categoriesResp.data);
      if (parsedCategories.success) {
        setCategories(parsedCategories.data.categories);
      }
    } catch {
      setLoadError("No pudimos cargar tus gastos fijos.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadBills();
  }, []);

  async function createBill(values: BillForm) {
    setServerError(null);
    setNotice(null);
    try {
      const resp = await api.post("/recurring-bills", buildPayload(values, { patch: false }));
      const created = RecurringBill.parse(resp.data);
      setBills((current) => [created, ...current]);
      form.reset(toFormDefaults());
      setNotice(`Gasto fijo guardado: ${created.name}.`);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos guardar el gasto fijo. Revisá los datos."),
      );
    }
  }

  function beginEdit(bill: RecurringBill) {
    setEditingId(bill.id);
    editForm.reset(toFormDefaults(bill));
  }

  async function saveEdit(bill: RecurringBill, values: BillForm) {
    setServerError(null);
    setNotice(null);
    try {
      const resp = await api.patch(
        `/recurring-bills/${bill.id}`,
        buildPayload(values, { patch: true }),
      );
      const updated = RecurringBill.parse(resp.data);
      setBills((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEditingId(null);
      setNotice(`Gasto fijo actualizado: ${updated.name}.`);
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos guardar los cambios."));
    }
  }

  async function deleteBill(bill: RecurringBill) {
    setDeletingId(bill.id);
    setServerError(null);
    setNotice(null);
    try {
      await api.delete(`/recurring-bills/${bill.id}`);
      setBills((current) => current.filter((item) => item.id !== bill.id));
      setNotice(`Gasto fijo eliminado: ${bill.name}.`);
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos eliminar el gasto fijo."));
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
          <h1 className="text-2xl font-semibold text-slate-900">Gastos fijos</h1>
          <p className="mt-1 text-slate-600">
            Registrá pagos recurrentes como servicios, alquileres o suscripciones.
          </p>
        </div>
      </header>

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </div>
      )}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Nuevo gasto fijo</h2>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={form.handleSubmit(createBill)}
        >
          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">Nombre</span>
            <input
              {...form.register("name")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="Internet casa"
            />
            {form.formState.errors.name && (
              <span className="text-sm text-red-700">
                {form.formState.errors.name.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Proveedor</span>
            <input
              {...form.register("provider")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="ICE"
            />
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Categoría</span>
            <select
              {...form.register("category")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {categoryOptions.map((category) => (
                <option key={category} value={category}>
                  {categoryLabel(category)}
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

          <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:col-span-2">
            <input
              {...form.register("is_variable_amount")}
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent"
            />
            <span>
              <span className="block text-sm font-medium text-slate-700">
                El monto cambia
              </span>
              <span className="block text-sm text-slate-500">
                Usalo para agua, electricidad u otros pagos que varían.
              </span>
            </span>
          </label>

          {!isVariableAmount && (
            <label className="space-y-1">
              <span className="text-sm font-medium text-slate-700">Monto esperado</span>
              <input
                {...form.register("amount_expected")}
                inputMode="decimal"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                onBlur={() => {
                  const value = form.getValues("amount_expected") ?? "";
                  form.setValue("amount_expected", formatMoneyInput(value, currency), {
                    shouldValidate: true,
                  });
                }}
              />
              {form.formState.errors.amount_expected && (
                <span className="text-sm text-red-700">
                  {form.formState.errors.amount_expected.message}
                </span>
              )}
            </label>
          )}

          {frequency !== "custom" && frequency !== "biweekly" && (
            <label className="space-y-1">
              <span className="text-sm font-medium text-slate-700">Día del mes</span>
              <input
                {...form.register("day_of_month")}
                inputMode="numeric"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              />
              {form.formState.errors.day_of_month && (
                <span className="text-sm text-red-700">
                  {form.formState.errors.day_of_month.message}
                </span>
              )}
            </label>
          )}

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Próxima fecha</span>
            <input
              {...form.register("start_date")}
              type="date"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.start_date && (
              <span className="text-sm text-red-700">
                {form.formState.errors.start_date.message}
              </span>
            )}
          </label>

          {frequency === "custom" && (
            <label className="space-y-1 sm:col-span-2">
              <span className="text-sm font-medium text-slate-700">Regla RRULE</span>
              <input
                {...form.register("recurrence_rule")}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                placeholder="FREQ=MONTHLY;INTERVAL=2"
              />
              {form.formState.errors.recurrence_rule && (
                <span className="text-sm text-red-700">
                  {form.formState.errors.recurrence_rule.message}
                </span>
              )}
            </label>
          )}

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Avisarme días antes</span>
            <input
              {...form.register("lead_time_days")}
              inputMode="numeric"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.lead_time_days && (
              <span className="text-sm text-red-700">
                {form.formState.errors.lead_time_days.message}
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
              disabled={form.formState.isSubmitting}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {form.formState.isSubmitting ? "Guardando..." : "Guardar gasto fijo"}
            </button>
          </div>
        </form>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">
          Gastos fijos registrados
        </h2>
        {loading && <p className="text-slate-500">Buscando tus gastos fijos...</p>}
        {loadError && <p className="text-red-700">{loadError}</p>}
        {!loading && !loadError && sortedBills.length === 0 && (
          <p className="text-slate-500">Todavía no tenés gastos fijos registrados.</p>
        )}

        <div className="space-y-3">
          {sortedBills.map((bill) => (
            <article
              key={bill.id}
              className="rounded-lg border border-slate-200 bg-white p-4"
            >
              {editingId === bill.id ? (
                <form
                  className="grid gap-3 sm:grid-cols-2"
                  onSubmit={editForm.handleSubmit((values) =>
                    saveEdit(bill, values),
                  )}
                >
                  <input
                    {...editForm.register("name")}
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                  />
                  <input
                    {...editForm.register("provider")}
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                    placeholder="Proveedor"
                  />
                  <select
                    {...editForm.register("category")}
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                  >
                    {categoryOptions.map((category) => (
                      <option key={category} value={category}>
                        {categoryLabel(category)}
                      </option>
                    ))}
                  </select>
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
                  <label className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-700">
                    <input
                      {...editForm.register("is_variable_amount")}
                      type="checkbox"
                      className="h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent"
                    />
                    Monto variable
                  </label>
                  {!editIsVariableAmount && (
                    <input
                      {...editForm.register("amount_expected")}
                      inputMode="decimal"
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                      onBlur={() => {
                        const value = editForm.getValues("amount_expected") ?? "";
                        editForm.setValue(
                          "amount_expected",
                          formatMoneyInput(value, editCurrency),
                          { shouldValidate: true },
                        );
                      }}
                    />
                  )}
                  {editFrequency !== "custom" && editFrequency !== "biweekly" && (
                    <input
                      {...editForm.register("day_of_month")}
                      inputMode="numeric"
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                    />
                  )}
                  <input
                    {...editForm.register("start_date")}
                    type="date"
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                  />
                  {editFrequency === "custom" && (
                    <input
                      {...editForm.register("recurrence_rule")}
                      className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                    />
                  )}
                  <input
                    {...editForm.register("lead_time_days")}
                    inputMode="numeric"
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
                    <h3 className="font-semibold text-slate-900">{bill.name}</h3>
                    <p className="text-sm text-slate-500">
                      {bill.provider ? `${bill.provider} · ` : ""}
                      {categoryLabel(bill.category)} ·{" "}
                      {bill.is_variable_amount
                        ? "Monto variable"
                        : `${displayMoney(bill.amount_expected, bill.currency)} ${bill.currency}`}{" "}
                      · {FREQUENCY_LABELS[bill.frequency]}
                    </p>
                    <p className="text-sm text-slate-500">
                      Próxima fecha base: {bill.start_date}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => beginEdit(bill)}
                      className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      Editar
                    </button>
                    <button
                      type="button"
                      disabled={deletingId === bill.id}
                      onClick={() => {
                        if (window.confirm(`¿Eliminar ${bill.name}?`)) {
                          void deleteBill(bill);
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
          ))}
        </div>
      </section>
    </div>
  );
}
