import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createTransfer,
  fetchAccounts,
  TransferPayload,
} from "../api/accounts";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import { Account, AccountType, Currency } from "../schemas/entities";

const TYPE_LABELS: Record<AccountType, string> = {
  checking: "Cuenta corriente",
  savings: "Ahorros",
  credit: "Crédito",
  investment: "Inversión",
};

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export default function AccountsIndex() {
  const [showArchived, setShowArchived] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const created = searchParams.get("created");
  const createdName = searchParams.get("name");

  const accountsQuery = useQuery({
    queryKey: ["accounts", showArchived],
    queryFn: () => fetchAccounts(showArchived),
  });

  const accounts = accountsQuery.data ?? [];
  const visible = useMemo(() => {
    return [...accounts].sort((a, b) => {
      if (a.archived !== b.archived) return a.archived ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
  }, [accounts]);

  const activeCount = accounts.filter((a) => !a.archived).length;

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2">
        <Link
          to="/"
          className="text-sm font-medium text-accent hover:text-accent-dark"
        >
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Cuentas</h1>
            <p className="mt-1 text-slate-600">
              Tu vista completa de saldos y movimientos por cuenta.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/accounts/new"
              className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
            >
              Nueva cuenta
            </Link>
            <button
              type="button"
              onClick={() => setTransferOpen(true)}
              disabled={activeCount < 2}
              className="inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              Nueva transferencia
            </button>
          </div>
        </div>
      </header>

      {created === "account" && (
        <div className="flex items-start justify-between gap-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          <span>
            Cuenta guardada{createdName ? `: ${createdName}` : ""}.
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

      <div className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-4 py-3">
        <p className="text-sm text-slate-600">
          {activeCount} activa{activeCount === 1 ? "" : "s"}
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Mostrar archivadas
        </label>
      </div>

      {accountsQuery.isLoading && (
        <p className="text-slate-500">Cargando cuentas...</p>
      )}
      {accountsQuery.isError && (
        <p className="text-red-700">No pudimos cargar tus cuentas.</p>
      )}
      {!accountsQuery.isLoading && !accountsQuery.isError && visible.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>Todavía no tenés cuentas registradas.</p>
          <Link
            to="/accounts/new"
            className="mt-3 inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark"
          >
            Crear la primera
          </Link>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {visible.map((account) => (
          <AccountCard key={account.id} account={account} />
        ))}
      </div>

      {transferOpen && (
        <TransferModal
          accounts={accounts.filter((a) => !a.archived)}
          onClose={() => setTransferOpen(false)}
        />
      )}
    </div>
  );
}

function AccountCard({ account }: { account: Account }) {
  const balanceLabel =
    account.current_balance !== null && account.current_balance !== undefined
      ? displayMoney(account.current_balance, account.currency)
      : displayMoney(account.initial_balance, account.currency);

  return (
    <Link
      to={`/accounts/${account.id}`}
      className={`block rounded-lg border bg-white p-4 shadow-sm transition hover:shadow ${
        account.archived ? "border-slate-200 opacity-70" : "border-slate-200"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{account.name}</p>
          <p className="text-sm text-slate-500">
            {TYPE_LABELS[account.account_type]} · {account.currency}
          </p>
          {account.archived && (
            <span className="mt-1 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
              Archivada
            </span>
          )}
        </div>
        <div className="text-right">
          <p className="text-xs text-slate-500">Saldo actual</p>
          <p className="text-lg font-semibold text-slate-950">{balanceLabel}</p>
        </div>
      </div>
    </Link>
  );
}

function TransferModal({
  accounts,
  onClose,
}: {
  accounts: Account[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const eligible = accounts.filter((a) => !a.archived);
  const [fromId, setFromId] = useState(eligible[0]?.id ?? "");
  const [toId, setToId] = useState(eligible[1]?.id ?? "");
  const [amount, setAmount] = useState("0");
  const [fxRate, setFxRate] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fromAccount = eligible.find((a) => a.id === fromId);
  const toAccount = eligible.find((a) => a.id === toId);
  const currenciesDiffer =
    fromAccount && toAccount && fromAccount.currency !== toAccount.currency;
  const currency: Currency = fromAccount?.currency ?? "CRC";

  const mutation = useMutation({
    mutationFn: (payload: TransferPayload) => createTransfer(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      onClose();
    },
    onError: (err) => {
      setError(apiErrorMessage(err, "No pudimos crear la transferencia."));
    },
  });

  function submit() {
    setError(null);
    if (!fromId || !toId || fromId === toId) {
      setError("Escogé dos cuentas distintas.");
      return;
    }
    const amountString = moneyToApiString(amount, currency);
    if (amountString === null || Number(amountString) <= 0) {
      setError("Poné un monto mayor a cero.");
      return;
    }
    let fxParsed: string | undefined;
    if (currenciesDiffer) {
      const parsed = Number(fxRate.replace(",", "."));
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setError("Poné un FX rate mayor a cero.");
        return;
      }
      fxParsed = parsed.toFixed(8);
    }
    mutation.mutate({
      from_account_id: fromId,
      to_account_id: toId,
      amount: amountString,
      currency,
      fx_rate: fxParsed,
      occurred_at: new Date().toISOString(),
      notes: notes.trim() || undefined,
    });
  }

  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center">
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">
            Nueva transferencia
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
            <span className="font-medium text-slate-700">Desde</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={fromId}
              onChange={(event) => setFromId(event.target.value)}
            >
              {eligible.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.currency})
                </option>
              ))}
            </select>
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Hacia</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={toId}
              onChange={(event) => setToId(event.target.value)}
            >
              {eligible.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.currency})
                </option>
              ))}
            </select>
          </label>

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">
              Monto ({currency})
            </span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              onBlur={() => setAmount(formatMoneyInput(amount, currency))}
              inputMode="decimal"
            />
          </label>

          {currenciesDiffer && (
            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">
                FX rate ({fromAccount?.currency} → {toAccount?.currency})
              </span>
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-2"
                value={fxRate}
                onChange={(event) => setFxRate(event.target.value)}
                inputMode="decimal"
                placeholder="0.00195"
              />
            </label>
          )}

          <label className="block space-y-1 text-sm">
            <span className="font-medium text-slate-700">Notas (opcional)</span>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
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
            {mutation.isPending ? "Guardando..." : "Transferir"}
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
