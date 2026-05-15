import { AxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { fetchAccounts, fetchUserCategories } from "../api/accounts";
import {
  TransactionFilters,
  TransactionKind,
  TransactionSortBy,
  TransactionSortDir,
  bulkArchiveTransactions,
  bulkCategorizeTransactions,
  downloadTransactionsCsv,
  fetchTransactions,
} from "../api/transactions";
import { TransactionEditModal } from "../components/transactions/TransactionEditModal";
import { displayMoney, moneyToApiString } from "../lib/money";
import { Transaction } from "../schemas/entities";

const KIND_OPTIONS: Array<{ value: TransactionKind; label: string }> = [
  { value: "all", label: "Todos" },
  { value: "income", label: "Ingresos" },
  { value: "expense", label: "Gastos" },
  { value: "transfer", label: "Transferencias" },
];

const SORT_OPTIONS: Array<{
  value: `${TransactionSortBy}:${TransactionSortDir}`;
  label: string;
}> = [
  { value: "date:desc", label: "Más recientes" },
  { value: "date:asc", label: "Más antiguos" },
  { value: "amount:desc", label: "Mayor monto" },
  { value: "amount:asc", label: "Menor monto" },
  { value: "category:asc", label: "Categoría A-Z" },
];

const PAGE_SIZE = 25;

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

function formatShortDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

export default function TransactionsIndex() {
  const queryClient = useQueryClient();

  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [categoryIds, setCategoryIds] = useState<string[]>([]);
  const [kind, setKind] = useState<TransactionKind>("all");
  const [currency, setCurrency] = useState<string>("");
  const [search, setSearch] = useState("");
  const [minAmount, setMinAmount] = useState("");
  const [maxAmount, setMaxAmount] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sort, setSort] = useState<string>("date:desc");
  const [includeArchived, setIncludeArchived] = useState(false);

  const [page, setPage] = useState(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [editingTxn, setEditingTxn] = useState<Transaction | null>(null);
  const [bulkCategoryId, setBulkCategoryId] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);

  const accountsQuery = useQuery({
    queryKey: ["accounts", true],
    queryFn: () => fetchAccounts(true),
  });
  const categoriesQuery = useQuery({
    queryKey: ["user-categories"],
    queryFn: fetchUserCategories,
  });

  const accounts = accountsQuery.data ?? [];
  const categories = categoriesQuery.data ?? [];

  const [sortBy, sortDir] = sort.split(":") as [
    TransactionSortBy,
    TransactionSortDir,
  ];

  const filters: TransactionFilters = useMemo(() => {
    const minNum = minAmount ? moneyToApiString(minAmount, "CRC") : null;
    const maxNum = maxAmount ? moneyToApiString(maxAmount, "CRC") : null;
    return {
      account_ids: accountIds.length ? accountIds : undefined,
      category_ids: categoryIds.length ? categoryIds : undefined,
      kind,
      currency: currency || undefined,
      q: search || undefined,
      min_amount: minNum ?? undefined,
      max_amount: maxNum ?? undefined,
      from_date: fromDate || undefined,
      to_date: toDate || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      include_archived: includeArchived,
    };
  }, [
    accountIds,
    categoryIds,
    kind,
    currency,
    search,
    minAmount,
    maxAmount,
    fromDate,
    toDate,
    sortBy,
    sortDir,
    includeArchived,
  ]);

  useEffect(() => {
    setPage(0);
    setSelectedIds(new Set());
  }, [filters]);

  const transactionsQuery = useQuery({
    queryKey: ["transactions", "index", filters, page],
    queryFn: () =>
      fetchTransactions({
        ...filters,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const archiveMutation = useMutation({
    mutationFn: (ids: string[]) => bulkArchiveTransactions(ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setSelectedIds(new Set());
      setActionError(null);
    },
    onError: (err) =>
      setActionError(apiErrorMessage(err, "No pudimos archivar.")),
  });

  const categorizeMutation = useMutation({
    mutationFn: (input: { ids: string[]; categoryId: string | null }) =>
      bulkCategorizeTransactions(input.ids, input.categoryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      setSelectedIds(new Set());
      setBulkCategoryId("");
      setActionError(null);
    },
    onError: (err) =>
      setActionError(apiErrorMessage(err, "No pudimos categorizar.")),
  });

  const exportMutation = useMutation({
    mutationFn: () => downloadTransactionsCsv(filters),
    onError: (err) =>
      setActionError(apiErrorMessage(err, "No pudimos exportar el CSV.")),
  });

  const txns = transactionsQuery.data?.items ?? [];
  const total = transactionsQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const allOnPageSelected =
    txns.length > 0 && txns.every((t) => selectedIds.has(t.id));

  function toggle(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllOnPage() {
    if (allOnPageSelected) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        txns.forEach((t) => next.delete(t.id));
        return next;
      });
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        txns.forEach((t) => next.add(t.id));
        return next;
      });
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Movimientos</h1>
            <p className="mt-1 text-slate-600">
              La lista completa de tus transacciones, filtrable y exportable.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => exportMutation.mutate()}
              disabled={exportMutation.isPending || total === 0}
              className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
            >
              {exportMutation.isPending ? "Exportando..." : "Exportar CSV"}
            </button>
          </div>
        </div>
      </header>

      <p className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
        La forma más rápida de registrar un movimiento es decírselo al bot
        ("gasté 5000 en el super"). Esta vista es para revisar, filtrar y
        editar lo ya cargado.
      </p>

      {actionError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {actionError}
        </p>
      )}

      <section className="grid gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-3">
        <input
          type="search"
          placeholder="Buscar..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="rounded-md border border-slate-300 px-3 py-2 text-sm sm:col-span-3"
        />

        <label className="text-xs text-slate-500">
          Cuentas
          <select
            multiple
            value={accountIds}
            onChange={(event) =>
              setAccountIds(
                Array.from(event.target.selectedOptions, (opt) => opt.value),
              )
            }
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
            size={Math.min(4, Math.max(2, accounts.length))}
          >
            {accounts.map((acc) => (
              <option key={acc.id} value={acc.id}>
                {acc.name} {acc.archived ? "(archivada)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs text-slate-500">
          Categorías
          <select
            multiple
            value={categoryIds}
            onChange={(event) =>
              setCategoryIds(
                Array.from(event.target.selectedOptions, (opt) => opt.value),
              )
            }
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
            size={Math.min(4, Math.max(2, categories.length))}
          >
            {categories
              .filter((c) => !c.archived)
              .map((cat) => (
                <option key={cat.id} value={cat.id}>
                  {cat.name}
                </option>
              ))}
          </select>
        </label>

        <label className="text-xs text-slate-500">
          Tipo
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as TransactionKind)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs text-slate-500">
          Moneda
          <select
            value={currency}
            onChange={(event) => setCurrency(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          >
            <option value="">Todas</option>
            <option value="CRC">CRC</option>
            <option value="USD">USD</option>
          </select>
        </label>

        <label className="text-xs text-slate-500">
          Orden
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs text-slate-500">
          Desde
          <input
            type="date"
            value={fromDate}
            onChange={(event) => setFromDate(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>
        <label className="text-xs text-slate-500">
          Hasta
          <input
            type="date"
            value={toDate}
            onChange={(event) => setToDate(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>
        <label className="text-xs text-slate-500">
          Monto mínimo
          <input
            value={minAmount}
            onChange={(event) => setMinAmount(event.target.value)}
            inputMode="decimal"
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>
        <label className="text-xs text-slate-500">
          Monto máximo
          <input
            value={maxAmount}
            onChange={(event) => setMaxAmount(event.target.value)}
            inputMode="decimal"
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>

        <label className="flex items-center gap-2 text-sm text-slate-700 sm:col-span-3">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
          />
          Incluir archivadas
        </label>
      </section>

      {selectedIds.size > 0 && (
        <section className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 sm:flex-row sm:items-center sm:justify-between">
          <p className="font-medium">
            {selectedIds.size} seleccionada{selectedIds.size === 1 ? "" : "s"}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={bulkCategoryId}
              onChange={(event) => setBulkCategoryId(event.target.value)}
              className="rounded-md border border-amber-300 bg-white px-3 py-2 text-sm text-slate-800"
            >
              <option value="">Categoría...</option>
              {categories
                .filter((c) => !c.archived)
                .map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
            </select>
            <button
              type="button"
              onClick={() =>
                categorizeMutation.mutate({
                  ids: Array.from(selectedIds),
                  categoryId: bulkCategoryId || null,
                })
              }
              disabled={categorizeMutation.isPending}
              className="min-h-11 rounded-md border border-amber-400 bg-white px-3 py-2 text-sm font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60"
            >
              {categorizeMutation.isPending ? "Aplicando..." : "Categorizar"}
            </button>
            <button
              type="button"
              onClick={() => archiveMutation.mutate(Array.from(selectedIds))}
              disabled={archiveMutation.isPending}
              className="min-h-11 rounded-md border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
            >
              {archiveMutation.isPending ? "Archivando..." : "Archivar"}
            </button>
            <button
              type="button"
              onClick={() => setSelectedIds(new Set())}
              className="min-h-11 rounded-md px-3 py-2 text-sm font-semibold text-amber-900"
            >
              Limpiar
            </button>
          </div>
        </section>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {transactionsQuery.isLoading && (
          <p className="text-sm text-slate-500">Buscando movimientos...</p>
        )}
        {transactionsQuery.isError && (
          <p className="text-sm text-red-700">No pudimos cargar movimientos.</p>
        )}
        {!transactionsQuery.isLoading && txns.length === 0 && (
          <div className="space-y-2 py-6 text-center text-slate-600">
            <p>Sin movimientos para este filtro.</p>
            <p className="text-sm text-slate-500">
              Mandá un mensaje al bot tipo "gasté 5000 en el super" o
              ajustá los filtros.
            </p>
          </div>
        )}

        {txns.length > 0 && (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-2 py-2">
                  <input
                    type="checkbox"
                    checked={allOnPageSelected}
                    onChange={toggleAllOnPage}
                  />
                </th>
                <th className="px-2 py-2">Fecha</th>
                <th className="px-2 py-2">Movimiento</th>
                <th className="px-2 py-2 text-right">Monto</th>
                <th className="px-2 py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {txns.map((txn) => (
                <tr key={txn.id} className={txn.archived ? "opacity-60" : ""}>
                  <td className="px-2 py-2 align-top">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(txn.id)}
                      onChange={() => toggle(txn.id)}
                    />
                  </td>
                  <td className="px-2 py-2 align-top text-slate-500">
                    {formatShortDate(txn.transaction_date)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    <p className="font-medium text-slate-900">
                      {txn.merchant || txn.description || "Movimiento"}
                    </p>
                    <p className="text-xs text-slate-500">
                      {txn.category || "Sin categoría"}
                      {txn.transfer_id ? " · Transferencia" : ""}
                      {txn.archived ? " · Archivada" : ""}
                      {txn.status !== "confirmed" ? ` · ${txn.status}` : ""}
                    </p>
                  </td>
                  <td
                    className={`px-2 py-2 text-right align-top font-semibold ${
                      txn.amount >= 0 ? "text-emerald-700" : "text-slate-900"
                    }`}
                  >
                    {displayMoney(txn.amount, txn.currency)}
                  </td>
                  <td className="px-2 py-2 text-right align-top">
                    {!txn.transfer_id &&
                      !txn.archived &&
                      txn.status === "confirmed" && (
                        <button
                          type="button"
                          onClick={() => setEditingTxn(txn)}
                          className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                        >
                          Editar
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {pageCount > 1 && (
          <div className="mt-3 flex items-center justify-between text-sm">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="min-h-11 rounded-md border border-slate-300 px-3 py-1.5 font-medium text-slate-700 disabled:opacity-50"
            >
              Anterior
            </button>
            <span className="text-slate-500">
              Página {page + 1} de {pageCount} · {total} movimientos
            </span>
            <button
              type="button"
              disabled={page >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              className="min-h-11 rounded-md border border-slate-300 px-3 py-1.5 font-medium text-slate-700 disabled:opacity-50"
            >
              Siguiente
            </button>
          </div>
        )}
      </section>

      {editingTxn && (
        <TransactionEditModal
          transaction={editingTxn}
          categories={categories}
          accountCurrency={editingTxn.currency === "USD" ? "USD" : "CRC"}
          onClose={() => setEditingTxn(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["transactions"] });
            queryClient.invalidateQueries({ queryKey: ["accounts"] });
            setEditingTxn(null);
          }}
        />
      )}
    </div>
  );
}
