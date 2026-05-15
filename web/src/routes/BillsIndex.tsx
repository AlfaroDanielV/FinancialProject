import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { DayPicker } from "react-day-picker";
import "react-day-picker/dist/style.css";

import { fetchAccounts, fetchUserCategories } from "../api/accounts";
import {
  BillOccurrence,
  fetchBillOccurrences,
  fetchRecurringBills,
} from "../api/bills";
import { BillActionsModal } from "../components/bills/BillActionsModal";
import { displayMoney } from "../lib/money";
import { RecurringBill } from "../schemas/entities";

const URGENCY = (dueIso: string) => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueIso + "T00:00:00");
  const days = Math.ceil((due.getTime() - today.getTime()) / 86_400_000);
  if (days < 0) return "border-red-300 bg-red-50 text-red-900";
  if (days < 3) return "border-red-200 bg-red-50 text-red-800";
  if (days < 7) return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-slate-200 bg-white text-slate-700";
};

function categoryColor(category: string | null | undefined) {
  switch ((category || "").toLowerCase()) {
    case "vivienda":
      return "#7c3aed";
    case "servicios":
      return "#0ea5e9";
    case "salud":
      return "#dc2626";
    case "alimentación":
      return "#16a34a";
    case "transporte":
      return "#2563eb";
    case "deudas":
      return "#ea580c";
    default:
      return "#64748b";
  }
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function addDaysIso(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function dueDateOnly(value: string) {
  return value.split("T")[0];
}

type ViewMode = "calendar" | "list";

export default function BillsIndex() {
  const [view, setView] = useState<ViewMode>("calendar");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [activeBill, setActiveBill] = useState<{
    bill: RecurringBill;
    occurrence?: BillOccurrence | null;
  } | null>(null);

  const billsQuery = useQuery({
    queryKey: ["recurring-bills", "all"],
    queryFn: () => fetchRecurringBills(false),
  });

  const occurrencesQuery = useQuery({
    queryKey: ["bill-occurrences", "next-60d"],
    queryFn: () =>
      fetchBillOccurrences({
        from_date: todayIso(),
        to_date: addDaysIso(60),
        status: "pending",
      }),
  });

  const accountsQuery = useQuery({
    queryKey: ["accounts", false],
    queryFn: () => fetchAccounts(false),
  });
  const categoriesQuery = useQuery({
    queryKey: ["user-categories"],
    queryFn: fetchUserCategories,
  });

  const bills = billsQuery.data ?? [];
  const occurrences = occurrencesQuery.data ?? [];
  const accounts = accountsQuery.data ?? [];
  const categories = categoriesQuery.data ?? [];

  const billsById = useMemo(() => {
    const out: Record<string, RecurringBill> = {};
    for (const bill of bills) out[bill.id] = bill;
    return out;
  }, [bills]);

  const occurrencesByDate = useMemo(() => {
    const out: Record<string, BillOccurrence[]> = {};
    for (const occ of occurrences) {
      const day = dueDateOnly(occ.due_date);
      if (!out[day]) out[day] = [];
      out[day].push(occ);
    }
    return out;
  }, [occurrences]);

  const sortedOccurrences = useMemo(
    () =>
      [...occurrences].sort((a, b) =>
        a.due_date.localeCompare(b.due_date),
      ),
    [occurrences],
  );

  const dayHasOccurrences = (day: Date) => {
    const iso = day.toISOString().slice(0, 10);
    return Boolean(occurrencesByDate[iso]?.length);
  };

  const occurrencesForSelectedDay = selectedDate
    ? occurrencesByDate[selectedDate] ?? []
    : [];

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
            <h1 className="text-2xl font-semibold text-slate-900">Gastos fijos</h1>
            <p className="mt-1 text-slate-600">
              Calendario y lista de tus pagos recurrentes.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/bills/new"
              className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
            >
              Nuevo gasto fijo
            </Link>
          </div>
        </div>
      </header>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setView("calendar")}
          className={`min-h-11 rounded-md border px-3 text-sm font-medium ${
            view === "calendar"
              ? "border-accent bg-accent text-white"
              : "border-slate-200 bg-white text-slate-700"
          }`}
        >
          Calendario
        </button>
        <button
          type="button"
          onClick={() => setView("list")}
          className={`min-h-11 rounded-md border px-3 text-sm font-medium ${
            view === "list"
              ? "border-accent bg-accent text-white"
              : "border-slate-200 bg-white text-slate-700"
          }`}
        >
          Lista
        </button>
      </div>

      {(billsQuery.isLoading || occurrencesQuery.isLoading) && (
        <p className="text-sm text-slate-500">Cargando...</p>
      )}

      {view === "calendar" && (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <DayPicker
            mode="single"
            selected={selectedDate ? new Date(selectedDate + "T00:00:00") : undefined}
            onSelect={(day) =>
              setSelectedDate(day ? day.toISOString().slice(0, 10) : null)
            }
            modifiers={{ hasBills: dayHasOccurrences }}
            modifiersClassNames={{
              hasBills: "rdp-day-with-bills",
            }}
            locale={undefined}
            footer={
              selectedDate ? (
                <SelectedDayPanel
                  date={selectedDate}
                  occurrences={occurrencesForSelectedDay}
                  billsById={billsById}
                  onPick={(occ) =>
                    setActiveBill({
                      bill: billsById[occ.recurring_bill_id],
                      occurrence: occ,
                    })
                  }
                />
              ) : (
                <p className="mt-3 text-xs text-slate-500">
                  Hacé click en un día con punto para ver los pagos.
                </p>
              )
            }
          />
          <style>{`
            .rdp-day-with-bills {
              position: relative;
            }
            .rdp-day-with-bills::after {
              content: "";
              position: absolute;
              bottom: 4px;
              left: 50%;
              transform: translateX(-50%);
              width: 6px;
              height: 6px;
              border-radius: 9999px;
              background-color: #2563eb;
            }
          `}</style>
        </section>
      )}

      {view === "list" && (
        <section className="space-y-2">
          {sortedOccurrences.length === 0 && (
            <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
              <p>No hay pagos pendientes en los próximos 60 días.</p>
            </div>
          )}
          {sortedOccurrences.map((occ) => {
            const bill = billsById[occ.recurring_bill_id];
            if (!bill) return null;
            return (
              <button
                key={occ.id}
                type="button"
                onClick={() => setActiveBill({ bill, occurrence: occ })}
                className={`flex w-full items-start justify-between gap-3 rounded-md border px-3 py-2 text-left ${URGENCY(
                  occ.due_date,
                )}`}
              >
                <div>
                  <p className="font-semibold">{bill.name}</p>
                  <p className="text-xs opacity-80">
                    {bill.provider || bill.category || ""}
                  </p>
                </div>
                <div className="text-right text-sm">
                  <p className="font-medium">{occ.due_date}</p>
                  <p>
                    {occ.amount_expected !== null
                      ? displayMoney(occ.amount_expected, bill.currency)
                      : "Variable"}
                  </p>
                </div>
              </button>
            );
          })}
        </section>
      )}

      {activeBill && activeBill.bill && (
        <BillActionsModal
          bill={activeBill.bill}
          occurrence={activeBill.occurrence ?? null}
          accounts={accounts}
          categories={categories}
          onClose={() => setActiveBill(null)}
        />
      )}
    </div>
  );
}

function SelectedDayPanel({
  date,
  occurrences,
  billsById,
  onPick,
}: {
  date: string;
  occurrences: BillOccurrence[];
  billsById: Record<string, RecurringBill>;
  onPick: (occ: BillOccurrence) => void;
}) {
  if (!occurrences.length) {
    return (
      <p className="mt-3 text-xs text-slate-500">
        Sin pagos para {date}.
      </p>
    );
  }
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {date}
      </p>
      {occurrences.map((occ) => {
        const bill = billsById[occ.recurring_bill_id];
        const color = categoryColor(bill?.category);
        return (
          <button
            key={occ.id}
            type="button"
            onClick={() => onPick(occ)}
            className="flex w-full items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm hover:bg-slate-50"
          >
            <span className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: color }}
              />
              <span className="font-medium text-slate-900">
                {bill?.name ?? "Pago"}
              </span>
            </span>
            <span className="text-slate-700">
              {occ.amount_expected !== null && bill
                ? displayMoney(occ.amount_expected, bill.currency)
                : "Variable"}
            </span>
          </button>
        );
      })}
    </div>
  );
}
