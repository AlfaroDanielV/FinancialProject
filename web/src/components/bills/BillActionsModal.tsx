import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  archiveBill,
  BillOccurrence,
  markBillPaid,
  pauseBill,
  resumeBill,
} from "../../api/bills";
import { displayMoney, formatMoneyInput } from "../../lib/money";
import { Account, RecurringBill, UserCategoryItem } from "../../schemas/entities";

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      return String((detail as { message: string }).message);
    }
  }
  return fallback;
}

function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

interface Props {
  bill: RecurringBill;
  occurrence?: BillOccurrence | null;
  accounts: Account[];
  categories: UserCategoryItem[];
  onClose: () => void;
}

export function BillActionsModal({
  bill,
  occurrence,
  accounts,
  categories,
  onClose,
}: Props) {
  const queryClient = useQueryClient();

  const defaultAmount = useMemo(() => {
    const value =
      occurrence?.amount_expected ?? bill.amount_expected ?? 0;
    return formatMoneyInput(String(value), bill.currency);
  }, [bill.amount_expected, bill.currency, occurrence]);

  const [amount, setAmount] = useState(defaultAmount);
  const [accountId, setAccountId] = useState<string>(bill.account_id ?? "");
  const [categoryId, setCategoryId] = useState<string>("");
  const [description, setDescription] = useState("");
  const [paidAt, setPaidAt] = useState(
    occurrence?.due_date ?? new Date().toISOString().slice(0, 10),
  );
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [idempotencyKey] = useState<string>(newIdempotencyKey);

  const markPaidMutation = useMutation({
    mutationFn: () =>
      markBillPaid(bill.id, {
        amount_paid: Number(
          amount.replace(/[^\d.,-]/g, "").replace(/\./g, "").replace(",", "."),
        ),
        paid_at: paidAt,
        account_id: accountId || undefined,
        category_id: categoryId || undefined,
        description: description.trim() || undefined,
        idempotency_key: idempotencyKey,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      queryClient.invalidateQueries({ queryKey: ["upcoming-feed"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setSuccess(
        data.idempotent_replay
          ? "Ya estaba marcado (mismo intento)."
          : data.warning
            ? data.warning
            : "Marcada como pagada.",
      );
      setError(null);
    },
    onError: (err) => {
      setError(apiErrorMessage(err, "No pudimos marcar el pago."));
    },
  });

  const pauseMutation = useMutation({
    mutationFn: () => pauseBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["upcoming-feed"] });
      onClose();
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos pausar.")),
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      queryClient.invalidateQueries({ queryKey: ["upcoming-feed"] });
      onClose();
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos reanudar.")),
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      queryClient.invalidateQueries({ queryKey: ["upcoming-feed"] });
      onClose();
    },
    onError: (err) => setError(apiErrorMessage(err, "No pudimos archivar.")),
  });

  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center">
      <div className="w-full max-w-md space-y-4 rounded-lg bg-white p-5 shadow-lg">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{bill.name}</h2>
            <p className="text-sm text-slate-500">
              {bill.provider || "Sin proveedor"} · {bill.frequency} ·{" "}
              {bill.amount_expected !== null
                ? displayMoney(bill.amount_expected, bill.currency)
                : "Monto variable"}
            </p>
            {occurrence && (
              <p className="mt-1 text-xs text-slate-500">
                Próximo vencimiento: {occurrence.due_date}
              </p>
            )}
          </div>
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
        {success && (
          <p className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
            {success}
          </p>
        )}

        {bill.is_active && (
          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-700">
              Marcar pagado
            </h3>

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">
                Monto ({bill.currency})
              </span>
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-2"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                onBlur={() => setAmount(formatMoneyInput(amount, bill.currency))}
                inputMode="decimal"
              />
            </label>

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">Cuenta</span>
              <select
                value={accountId}
                onChange={(event) => setAccountId(event.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="">Sin cuenta</option>
                {accounts
                  .filter((acc) => !acc.archived)
                  .map((acc) => (
                    <option key={acc.id} value={acc.id}>
                      {acc.name} ({acc.currency})
                    </option>
                  ))}
              </select>
            </label>

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">Categoría</span>
              <select
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="">Sin categoría</option>
                {categories
                  .filter((cat) => !cat.archived)
                  .map((cat) => (
                    <option key={cat.id} value={cat.id}>
                      {cat.name}
                    </option>
                  ))}
              </select>
            </label>

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">Fecha de pago</span>
              <input
                type="date"
                className="w-full rounded-md border border-slate-300 px-3 py-2"
                value={paidAt}
                onChange={(event) => setPaidAt(event.target.value)}
              />
            </label>

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">Notas</span>
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-2"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>

            <button
              type="button"
              onClick={() => markPaidMutation.mutate()}
              disabled={markPaidMutation.isPending}
              className="min-h-11 w-full rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {markPaidMutation.isPending ? "Guardando..." : "Marcar pagado"}
            </button>
          </section>
        )}

        <section className="grid gap-2 border-t border-slate-200 pt-4 sm:grid-cols-3">
          {bill.is_active ? (
            <button
              type="button"
              onClick={() => pauseMutation.mutate()}
              disabled={pauseMutation.isPending}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
            >
              Pausar
            </button>
          ) : (
            <button
              type="button"
              onClick={() => resumeMutation.mutate()}
              disabled={resumeMutation.isPending}
              className="min-h-11 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900 hover:bg-emerald-100 disabled:opacity-60"
            >
              Reanudar
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  `Archivar ${bill.name}? Las próximas se cancelan.`,
                )
              ) {
                archiveMutation.mutate();
              }
            }}
            disabled={archiveMutation.isPending}
            className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
          >
            Archivar
          </button>
          <a
            href={`/bills/new?id=${bill.id}`}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-center text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Editar
          </a>
        </section>
      </div>
    </div>
  );
}
