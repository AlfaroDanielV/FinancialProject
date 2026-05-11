import { zodResolver } from "@hookform/resolvers/zod";
import { AxiosError } from "axios";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { displayMoney, formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  Account,
  AccountForm,
  AccountList,
  AccountType,
  Currency,
} from "../schemas/entities";

const ACCOUNT_TYPE_OPTIONS: Array<{ value: AccountType; label: string }> = [
  { value: "checking", label: "Cuenta corriente" },
  { value: "savings", label: "Ahorros" },
  { value: "credit", label: "Crédito" },
  { value: "investment", label: "Inversión" },
];

const CURRENCY_OPTIONS: Array<{ value: Currency; label: string }> = [
  { value: "CRC", label: "CRC" },
  { value: "USD", label: "USD" },
];

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function accountTypeLabel(type: AccountType) {
  return ACCOUNT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type;
}

function toFormDefaults(account?: Account): AccountForm {
  return {
    name: account?.name ?? "",
    account_type: account?.account_type ?? "checking",
    currency: account?.currency ?? "CRC",
    initial_balance: account
      ? displayMoney(account.initial_balance, account.currency)
      : "0",
  };
}

export default function AccountsNew() {
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const form = useForm<AccountForm>({
    resolver: zodResolver(AccountForm),
    defaultValues: toFormDefaults(),
  });
  const editForm = useForm<AccountForm>({
    resolver: zodResolver(AccountForm),
    defaultValues: toFormDefaults(),
  });

  const selectedCurrency = form.watch("currency");
  const selectedType = form.watch("account_type");
  const editCurrency = editForm.watch("currency");

  const sortedAccounts = useMemo(
    () => [...accounts].sort((a, b) => a.name.localeCompare(b.name)),
    [accounts],
  );

  async function loadAccounts() {
    setLoading(true);
    setLoadError(null);
    try {
      const resp = await api.get("/accounts");
      const parsed = AccountList.parse(resp.data);
      setAccounts(parsed);
    } catch {
      setLoadError("No pudimos cargar tus cuentas.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAccounts();
  }, []);

  async function createAccount(values: AccountForm) {
    setServerError(null);
    const amount = moneyToApiString(values.initial_balance, values.currency);
    if (amount === null) return;

    try {
      await api.post("/accounts", {
        name: values.name.trim(),
        account_type: values.account_type,
        currency: values.currency,
        initial_balance: amount,
      });
      navigate(`/?created=account&name=${encodeURIComponent(values.name.trim())}`);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos crear la cuenta. Revisá los datos."),
      );
    }
  }

  function beginEdit(account: Account) {
    setEditingId(account.id);
    editForm.reset(toFormDefaults(account));
  }

  async function saveEdit(account: Account, values: AccountForm) {
    setServerError(null);
    const amount = moneyToApiString(values.initial_balance, values.currency);
    if (amount === null) return;

    try {
      const resp = await api.patch(`/accounts/${account.id}`, {
        name: values.name.trim(),
        account_type: values.account_type,
        currency: values.currency,
        initial_balance: amount,
      });
      const updated = Account.parse(resp.data);
      setAccounts((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEditingId(null);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos guardar los cambios."),
      );
    }
  }

  async function deleteAccount(account: Account) {
    setDeletingId(account.id);
    setServerError(null);
    try {
      await api.delete(`/accounts/${account.id}`);
      setAccounts((current) => current.filter((item) => item.id !== account.id));
    } catch (error) {
      setServerError(apiErrorMessage(error, "No pudimos eliminar la cuenta."));
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
          <h1 className="text-2xl font-semibold text-slate-900">Cuentas</h1>
          <p className="mt-1 text-slate-600">
            Agregá las cuentas que usás para mover plata.
          </p>
        </div>
      </header>

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </div>
      )}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Nueva cuenta</h2>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={form.handleSubmit(createAccount)}
        >
          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">Nombre</span>
            <input
              {...form.register("name")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              placeholder="BAC débito"
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
              {...form.register("account_type")}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
            >
              {ACCOUNT_TYPE_OPTIONS.map((option) => (
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

          <label className="space-y-1 sm:col-span-2">
            <span className="text-sm font-medium text-slate-700">
              Saldo inicial
            </span>
            <input
              {...form.register("initial_balance")}
              inputMode="decimal"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-accent"
              onBlur={() => {
                const value = form.getValues("initial_balance");
                form.setValue(
                  "initial_balance",
                  formatMoneyInput(value, selectedCurrency),
                  { shouldValidate: true },
                );
              }}
            />
            {form.formState.errors.initial_balance && (
              <span className="text-sm text-red-700">
                {form.formState.errors.initial_balance.message}
              </span>
            )}
            {selectedType === "credit" && (
              <span className="block text-sm text-slate-500">
                Si ya debés en esa tarjeta, podés poner el saldo en negativo.
              </span>
            )}
          </label>

          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={form.formState.isSubmitting}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {form.formState.isSubmitting ? "Guardando..." : "Guardar cuenta"}
            </button>
          </div>
        </form>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Cuentas registradas</h2>
        {loading && <p className="text-slate-500">Cargando cuentas...</p>}
        {loadError && <p className="text-red-700">{loadError}</p>}
        {!loading && !loadError && sortedAccounts.length === 0 && (
          <p className="text-slate-500">Todavía no tenés cuentas registradas.</p>
        )}

        <div className="space-y-3">
          {sortedAccounts.map((account) => (
            <article
              key={account.id}
              className="rounded-lg border border-slate-200 bg-white p-4"
            >
              {editingId === account.id ? (
                <form
                  className="grid gap-3 sm:grid-cols-2"
                  onSubmit={editForm.handleSubmit((values) =>
                    saveEdit(account, values),
                  )}
                >
                  <input
                    {...editForm.register("name")}
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                  />
                  <select
                    {...editForm.register("account_type")}
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent"
                  >
                    {ACCOUNT_TYPE_OPTIONS.map((option) => (
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
                  <input
                    {...editForm.register("initial_balance")}
                    inputMode="decimal"
                    className="rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-accent sm:col-span-2"
                    onBlur={() => {
                      const value = editForm.getValues("initial_balance");
                      editForm.setValue(
                        "initial_balance",
                        formatMoneyInput(value, editCurrency),
                        { shouldValidate: true },
                      );
                    }}
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
                    <h3 className="font-semibold text-slate-900">{account.name}</h3>
                    <p className="text-sm text-slate-500">
                      {accountTypeLabel(account.account_type)} · {account.currency} ·{" "}
                      {displayMoney(account.initial_balance, account.currency)}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => beginEdit(account)}
                      className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      Editar
                    </button>
                    <button
                      type="button"
                      disabled={deletingId === account.id}
                      onClick={() => {
                        if (window.confirm(`¿Eliminar ${account.name}?`)) {
                          void deleteAccount(account);
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
