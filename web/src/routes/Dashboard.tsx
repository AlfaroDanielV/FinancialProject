import { Suspense, lazy, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  addDaysIso,
  currentMonthIso,
  fetchDailyCashFlow,
  fetchDashboardInsights,
  fetchDashboardSummary,
  fetchDebtOverview,
  fetchGoals,
  fetchMe,
  fetchOnboardingStatus,
  fetchUpcomingFeed,
  todayIso,
} from "../api/dashboard";
import { useAuth } from "../lib/auth";
import { displayMoney } from "../lib/money";
import {
  DashboardPeriod,
  DashboardSummary,
  Goal,
  UpcomingDebtPayment,
  UpcomingFeedItem,
} from "../schemas/dashboard";

const DashboardCharts = lazy(() => import("../components/dashboard/DashboardCharts"));

const PERIODS: Array<{ value: DashboardPeriod; label: string }> = [
  { value: "month_current", label: "Mes actual" },
  { value: "month_prev", label: "Mes anterior" },
  { value: "ytd", label: "Año" },
  { value: "last_6_months", label: "6 meses" },
];

interface UpcomingItem {
  id: string;
  kind: "bill" | "event" | "debt";
  title: string;
  date: string;
  amount: number | null;
  currency: string;
  meta: string;
}

function monthStartIso() {
  return `${currentMonthIso()}-01`;
}

function trendText(current?: DashboardSummary, previous?: DashboardSummary) {
  if (!current || !previous || Math.abs(previous.net_flow) < 1) {
    return "Sin base anterior";
  }
  const change = ((current.net_flow - previous.net_flow) / Math.abs(previous.net_flow)) * 100;
  const direction = change >= 0 ? "+" : "";
  return `${direction}${change.toFixed(1)}% vs mes anterior`;
}

function savingsLabel(rate: number | null) {
  if (rate === null) return "Sin ingresos";
  return `${(rate * 100).toFixed(1)}%`;
}

function progressFor(goal: Goal) {
  if (goal.target_amount <= 0) return 0;
  return Math.min(100, Math.round((goal.current_amount / goal.target_amount) * 100));
}

function parseDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatShortDate(value: string) {
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "short",
  }).format(parseDate(value));
}

function urgencyClass(value: string) {
  const today = parseDate(todayIso());
  const target = parseDate(value);
  const days = Math.ceil((target.getTime() - today.getTime()) / 86_400_000);
  if (days < 3) return "border-red-200 bg-red-50 text-red-800";
  if (days < 7) return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-slate-200 bg-slate-50 text-slate-700";
}

function nextDebtDate(paymentDay: number) {
  const now = new Date();
  const candidate = new Date(
    now.getFullYear(),
    now.getMonth(),
    Math.min(paymentDay, new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()),
  );
  const today = parseDate(todayIso());
  if (candidate >= today) return candidate.toISOString().slice(0, 10);
  const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  const lastDay = new Date(nextMonth.getFullYear(), nextMonth.getMonth() + 1, 0).getDate();
  nextMonth.setDate(Math.min(paymentDay, lastDay));
  return nextMonth.toISOString().slice(0, 10);
}

function billToUpcoming(item: UpcomingFeedItem): UpcomingItem {
  return {
    id: item.id,
    kind: item.item_type,
    title: item.title,
    date: item.date,
    amount: item.amount,
    currency: item.currency,
    meta: item.provider || item.category || item.status || "Pago programado",
  };
}

function debtToUpcoming(item: UpcomingDebtPayment): UpcomingItem {
  return {
    id: item.debt_id,
    kind: "debt",
    title: item.name,
    date: nextDebtDate(item.payment_due_day),
    amount: item.minimum_payment,
    currency: "CRC",
    meta: "Deuda",
  };
}

function SkeletonBlock({ label, className = "" }: { label: string; className?: string }) {
  return (
    <div className={`animate-pulse rounded-lg border border-slate-200 bg-white p-4 ${className}`}>
      <div className="mb-3 h-3 w-32 rounded bg-slate-200" />
      <div className="h-8 w-44 rounded bg-slate-200" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

function ErrorBox({ children }: { children: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      {children}
    </div>
  );
}

function Panel({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export default function Dashboard() {
  const auth = useAuth();
  const [period, setPeriod] = useState<DashboardPeriod>("month_current");
  const [searchParams, setSearchParams] = useSearchParams();

  const created = searchParams.get("created");
  const createdName = searchParams.get("name");

  const me = useQuery({ queryKey: ["me"], queryFn: fetchMe });
  const onboarding = useQuery({
    queryKey: ["onboarding-status"],
    queryFn: fetchOnboardingStatus,
  });
  const summary = useQuery({
    queryKey: ["dashboard-summary", period],
    queryFn: () => fetchDashboardSummary(period),
  });
  const previousSummary = useQuery({
    queryKey: ["dashboard-summary", "month_prev"],
    queryFn: () => fetchDashboardSummary("month_prev"),
  });
  const dailyFlow = useQuery({
    queryKey: ["daily-cash-flow", monthStartIso(), todayIso()],
    queryFn: () => fetchDailyCashFlow(monthStartIso(), todayIso()),
  });
  const upcomingFeed = useQuery({
    queryKey: ["upcoming-feed", todayIso(), addDaysIso(7)],
    queryFn: () => fetchUpcomingFeed(todayIso(), addDaysIso(7)),
  });
  const debtOverview = useQuery({
    queryKey: ["debt-overview"],
    queryFn: fetchDebtOverview,
  });
  const goals = useQuery({ queryKey: ["goals", "active"], queryFn: fetchGoals });
  const insights = useQuery({
    queryKey: ["dashboard-insights"],
    queryFn: fetchDashboardInsights,
  });

  const upcomingItems = useMemo(() => {
    const items = [
      ...(upcomingFeed.data?.items.map(billToUpcoming) ?? []),
      ...(debtOverview.data?.upcoming_payments.map(debtToUpcoming) ?? []),
    ];
    return items
      .filter((item) => item.date <= addDaysIso(7))
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(0, 5);
  }, [upcomingFeed.data, debtOverview.data]);

  const topGoals = useMemo(
    () =>
      [...(goals.data ?? [])]
        .sort((a, b) => progressFor(b) - progressFor(a))
        .slice(0, 3),
    [goals.data],
  );

  const summaryData = summary.data;
  const currency = summaryData?.display_currency ?? me.data?.display_currency ?? "CRC";
  const createdLabel =
    created === "account"
      ? "Cuenta guardada"
      : created === "income"
        ? "Ingreso guardado"
        : created === "bill"
          ? "Gasto fijo guardado"
          : created === "debt"
            ? "Deuda guardada"
            : null;

  if (auth.kind === "unauthenticated") {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold text-slate-900">No estás autenticado</h1>
        <p className="text-slate-600">
          Volvé a Telegram y mandá <code className="rounded bg-slate-100 px-1">/setup</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-8">
      {createdLabel && (
        <div className="flex items-start justify-between gap-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          <span>
            {createdLabel}
            {createdName ? `: ${createdName}` : ""}.
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

      <section className="space-y-4">
        <div>
          <p className="text-sm font-medium text-accent">Centro Financiero</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">
            {me.isLoading ? "Buscando tus datos..." : `Hola, ${me.data?.full_name || "Daniel"}`}
          </h1>
        </div>

        <div className="grid gap-3 md:grid-cols-[1.2fr_2fr]">
          {summary.isLoading ? (
            <SkeletonBlock label="Calculando balance total" />
          ) : summary.isError || !summaryData ? (
            <ErrorBox>No pudimos calcular tu resumen. Refrescá la página.</ErrorBox>
          ) : (
            <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-sm text-slate-500">Balance total</p>
              <p className="mt-1 text-3xl font-semibold text-slate-950">
                {displayMoney(summaryData.balance_total, currency)}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                {trendText(summaryData, previousSummary.data)}
              </p>
            </article>
          )}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {summary.isLoading || !summaryData ? (
              <>
                <SkeletonBlock label="Buscando ingresos" />
                <SkeletonBlock label="Buscando gastos" />
                <SkeletonBlock label="Calculando flujo" />
                <SkeletonBlock label="Calculando ahorro" />
              </>
            ) : (
              <>
                <Metric label="Ingresos" value={displayMoney(summaryData.income_total, currency)} />
                <Metric label="Gastos" value={displayMoney(summaryData.expense_total, currency)} />
                <Metric label="Flujo neto" value={displayMoney(summaryData.net_flow, currency)} />
                <Metric label="Ahorro" value={savingsLabel(summaryData.savings_rate)} />
              </>
            )}
          </div>
        </div>
      </section>

      <section className="flex gap-2 overflow-x-auto pb-1">
        {PERIODS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setPeriod(item.value)}
            className={`min-h-11 shrink-0 rounded-md border px-3 text-sm font-medium ${
              period === item.value
                ? "border-accent bg-accent text-white"
                : "border-slate-200 bg-white text-slate-700"
            }`}
          >
            {item.label}
          </button>
        ))}
      </section>

      {onboarding.data && onboarding.data.completeness_score < 1 && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="font-medium text-amber-950">Te faltan datos para cerrar el mapa.</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            {!onboarding.data.has_accounts && <SetupLink to="/accounts/new" label="Cuenta" />}
            {!onboarding.data.has_incomes && <SetupLink to="/incomes/new" label="Ingreso" />}
            {!onboarding.data.has_debts && <SetupLink to="/debts/new" label="Deuda" />}
            {!onboarding.data.has_recurring_bills && <SetupLink to="/bills/new" label="Gasto fijo" />}
          </div>
        </section>
      )}

      {summaryData && summaryData.accounts_count > 0 && summaryData.transaction_count === 0 && (
        <section className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-blue-950">
          Tenés cuenta registrada. Cuando el bot empiece a guardar movimientos, este tablero se llena solo.
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Panel title="Este mes">
          {dailyFlow.isLoading ? (
            <SkeletonBlock label="Calculando flujo diario" className="h-40" />
          ) : dailyFlow.isError || !dailyFlow.data ? (
            <ErrorBox>No pudimos cargar el flujo diario.</ErrorBox>
          ) : (
            <Suspense fallback={<SkeletonBlock label="Dibujando flujo diario" className="h-40" />}>
              <DashboardCharts
                daily={dailyFlow.data.points}
                categories={[]}
                currency={currency}
                showCategories={false}
              />
            </Suspense>
          )}
        </Panel>

        <Panel title="Próximos pagos">
          {upcomingFeed.isLoading || debtOverview.isLoading ? (
            <SkeletonBlock label="Buscando próximos pagos" />
          ) : upcomingItems.length === 0 ? (
            <p className="text-sm text-slate-500">No hay pagos en los próximos 7 días.</p>
          ) : (
            <div className="space-y-2">
              {upcomingItems.map((item) => (
                <article
                  key={`${item.kind}-${item.id}`}
                  className={`rounded-md border px-3 py-2 ${urgencyClass(item.date)}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">{item.title}</p>
                      <p className="text-xs opacity-80">{item.meta}</p>
                    </div>
                    <div className="text-right text-sm">
                      <p>{formatShortDate(item.date)}</p>
                      <p>{item.amount === null ? "Variable" : displayMoney(item.amount, item.currency)}</p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Categorías">
        {summary.isLoading ? (
          <SkeletonBlock label="Calculando categorías" className="h-64" />
        ) : summary.isError || !summaryData ? (
          <ErrorBox>No pudimos cargar el detalle por categoría.</ErrorBox>
        ) : (
          <Suspense fallback={<SkeletonBlock label="Dibujando categorías" className="h-64" />}>
            <DashboardCharts
              daily={[]}
              categories={summaryData.category_breakdown}
              currency={currency}
              showDaily={false}
            />
          </Suspense>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Tus metas" action={<Link className="text-sm font-medium text-accent" to="/goals">Ver</Link>}>
          {goals.isLoading ? (
            <SkeletonBlock label="Buscando metas" />
          ) : topGoals.length === 0 ? (
            <p className="text-sm text-slate-500">Aún no tenés metas activas.</p>
          ) : (
            <div className="space-y-3">
              {topGoals.map((goal) => (
                <article key={goal.id} className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-medium text-slate-900">{goal.name}</p>
                    <p className="text-sm text-slate-500">{progressFor(goal)}%</p>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${progressFor(goal)}%` }}
                    />
                  </div>
                  <p className="text-sm text-slate-500">
                    {displayMoney(goal.current_amount, goal.target_currency)} de{" "}
                    {displayMoney(goal.target_amount, goal.target_currency)}
                  </p>
                </article>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Te conozco" action={<Link className="text-sm font-medium text-accent" to="/memoria">Ver</Link>}>
          {insights.isLoading ? (
            <SkeletonBlock label="Buscando memoria" />
          ) : insights.data?.items.length ? (
            <div className="space-y-3">
              {insights.data.items.map((item) => (
                <article key={item.id} className="border-l-2 border-accent pl-3">
                  <p className="text-sm text-slate-800">{item.description}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    Confianza {(item.confidence * 100).toFixed(0)}%
                  </p>
                </article>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">Todavía no tengo suficiente evidencia.</p>
          )}
        </Panel>
      </div>

      <nav className="grid grid-cols-2 gap-2 border-t border-slate-200 pt-4 text-sm font-medium sm:grid-cols-5">
        <QuickLink to="/accounts" label="Cuentas" />
        <QuickLink to="/transactions" label="Movimientos" />
        <QuickLink to="/debts" label="Deudas" />
        <QuickLink to="/bills" label="Gastos" />
        <QuickLink to="/goals" label="Metas" />
      </nav>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 break-words text-lg font-semibold text-slate-950">{value}</p>
    </article>
  );
}

function SetupLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="flex min-h-11 items-center justify-center rounded-md border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950"
    >
      {label}
    </Link>
  );
}

function QuickLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="flex min-h-11 items-center justify-center rounded-md border border-slate-200 bg-white px-3 text-slate-700"
    >
      {label}
    </Link>
  );
}
