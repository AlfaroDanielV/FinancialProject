import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveDebt,
  DebtUpdatePayload,
  fetchDebt,
  fetchDebtSchedule,
  fetchPayoffScenarios,
  pauseDebt,
  restoreDebt,
  resumeDebt,
  updateDebt,
} from "../api/debts";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  earlyPayoffExtraMonthly,
  earlyPayoffLumpSum,
  PayoffScenarioOutput,
} from "../lib/amortization";
import { Debt } from "../schemas/entities";

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

type TabKey = "amortization" | "calculator" | "scenarios";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "amortization", label: "Amortización" },
  { key: "calculator", label: "Cancelación anticipada" },
  { key: "scenarios", label: "Escenarios Ley 7472" },
];

function nextPaymentDateLabel(paymentDueDay: number) {
  const today = new Date();
  const candidate = new Date(today.getFullYear(), today.getMonth(), paymentDueDay);
  if (candidate < today) {
    candidate.setMonth(candidate.getMonth() + 1);
  }
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(candidate);
}

export default function DebtDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<TabKey>("amortization");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const debtQuery = useQuery({
    queryKey: ["debt", id],
    queryFn: () => fetchDebt(id),
    enabled: Boolean(id),
  });

  const debt = debtQuery.data;

  const updateMutation = useMutation({
    mutationFn: (payload: DebtUpdatePayload) => updateDebt(id, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["debt", id], data);
      queryClient.invalidateQueries({ queryKey: ["debts"] });
      setEditing(false);
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos guardar.")),
  });
  const pauseMutation = useMutation({
    mutationFn: () => pauseDebt(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["debt", id] });
      queryClient.invalidateQueries({ queryKey: ["debts"] });
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos pausar.")),
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeDebt(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["debt", id] });
      queryClient.invalidateQueries({ queryKey: ["debts"] });
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos reanudar.")),
  });
  const archiveMutation = useMutation({
    mutationFn: () => archiveDebt(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["debts"] });
      navigate("/debts");
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos archivar.")),
  });
  const restoreMutation = useMutation({
    mutationFn: () => restoreDebt(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["debt", id] });
      queryClient.invalidateQueries({ queryKey: ["debts"] });
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos restaurar.")),
  });

  if (!id) {
    return <p className="text-slate-700">Deuda inválida.</p>;
  }
  if (debtQuery.isLoading) {
    return <p className="text-slate-500">Cargando deuda...</p>;
  }
  if (debtQuery.isError || !debt) {
    return (
      <div className="space-y-3">
        <Link to="/debts" className="text-sm font-medium text-accent">
          Volver a deudas
        </Link>
        <p className="text-red-700">No pudimos encontrar esta deuda.</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Link to="/debts" className="text-sm font-medium text-accent hover:text-accent-dark">
        Volver a deudas
      </Link>

      {error && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      {debt.archived && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div>
            <p className="font-medium">Deuda archivada.</p>
            <p className="mt-1">
              Los pagos históricos se conservan pero la deuda no aparece en
              cálculos ni en el resumen.
            </p>
          </div>
          <button
            type="button"
            onClick={() => restoreMutation.mutate()}
            disabled={restoreMutation.isPending}
            className="min-h-11 rounded-md border border-amber-400 bg-white px-3 py-2 text-sm font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60"
          >
            {restoreMutation.isPending ? "Restaurando..." : "Restaurar"}
          </button>
        </div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {editing && !debt.archived ? (
          <DebtEditForm
            debt={debt}
            onCancel={() => setEditing(false)}
            onSubmit={(values) => updateMutation.mutate(values)}
            submitting={updateMutation.isPending}
          />
        ) : (
          <DebtHeader debt={debt} />
        )}

        {!editing && (
          <div className="mt-4 flex flex-wrap gap-2">
            {!debt.archived && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
              >
                Editar
              </button>
            )}
            {debt.is_active && !debt.archived && (
              <button
                type="button"
                onClick={() => pauseMutation.mutate()}
                disabled={pauseMutation.isPending}
                className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
              >
                Pausar
              </button>
            )}
            {!debt.is_active && !debt.archived && (
              <button
                type="button"
                onClick={() => resumeMutation.mutate()}
                disabled={resumeMutation.isPending}
                className="min-h-11 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900 hover:bg-emerald-100 disabled:opacity-60"
              >
                Reanudar
              </button>
            )}
            {!debt.archived && (
              <button
                type="button"
                onClick={() => {
                  if (
                    window.confirm(`Archivar ${debt.name}? Se sacará del resumen.`)
                  ) {
                    archiveMutation.mutate();
                  }
                }}
                disabled={archiveMutation.isPending}
                className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
              >
                Archivar
              </button>
            )}
          </div>
        )}
      </section>

      <nav className="flex gap-2 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`min-h-11 shrink-0 rounded-md border px-3 text-sm font-medium ${
              tab === t.key
                ? "border-accent bg-accent text-white"
                : "border-slate-200 bg-white text-slate-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "amortization" && <AmortizationTab debtId={debt.id} />}
      {tab === "calculator" && <CalculatorTab debt={debt} />}
      {tab === "scenarios" && <ScenariosTab debt={debt} />}
    </div>
  );
}

function DebtHeader({ debt }: { debt: Debt }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">{debt.name}</h1>
        <p className="mt-1 text-sm text-slate-500">
          {debt.lender || "Acreedor desconocido"} · {debt.currency} ·{" "}
          {(debt.interest_rate * 100).toFixed(2)}% anual
        </p>
        {!debt.is_active && !debt.archived && (
          <span className="mt-2 inline-block rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            Pausada
          </span>
        )}
      </div>
      <div className="grid w-full max-w-sm grid-cols-2 gap-3 text-right">
        <div>
          <p className="text-xs text-slate-500">Saldo actual</p>
          <p className="text-lg font-semibold text-slate-950">
            {displayMoney(debt.current_balance, debt.currency)}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Cuota mensual</p>
          <p className="text-lg font-semibold text-slate-700">
            {displayMoney(debt.minimum_payment, debt.currency)}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Principal original</p>
          <p className="text-sm text-slate-700">
            {displayMoney(debt.original_amount, debt.currency)}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Próximo pago</p>
          <p className="text-sm text-slate-700">
            {nextPaymentDateLabel(debt.payment_due_day)}
          </p>
        </div>
      </div>
    </div>
  );
}

function DebtEditForm({
  debt,
  onCancel,
  onSubmit,
  submitting,
}: {
  debt: Debt;
  onCancel: () => void;
  onSubmit: (values: DebtUpdatePayload) => void;
  submitting: boolean;
}) {
  const [name, setName] = useState(debt.name);
  const [paymentDay, setPaymentDay] = useState(String(debt.payment_due_day));
  const [notes, setNotes] = useState(debt.notes ?? "");

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        const day = Number(paymentDay);
        if (!Number.isInteger(day) || day < 1 || day > 31) return;
        onSubmit({
          name: name.trim(),
          payment_due_day: day,
          notes: notes.trim() || undefined,
        });
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
        <span className="font-medium text-slate-700">Día de pago</span>
        <input
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          value={paymentDay}
          onChange={(event) => setPaymentDay(event.target.value)}
          inputMode="numeric"
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
      <div className="space-y-1 text-sm sm:col-span-2">
        <p className="text-xs text-slate-500">
          Los datos financieros (monto, tasa, cuota, plazo, seguro) son
          inmutables. Para refinanciar, registrá una deuda nueva.
        </p>
      </div>
      <div className="flex gap-2 sm:col-span-2">
        <button
          type="submit"
          disabled={submitting}
          className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
        >
          {submitting ? "Guardando..." : "Guardar"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          Cancelar
        </button>
      </div>
    </form>
  );
}

function AmortizationTab({ debtId }: { debtId: string }) {
  const scheduleQuery = useQuery({
    queryKey: ["debt-schedule", debtId],
    queryFn: () => fetchDebtSchedule(debtId),
  });
  const [year, setYear] = useState<number | "all">(new Date().getFullYear());

  if (scheduleQuery.isLoading) {
    return <p className="text-sm text-slate-500">Calculando amortización...</p>;
  }
  if (scheduleQuery.isError || !scheduleQuery.data) {
    return (
      <p className="text-sm text-red-700">
        No pudimos calcular la amortización (¿la cuota cubre los intereses?).
      </p>
    );
  }
  const schedule = scheduleQuery.data.schedule;
  const years = Array.from(
    new Set(schedule.map((row) => Number(row.payment_date.slice(0, 4)))),
  ).sort();
  const filtered =
    year === "all"
      ? schedule
      : schedule.filter((row) => row.payment_date.startsWith(String(year)));

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-900">
          Tabla de amortización
        </h2>
        <select
          value={String(year)}
          onChange={(event) =>
            setYear(event.target.value === "all" ? "all" : Number(event.target.value))
          }
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="all">Todos los años</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="px-2 py-2">#</th>
              <th className="px-2 py-2">Fecha</th>
              <th className="px-2 py-2 text-right">Cuota</th>
              <th className="px-2 py-2 text-right">Principal</th>
              <th className="px-2 py-2 text-right">Interés</th>
              <th className="px-2 py-2 text-right">Saldo</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.map((row) => (
              <tr key={row.month_number}>
                <td className="px-2 py-1">{row.month_number}</td>
                <td className="px-2 py-1 whitespace-nowrap text-slate-500">
                  {row.payment_date}
                </td>
                <td className="px-2 py-1 text-right">
                  {displayMoney(row.payment_amount, "CRC")}
                </td>
                <td className="px-2 py-1 text-right text-emerald-700">
                  {displayMoney(row.principal_portion, "CRC")}
                </td>
                <td className="px-2 py-1 text-right text-red-700">
                  {displayMoney(row.interest_portion, "CRC")}
                </td>
                <td className="px-2 py-1 text-right">
                  {displayMoney(row.remaining_balance, "CRC")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function buildScenarioInput(debt: Debt) {
  return {
    balance: debt.current_balance,
    annualRate: debt.interest_rate,
    monthlyPayment: debt.minimum_payment,
    includesInsurance: debt.includes_insurance,
    insuranceMonthly: debt.insurance_monthly ?? 0,
    paymentsMade: debt.payments_made,
    prepaymentPenaltyPct: debt.prepayment_penalty_pct,
  };
}

function ScenarioOutputCard({
  title,
  result,
  currency,
}: {
  title: string;
  result: PayoffScenarioOutput;
  currency: string;
}) {
  return (
    <article className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <p className="font-semibold text-slate-900">{title}</p>
      <ul className="mt-2 space-y-1 text-slate-700">
        <li>
          Meses ahorrados: <strong>{result.monthsSaved}</strong>
        </li>
        <li>
          Intereses ahorrados:{" "}
          <strong>{displayMoney(result.interestSaved, currency)}</strong>
        </li>
        <li>
          Nuevo plazo: <strong>{result.newTotalMonths} meses</strong>
        </li>
        {result.prepaymentPenaltyApplies && (
          <li className="text-amber-800">
            Penalidad Ley 7472:{" "}
            <strong>
              {displayMoney(result.prepaymentPenaltyAmount, currency)}
            </strong>
          </li>
        )}
        {!result.prepaymentPenaltyApplies && (
          <li className="text-xs text-slate-500">
            Sin penalidad (Ley 7472, &gt; 2 cuotas pagadas).
          </li>
        )}
        <li className="border-t border-slate-200 pt-1 text-slate-800">
          Ahorro neto: <strong>{displayMoney(result.totalSaved, currency)}</strong>
        </li>
      </ul>
    </article>
  );
}

function CalculatorTab({ debt }: { debt: Debt }) {
  const [mode, setMode] = useState<"lump_sum" | "extra_monthly">("lump_sum");
  const [lumpSum, setLumpSum] = useState("100000");
  const [extraMonthly, setExtraMonthly] = useState("5000");

  const result = useMemo<PayoffScenarioOutput | null>(() => {
    const input = buildScenarioInput(debt);
    if (mode === "lump_sum") {
      const amount = Number(moneyToApiString(lumpSum, debt.currency) ?? "0");
      if (amount <= 0) return null;
      return earlyPayoffLumpSum(input, amount);
    }
    const amount = Number(moneyToApiString(extraMonthly, debt.currency) ?? "0");
    if (amount <= 0) return null;
    return earlyPayoffExtraMonthly(input, amount);
  }, [debt, mode, lumpSum, extraMonthly]);

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-slate-900">
        Calculadora de cancelación anticipada
      </h2>
      <p className="text-xs text-slate-500">
        Cálculo en el navegador. La validación cruzada vs el servidor está
        cubierta por las pruebas de paridad.
      </p>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setMode("lump_sum")}
          className={`min-h-11 rounded-md border px-3 text-sm font-medium ${
            mode === "lump_sum"
              ? "border-accent bg-accent text-white"
              : "border-slate-200 bg-white text-slate-700"
          }`}
        >
          Monto único
        </button>
        <button
          type="button"
          onClick={() => setMode("extra_monthly")}
          className={`min-h-11 rounded-md border px-3 text-sm font-medium ${
            mode === "extra_monthly"
              ? "border-accent bg-accent text-white"
              : "border-slate-200 bg-white text-slate-700"
          }`}
        >
          Cuota extra
        </button>
      </div>

      {mode === "lump_sum" ? (
        <label className="block space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            Monto único ({debt.currency})
          </span>
          <input
            value={lumpSum}
            onChange={(event) => setLumpSum(event.target.value)}
            onBlur={() => setLumpSum(formatMoneyInput(lumpSum, debt.currency))}
            inputMode="decimal"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
      ) : (
        <label className="block space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            Extra mensual ({debt.currency})
          </span>
          <input
            value={extraMonthly}
            onChange={(event) => setExtraMonthly(event.target.value)}
            onBlur={() =>
              setExtraMonthly(formatMoneyInput(extraMonthly, debt.currency))
            }
            inputMode="decimal"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
      )}

      {result ? (
        <ScenarioOutputCard
          title={mode === "lump_sum" ? "Pago único" : "Cuota extra"}
          result={result}
          currency={debt.currency}
        />
      ) : (
        <p className="text-sm text-slate-500">Poné un monto mayor a cero.</p>
      )}
    </section>
  );
}

function ScenariosTab({ debt }: { debt: Debt }) {
  const [lumpSum, setLumpSum] = useState("");
  const [extraMonthly, setExtraMonthly] = useState("");

  const lumpAmount = lumpSum
    ? Number(moneyToApiString(lumpSum, debt.currency) ?? "0")
    : 0;
  const extraAmount = extraMonthly
    ? Number(moneyToApiString(extraMonthly, debt.currency) ?? "0")
    : 0;

  const scenariosQuery = useQuery({
    queryKey: ["debt-payoff-scenarios", debt.id, lumpAmount, extraAmount],
    queryFn: () =>
      fetchPayoffScenarios(debt.id, {
        lump_sum: lumpAmount > 0 ? lumpAmount : undefined,
        extra_monthly: extraAmount > 0 ? extraAmount : undefined,
      }),
    enabled: lumpAmount > 0 || extraAmount > 0,
  });

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-slate-900">
        Escenarios Ley 7472
      </h2>
      <p className="text-xs text-slate-500">
        Calculado en el servidor con la misma fórmula que la calculadora.
        Pasados los 2 primeros pagos, la comisión por cancelación anticipada
        es 0%.
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            Monto único ({debt.currency})
          </span>
          <input
            value={lumpSum}
            onChange={(event) => setLumpSum(event.target.value)}
            onBlur={() => lumpSum && setLumpSum(formatMoneyInput(lumpSum, debt.currency))}
            inputMode="decimal"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            placeholder="0"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">
            Extra mensual ({debt.currency})
          </span>
          <input
            value={extraMonthly}
            onChange={(event) => setExtraMonthly(event.target.value)}
            onBlur={() =>
              extraMonthly &&
              setExtraMonthly(formatMoneyInput(extraMonthly, debt.currency))
            }
            inputMode="decimal"
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            placeholder="0"
          />
        </label>
      </div>

      {scenariosQuery.isLoading && (
        <p className="text-sm text-slate-500">Calculando...</p>
      )}
      {scenariosQuery.isError && (
        <p className="text-sm text-red-700">No pudimos calcular el escenario.</p>
      )}
      {scenariosQuery.data && (
        <div className="grid gap-2 sm:grid-cols-2">
          {scenariosQuery.data.lump_sum && (
            <ScenarioOutputCard
              title="Pago único"
              result={{
                monthsSaved: scenariosQuery.data.lump_sum.months_saved,
                interestSaved: scenariosQuery.data.lump_sum.interest_saved,
                totalSaved: scenariosQuery.data.lump_sum.total_saved,
                newTotalMonths: scenariosQuery.data.lump_sum.new_total_months,
                prepaymentPenaltyApplies:
                  scenariosQuery.data.lump_sum.prepayment_penalty_applies,
                prepaymentPenaltyAmount:
                  scenariosQuery.data.lump_sum.prepayment_penalty_amount,
              }}
              currency={debt.currency}
            />
          )}
          {scenariosQuery.data.extra_monthly && (
            <ScenarioOutputCard
              title="Cuota extra"
              result={{
                monthsSaved: scenariosQuery.data.extra_monthly.months_saved,
                interestSaved:
                  scenariosQuery.data.extra_monthly.interest_saved,
                totalSaved: scenariosQuery.data.extra_monthly.total_saved,
                newTotalMonths:
                  scenariosQuery.data.extra_monthly.new_total_months,
                prepaymentPenaltyApplies:
                  scenariosQuery.data.extra_monthly.prepayment_penalty_applies,
                prepaymentPenaltyAmount:
                  scenariosQuery.data.extra_monthly.prepayment_penalty_amount,
              }}
              currency={debt.currency}
            />
          )}
        </div>
      )}

      {lumpAmount === 0 && extraAmount === 0 && (
        <p className="text-sm text-slate-500">
          Poné un monto único, una cuota extra, o ambos.
        </p>
      )}
    </section>
  );
}
