import { zodResolver } from "@hookform/resolvers/zod";
import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { computeMonthlyPaymentPreview } from "../lib/amortization";
import {
  displayMoney,
  formatMoneyInput,
  moneyToApiString,
  parseMoneyInput,
} from "../lib/money";
import {
  AmortizationSchedule,
  Currency,
  Debt,
  DebtForm,
  DebtType,
  RateType,
  percentToApiDecimal,
  wholeNumberToApi,
} from "../schemas/entities";

const DEBT_TYPE_OPTIONS: Array<{ value: DebtType; label: string }> = [
  { value: "mortgage", label: "Hipoteca" },
  { value: "personal_loan", label: "Préstamo personal" },
  { value: "credit_card_balance", label: "Saldo de tarjeta" },
  { value: "auto_loan", label: "Préstamo de carro" },
  { value: "student_loan", label: "Préstamo estudiantil" },
  { value: "other", label: "Otra deuda" },
];

const RATE_TYPE_OPTIONS: Array<{ value: RateType; label: string }> = [
  { value: "fixed", label: "Fija" },
  { value: "variable", label: "Variable" },
];

const CURRENCY_OPTIONS: Array<{ value: Currency; label: string }> = [
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

const SCHEDULE_PAGE_SIZE = 12;

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function toDefaults(): DebtForm {
  return {
    name: "",
    debt_type: "mortgage",
    lender: "",
    currency: "CRC",
    original_amount: "",
    current_balance: "",
    interest_rate: "",
    minimum_payment: "",
    payment_due_day: "15",
    term_months: "",
    start_date: todayIso(),
    rate_type: "fixed",
    rate_reference: "",
    rate_spread: "",
    prepayment_penalty_pct: "0",
    payments_made: "0",
    includes_insurance: false,
    insurance_monthly: "0",
    notes: "",
  };
}

function optionalText(value: string | undefined) {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function moneyToNumber(value: string, currency: Currency) {
  const parsed = moneyToApiString(value, currency);
  return parsed === null ? null : Number(parsed);
}

export default function DebtsNew() {
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedDebt, setSavedDebt] = useState<Debt | null>(null);
  const [schedule, setSchedule] = useState<AmortizationSchedule | null>(null);
  const [schedulePage, setSchedulePage] = useState(0);

  const form = useForm<DebtForm>({
    resolver: zodResolver(DebtForm),
    defaultValues: toDefaults(),
  });

  const currency = form.watch("currency");
  const currentBalance = form.watch("current_balance");
  const originalAmount = form.watch("original_amount");
  const interestRate = form.watch("interest_rate");
  const minimumPayment = form.watch("minimum_payment");
  const termMonths = form.watch("term_months");
  const rateType = form.watch("rate_type");
  const includesInsurance = form.watch("includes_insurance");
  const insuranceMonthly = form.watch("insurance_monthly");
  const prepaymentPenaltyPct = form.watch("prepayment_penalty_pct");
  const paymentsMadeRaw = form.watch("payments_made");

  const previewPayment = useMemo(() => {
    const balance = parseMoneyInput(currentBalance, currency);
    const annualRate = percentToApiDecimal(interestRate);
    const term = wholeNumberToApi(termMonths);
    const insurance = includesInsurance
      ? parseMoneyInput(insuranceMonthly || "0", currency)
      : 0;

    if (
      balance === null ||
      balance <= 0 ||
      annualRate === null ||
      term === null ||
      term <= 0 ||
      insurance === null
    ) {
      return null;
    }

    return computeMonthlyPaymentPreview({
      balance,
      annualRate,
      termMonths: term,
      includesInsurance,
      insuranceMonthly: insurance,
    });
  }, [currency, currentBalance, includesInsurance, insuranceMonthly, interestRate, termMonths]);

  const leyWarning = useMemo(() => {
    const paymentsMade = wholeNumberToApi(paymentsMadeRaw || "0");
    const penalty = percentToApiDecimal(prepaymentPenaltyPct || "0");
    return paymentsMade !== null && paymentsMade >= 2 && penalty !== null && penalty > 0;
  }, [paymentsMadeRaw, prepaymentPenaltyPct]);

  function applyPreviewPayment() {
    if (previewPayment === null) return;
    form.setValue(
      "minimum_payment",
      formatMoneyInput(String(previewPayment), currency),
      { shouldValidate: true },
    );
  }

  async function createDebt(values: DebtForm) {
    setServerError(null);
    setSavedDebt(null);
    setSchedule(null);

    const original = moneyToNumber(values.original_amount, values.currency);
    const current = moneyToNumber(values.current_balance, values.currency);
    const minimum = moneyToNumber(values.minimum_payment, values.currency);
    const insurance = values.includes_insurance
      ? moneyToNumber(values.insurance_monthly || "0", values.currency)
      : 0;
    const interest = percentToApiDecimal(values.interest_rate);
    const rateSpread = percentToApiDecimal(values.rate_spread || "0");
    const prepaymentPenalty = percentToApiDecimal(
      values.prepayment_penalty_pct || "0",
    );
    const paymentDay = wholeNumberToApi(values.payment_due_day);
    const term = wholeNumberToApi(values.term_months);
    const paymentsMade = wholeNumberToApi(values.payments_made || "0");

    if (
      original === null ||
      current === null ||
      minimum === null ||
      insurance === null ||
      interest === null ||
      prepaymentPenalty === null ||
      paymentDay === null ||
      term === null ||
      paymentsMade === null
    ) {
      return;
    }

    try {
      const createResp = await api.post("/debts", {
        name: values.name.trim(),
        debt_type: values.debt_type,
        lender: optionalText(values.lender),
        original_amount: original,
        current_balance: current,
        interest_rate: interest,
        minimum_payment: minimum,
        payment_due_day: paymentDay,
        term_months: term,
        start_date: values.start_date || undefined,
        currency: values.currency,
        rate_type: values.rate_type,
        rate_reference:
          values.rate_type === "variable"
            ? optionalText(values.rate_reference)
            : undefined,
        rate_spread:
          values.rate_type === "variable" && rateSpread !== null
            ? rateSpread
            : undefined,
        prepayment_penalty_pct: prepaymentPenalty,
        payments_made: paymentsMade,
        includes_insurance: values.includes_insurance,
        insurance_monthly: values.includes_insurance ? insurance : undefined,
        notes: optionalText(values.notes),
      });
      const debt = Debt.parse(createResp.data);
      const scheduleResp = await api.get(`/debts/${debt.id}/schedule`);
      setSavedDebt(debt);
      setSchedule(AmortizationSchedule.parse(scheduleResp.data));
      setSchedulePage(0);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos guardar la deuda. Revisá los datos."),
      );
    }
  }

  const pageCount = schedule
    ? Math.max(1, Math.ceil(schedule.schedule.length / SCHEDULE_PAGE_SIZE))
    : 1;
  const pageStart = schedulePage * SCHEDULE_PAGE_SIZE;
  const visibleRows = schedule?.schedule.slice(
    pageStart,
    pageStart + SCHEDULE_PAGE_SIZE,
  ) ?? [];
  const pageEnd = pageStart + visibleRows.length;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Deudas</h1>
          <p className="mt-1 text-slate-600">
            Registrá préstamos, hipotecas y saldos de tarjeta con su cuota.
          </p>
        </div>
      </header>

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </div>
      )}

      {savedDebt && (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          Deuda guardada: {savedDebt.name}.
        </div>
      )}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Nueva deuda</h2>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={form.handleSubmit(createDebt)}
        >
          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">Nombre</span>
            <input
              {...form.register("name")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="Hipoteca casa"
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
              {...form.register("debt_type")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {DEBT_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Entidad</span>
            <input
              {...form.register("lender")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="BAC"
            />
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

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Monto original</span>
            <input
              {...form.register("original_amount")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              onBlur={() => {
                form.setValue(
                  "original_amount",
                  formatMoneyInput(originalAmount, currency),
                  { shouldValidate: true },
                );
              }}
            />
            {form.formState.errors.original_amount && (
              <span className="text-sm text-red-700">
                {form.formState.errors.original_amount.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Saldo actual</span>
            <input
              {...form.register("current_balance")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              onBlur={() => {
                form.setValue(
                  "current_balance",
                  formatMoneyInput(currentBalance, currency),
                  { shouldValidate: true },
                );
              }}
            />
            {form.formState.errors.current_balance && (
              <span className="text-sm text-red-700">
                {form.formState.errors.current_balance.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Tasa anual %</span>
            <input
              {...form.register("interest_rate")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="8.5"
            />
            {form.formState.errors.interest_rate && (
              <span className="text-sm text-red-700">
                {form.formState.errors.interest_rate.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Plazo meses</span>
            <input
              {...form.register("term_months")}
              inputMode="numeric"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="240"
            />
            {form.formState.errors.term_months && (
              <span className="text-sm text-red-700">
                {form.formState.errors.term_months.message}
              </span>
            )}
          </label>

          <div className="rounded-lg border border-slate-200 bg-white p-4 sm:col-span-2">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="font-semibold text-slate-900">Cuota calculada</h3>
                <p className="text-sm text-slate-500">
                  {previewPayment === null
                    ? "Completá saldo, tasa y plazo para calcularla."
                    : `${displayMoney(previewPayment, currency)} ${currency}`}
                </p>
              </div>
              <button
                type="button"
                disabled={previewPayment === null}
                onClick={applyPreviewPayment}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                Usar esta cuota
              </button>
            </div>
          </div>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Cuota mensual</span>
            <input
              {...form.register("minimum_payment")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              onBlur={() => {
                form.setValue(
                  "minimum_payment",
                  formatMoneyInput(minimumPayment, currency),
                  { shouldValidate: true },
                );
              }}
            />
            {form.formState.errors.minimum_payment && (
              <span className="text-sm text-red-700">
                {form.formState.errors.minimum_payment.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Día de pago</span>
            <input
              {...form.register("payment_due_day")}
              inputMode="numeric"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.payment_due_day && (
              <span className="text-sm text-red-700">
                {form.formState.errors.payment_due_day.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Fecha de inicio</span>
            <input
              {...form.register("start_date")}
              type="date"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">Tasa</span>
            <select
              {...form.register("rate_type")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {RATE_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          {rateType === "variable" && (
            <>
              <label className="space-y-1">
                <span className="text-sm font-medium text-slate-700">
                  Referencia
                </span>
                <input
                  {...form.register("rate_reference")}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                  placeholder="TBP"
                />
                {form.formState.errors.rate_reference && (
                  <span className="text-sm text-red-700">
                    {form.formState.errors.rate_reference.message}
                  </span>
                )}
              </label>

              <label className="space-y-1">
                <span className="text-sm font-medium text-slate-700">Spread %</span>
                <input
                  {...form.register("rate_spread")}
                  inputMode="decimal"
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                  placeholder="5"
                />
              </label>
            </>
          )}

          <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:col-span-2">
            <input
              {...form.register("includes_insurance")}
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent"
            />
            <span>
              <span className="block text-sm font-medium text-slate-700">
                La cuota incluye seguro
              </span>
              <span className="block text-sm text-slate-500">
                El seguro se separa del abono a principal e intereses.
              </span>
            </span>
          </label>

          {includesInsurance && (
            <label className="space-y-1 sm:col-span-2">
              <span className="text-sm font-medium text-slate-700">
                Seguro mensual
              </span>
              <input
                {...form.register("insurance_monthly")}
                inputMode="decimal"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
                onBlur={() => {
                  form.setValue(
                    "insurance_monthly",
                    formatMoneyInput(insuranceMonthly || "0", currency),
                    { shouldValidate: true },
                  );
                }}
              />
              {form.formState.errors.insurance_monthly && (
                <span className="text-sm text-red-700">
                  {form.formState.errors.insurance_monthly.message}
                </span>
              )}
            </label>
          )}

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">
              Comisión prepago %
            </span>
            <input
              {...form.register("prepayment_penalty_pct")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.prepayment_penalty_pct && (
              <span className="text-sm text-red-700">
                {form.formState.errors.prepayment_penalty_pct.message}
              </span>
            )}
          </label>

          <label className="space-y-1">
            <span className="text-sm font-medium text-slate-700">
              Cuotas pagadas
            </span>
            <input
              {...form.register("payments_made")}
              inputMode="numeric"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            />
            {form.formState.errors.payments_made && (
              <span className="text-sm text-red-700">
                {form.formState.errors.payments_made.message}
              </span>
            )}
          </label>

          {leyWarning && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 sm:col-span-2">
              Ley 7472: después de dos cuotas pagadas, la comisión por prepago
              no debería aplicar. La guardamos como referencia, no como bloqueo.
            </div>
          )}

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
              {form.formState.isSubmitting ? "Guardando..." : "Guardar deuda"}
            </button>
          </div>
        </form>
      </section>

      {schedule && savedDebt && (
        <section className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              Amortización de {savedDebt.name}
            </h2>
            <p className="text-sm text-slate-500">
              Cuotas {pageStart + 1}-{pageEnd} de {schedule.total_months},
              calculadas por el servidor.
            </p>
          </div>

          {schedule.variable_rate_notice && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              {schedule.variable_rate_notice}
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-sm text-slate-500">Cuota</p>
              <p className="mt-1 font-semibold text-slate-900">
                {displayMoney(schedule.monthly_payment, savedDebt.currency)}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-sm text-slate-500">Interés total</p>
              <p className="mt-1 font-semibold text-slate-900">
                {displayMoney(schedule.total_interest, savedDebt.currency)}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-sm text-slate-500">Fecha final</p>
              <p className="mt-1 font-semibold text-slate-900">
                {schedule.payoff_date ?? "Sin fecha"}
              </p>
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-slate-600">
                <tr>
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">Fecha</th>
                  <th className="px-3 py-2 font-medium">Cuota</th>
                  <th className="px-3 py-2 font-medium">Principal</th>
                  <th className="px-3 py-2 font-medium">Interés</th>
                  <th className="px-3 py-2 font-medium">Saldo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleRows.map((row) => (
                  <tr key={row.month_number}>
                    <td className="px-3 py-2">{row.month_number}</td>
                    <td className="px-3 py-2">{row.payment_date}</td>
                    <td className="px-3 py-2">
                      {displayMoney(row.payment_amount, savedDebt.currency)}
                    </td>
                    <td className="px-3 py-2">
                      {displayMoney(row.principal_portion, savedDebt.currency)}
                    </td>
                    <td className="px-3 py-2">
                      {displayMoney(row.interest_portion, savedDebt.currency)}
                    </td>
                    <td className="px-3 py-2">
                      {displayMoney(row.remaining_balance, savedDebt.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-500">
              Página {schedulePage + 1} de {pageCount}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={schedulePage === 0}
                onClick={() => setSchedulePage((page) => Math.max(0, page - 1))}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                Anterior
              </button>
              <button
                type="button"
                disabled={schedulePage >= pageCount - 1}
                onClick={() =>
                  setSchedulePage((page) => Math.min(pageCount - 1, page + 1))
                }
                className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                Siguiente
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
