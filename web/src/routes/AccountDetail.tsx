import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  AccountUpdatePayload,
  archiveAccount,
  fetchAccount,
  fetchUserCategories,
  restoreAccount,
  updateAccount,
} from "../api/accounts";
import {
  fetchTransactions,
  TransactionFilters,
  TransactionKind,
} from "../api/transactions";
import { TransactionEditModal } from "../components/transactions/TransactionEditModal";
import { displayMoney } from "../lib/money";
import {
  Account,
  AccountType,
  Transaction,
  UserCategoryItem,
} from "../schemas/entities";

const TYPE_LABELS: Record<AccountType, string> = {
  checking: "Cuenta corriente",
  savings: "Ahorros",
  credit: "Crédito",
  investment: "Inversión",
};

const TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({
  value: value as AccountType,
  label,
}));

const KIND_OPTIONS: Array<{ value: TransactionKind; label: string }> = [
  { value: "all", label: "Todos" },
  { value: "income", label: "Ingresos" },
  { value: "expense", label: "Gastos" },
  { value: "transfer", label: "Transferencias" },
];

const PAGE_SIZE = 25;

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

function statusBadge(status: string) {
  if (status === "shadow") {
    return (
      <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
        Pendiente
      </span>
    );
  }
  if (status === "pending_review") {
    return (
      <span className="ml-2 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
        En revisión
      </span>
    );
  }
  return null;
}

export default function AccountDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [editingTxn, setEditingTxn] = useState<Transaction | null>(null);
  const [filters, setFilters] = useState<TransactionFilters>({
    account_id: id,
    kind: "all",
  });
  const [page, setPage] = useState(0);
  const [serverError, setServerError] = useState<string | null>(null);

  const accountQuery = useQuery({
    queryKey: ["account", id],
    queryFn: () => fetchAccount(id),
    enabled: Boolean(id),
  });

  const categoriesQuery = useQuery({
    queryKey: ["user-categories"],
    queryFn: fetchUserCategories,
  });

  const transactionsQuery = useQuery({
    queryKey: ["transactions", id, filters, page],
    queryFn: () =>
      fetchTransactions({
        ...filters,
        account_id: id,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    enabled: Boolean(id),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: AccountUpdatePayload) => updateAccount(id, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["account", id], data);
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setEditing(false);
    },
    onError: (err) => {
      setServerError(apiErrorMessage(err, "No pudimos guardar la cuenta."));
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["account", id] });
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      navigate("/accounts");
    },
    onError: (err) => {
      setServerError(apiErrorMessage(err, "No pudimos archivar la cuenta."));
    },
  });

  const restoreMutation = useMutation({
    mutationFn: () => restoreAccount(id),
    onSuccess: (data) => {
      queryClient.setQueryData(["account", id], data);
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => {
      setServerError(apiErrorMessage(err, "No pudimos restaurar la cuenta."));
    },
  });

  const account = accountQuery.data;
  const categories = categoriesQuery.data ?? [];
  const transactionList = transactionsQuery.data;
  const total = transactionList?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const visibleTransactions = transactionList?.items ?? [];

  function patchFilter<K extends keyof TransactionFilters>(
    key: K,
    value: TransactionFilters[K] | undefined,
  ) {
    setFilters((prev) => {
      const next = { ...prev };
      if (value === undefined || value === "" || value === null) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
    setPage(0);
  }

  if (!id) {
    return <p className="text-slate-700">Cuenta inválida.</p>;
  }
  if (accountQuery.isLoading) {
    return <p className="text-slate-500">Cargando cuenta...</p>;
  }
  if (accountQuery.isError || !account) {
    return (
      <div className="space-y-3">
        <Link to="/accounts" className="text-sm font-medium text-accent">
          Volver a cuentas
        </Link>
        <p className="text-red-700">No pudimos encontrar esta cuenta.</p>
      </div>
    );
  }

  const balanceCurrent = displayMoney(
    account.current_balance ?? account.initial_balance,
    account.currency,
  );
  const balanceMonthStart = displayMoney(
    account.month_start_balance ?? account.initial_balance,
    account.currency,
  );

  return (
    <div className="space-y-6">
      <Link
        to="/accounts"
        className="text-sm font-medium text-accent hover:text-accent-dark"
      >
        Volver a cuentas
      </Link>

      {serverError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </p>
      )}

      {account.archived && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div>
            <p className="font-medium">Esta cuenta está archivada.</p>
            <p className="mt-1">
              Las transacciones históricas se mantienen, pero no podés editarla
              ni transferir mientras esté archivada.
            </p>
          </div>
          <button
            type="button"
            onClick={() => restoreMutation.mutate()}
            disabled={restoreMutation.isPending}
            className="min-h-11 rounded-md border border-amber-400 bg-white px-3 py-2 text-sm font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60"
          >
            {restoreMutation.isPending ? "Restaurando..." : "Restaurar cuenta"}
          </button>
        </div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {editing && !account.archived ? (
          <AccountEditForm
            account={account}
            onCancel={() => setEditing(false)}
            onSubmit={(values) => updateMutation.mutate(values)}
            submitting={updateMutation.isPending}
          />
        ) : (
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-slate-900">
                {account.name}
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                {TYPE_LABELS[account.account_type]} · {account.currency}
              </p>
            </div>
            <div className="grid w-full max-w-sm grid-cols-2 gap-3 text-right">
              <div>
                <p className="text-xs text-slate-500">Saldo actual</p>
                <p className="text-lg font-semibold text-slate-950">
                  {balanceCurrent}
                </p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Inicio de mes</p>
                <p className="text-lg font-semibold text-slate-700">
                  {balanceMonthStart}
                </p>
              </div>
            </div>
          </div>
        )}

        {!editing && !account.archived && (
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
            >
              Editar
            </button>
            <button
              type="button"
              onClick={() => {
                const balanceText =
                  account.current_balance ?? account.initial_balance;
                const balanceLabel = displayMoney(balanceText, account.currency);
                if (
                  window.confirm(
                    `Archivar ${account.name}? Saldo actual: ${balanceLabel}.`,
                  )
                ) {
                  archiveMutation.mutate();
                }
              }}
              disabled={archiveMutation.isPending}
              className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
            >
              {archiveMutation.isPending ? "Archivando..." : "Archivar cuenta"}
            </button>
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-base font-semibold text-slate-900">Movimientos</h2>
        <TransactionFiltersBar
          categories={categories}
          filters={filters}
          onChange={patchFilter}
        />

        {transactionsQuery.isLoading && (
          <p className="text-sm text-slate-500">Buscando movimientos...</p>
        )}
        {transactionsQuery.isError && (
          <p className="text-sm text-red-700">No pudimos cargar movimientos.</p>
        )}
        {!transactionsQuery.isLoading && visibleTransactions.length === 0 && (
          <p className="text-sm text-slate-500">
            Sin movimientos para este filtro.
          </p>
        )}

        <ul className="divide-y divide-slate-100">
          {visibleTransactions.map((txn) => (
            <li
              key={txn.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div>
                <p className="font-medium text-slate-900">
                  {txn.merchant || txn.description || "Movimiento"}
                  {statusBadge(txn.status)}
                </p>
                <p className="text-xs text-slate-500">
                  {formatShortDate(txn.transaction_date)}
                  {txn.category ? ` · ${txn.category}` : ""}
                  {txn.transfer_id ? " · Transferencia" : ""}
                </p>
              </div>
              <div className="flex items-center justify-between gap-3 sm:justify-end">
                <span
                  className={`text-base font-semibold ${
                    txn.amount >= 0 ? "text-emerald-700" : "text-slate-900"
                  }`}
                >
                  {displayMoney(txn.amount, txn.currency)}
                </span>
                {txn.status === "confirmed" && !txn.transfer_id && (
                  <button
                    type="button"
                    onClick={() => setEditingTxn(txn)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    Editar
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>

        {pageCount > 1 && (
          <div className="flex items-center justify-between pt-2 text-sm">
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
          accountCurrency={account.currency}
          onClose={() => setEditingTxn(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["transactions", id] });
            queryClient.invalidateQueries({ queryKey: ["account", id] });
            queryClient.invalidateQueries({ queryKey: ["accounts"] });
            setEditingTxn(null);
          }}
        />
      )}
    </div>
  );
}

function AccountEditForm({
  account,
  onCancel,
  onSubmit,
  submitting,
}: {
  account: Account;
  onCancel: () => void;
  onSubmit: (values: AccountUpdatePayload) => void;
  submitting: boolean;
}) {
  const [name, setName] = useState(account.name);
  const [type, setType] = useState<AccountType>(account.account_type);

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ name: name.trim(), account_type: type });
      }}
    >
      <label className="space-y-1 sm:col-span-2 text-sm">
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
        <span className="font-medium text-slate-700">Tipo</span>
        <select
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          value={type}
          onChange={(event) => setType(event.target.value as AccountType)}
        >
          {TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <div className="space-y-1 text-sm">
        <span className="font-medium text-slate-700">Moneda · Saldo inicial</span>
        <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-slate-500">
          {account.currency} · {displayMoney(account.initial_balance, account.currency)}
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

function TransactionFiltersBar({
  categories,
  filters,
  onChange,
}: {
  categories: UserCategoryItem[];
  filters: TransactionFilters;
  onChange: <K extends keyof TransactionFilters>(
    key: K,
    value: TransactionFilters[K] | undefined,
  ) => void;
}) {
  const activeCategories = useMemo(
    () => categories.filter((c) => !c.archived),
    [categories],
  );

  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <input
        type="search"
        placeholder="Buscar..."
        value={filters.q ?? ""}
        onChange={(event) => onChange("q", event.target.value || undefined)}
        className="rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      <select
        value={filters.kind ?? "all"}
        onChange={(event) =>
          onChange("kind", event.target.value as TransactionKind)
        }
        className="rounded-md border border-slate-300 px-3 py-2 text-sm"
      >
        {KIND_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <select
        value={filters.category_id ?? ""}
        onChange={(event) =>
          onChange("category_id", event.target.value || undefined)
        }
        className="rounded-md border border-slate-300 px-3 py-2 text-sm"
      >
        <option value="">Cualquier categoría</option>
        {activeCategories.map((cat) => (
          <option key={cat.id} value={cat.id}>
            {cat.name}
          </option>
        ))}
      </select>
      <label className="flex flex-col text-xs text-slate-500">
        Desde
        <input
          type="date"
          value={filters.from_date ?? ""}
          onChange={(event) =>
            onChange("from_date", event.target.value || undefined)
          }
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
      </label>
      <label className="flex flex-col text-xs text-slate-500">
        Hasta
        <input
          type="date"
          value={filters.to_date ?? ""}
          onChange={(event) =>
            onChange("to_date", event.target.value || undefined)
          }
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
      </label>
    </div>
  );
}

