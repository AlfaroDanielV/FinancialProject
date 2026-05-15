import { zodResolver } from "@hookform/resolvers/zod";
import { AxiosError } from "axios";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { formatMoneyInput, moneyToApiString } from "../lib/money";
import {
  AccountForm,
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

export default function AccountsNew() {
  const navigate = useNavigate();
  const [serverError, setServerError] = useState<string | null>(null);

  const form = useForm<AccountForm>({
    resolver: zodResolver(AccountForm),
    defaultValues: {
      name: "",
      account_type: "checking",
      currency: "CRC",
      initial_balance: "0",
    },
  });

  const selectedCurrency = form.watch("currency");
  const selectedType = form.watch("account_type");

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
      navigate(`/accounts?created=account&name=${encodeURIComponent(values.name.trim())}`);
    } catch (error) {
      setServerError(
        apiErrorMessage(error, "No pudimos crear la cuenta. Revisá los datos."),
      );
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <Link to="/accounts" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Nueva cuenta</h1>
          <p className="mt-1 text-slate-600">
            La moneda y el saldo inicial no se pueden cambiar después.
          </p>
        </div>
      </header>

      {serverError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </div>
      )}

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
          <span className="text-sm font-medium text-slate-700">Saldo inicial</span>
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
    </div>
  );
}
