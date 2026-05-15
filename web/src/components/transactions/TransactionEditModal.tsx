import { AxiosError } from "axios";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import {
  TransactionUpdatePayload,
  updateTransaction,
} from "../../api/transactions";
import { formatMoneyInput } from "../../lib/money";
import { Currency, Transaction, UserCategoryItem } from "../../schemas/entities";

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function TransactionEditModal({
  transaction,
  categories,
  accountCurrency,
  onClose,
  onSaved,
}: {
  transaction: Transaction;
  categories: UserCategoryItem[];
  accountCurrency: Currency;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [amount, setAmount] = useState(
    formatMoneyInput(String(transaction.amount), accountCurrency),
  );
  const [merchant, setMerchant] = useState(transaction.merchant ?? "");
  const [description, setDescription] = useState(transaction.description ?? "");
  const [transactionDate, setTransactionDate] = useState(
    transaction.transaction_date,
  );
  const [categoryId, setCategoryId] = useState(transaction.category_id ?? "");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (payload: TransactionUpdatePayload) =>
      updateTransaction(transaction.id, payload),
    onSuccess: () => onSaved(),
    onError: (err) => {
      setError(apiErrorMessage(err, "No pudimos guardar la transacción."));
    },
  });

  function submit() {
    setError(null);
    const numericAmount = Number(
      amount.replace(/[^\d.,-]/g, "").replace(/\./g, "").replace(",", "."),
    );
    if (!Number.isFinite(numericAmount)) {
      setError("Poné un monto válido.");
      return;
    }
    const payload: TransactionUpdatePayload = {
      amount: numericAmount.toFixed(2),
      merchant: merchant.trim() || undefined,
      description: description.trim() || undefined,
      transaction_date: transactionDate,
    };
    payload.category_id = categoryId || null;
    mutation.mutate(payload);
  }

  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center">
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">
            Editar movimiento
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
          <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}

        <div className="mt-4 space-y-3">
          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">
              Monto ({transaction.currency})
            </span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              onBlur={() => setAmount(formatMoneyInput(amount, accountCurrency))}
              inputMode="decimal"
            />
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Comercio</span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={merchant}
              onChange={(event) => setMerchant(event.target.value)}
            />
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Categoría</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={categoryId}
              onChange={(event) => setCategoryId(event.target.value)}
            >
              <option value="">Sin categoría</option>
              {categories
                .filter((c) => !c.archived)
                .map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
            </select>
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Fecha</span>
            <input
              type="date"
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={transactionDate}
              onChange={(event) => setTransactionDate(event.target.value)}
            />
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Notas</span>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
            />
          </label>
        </div>

        <div className="mt-5 flex gap-2">
          <button
            type="button"
            onClick={submit}
            disabled={mutation.isPending}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
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
