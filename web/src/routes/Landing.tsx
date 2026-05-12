import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { OnboardingStatus } from "../schemas/onboarding";

interface SectionState {
  done: boolean;
  count: number;
  label: string;
  cta: string;
  href: string;
}

export default function Landing() {
  const auth = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const created = searchParams.get("created");
  const createdName = searchParams.get("name");

  useEffect(() => {
    if (auth.kind !== "authenticated") return;
    let cancelled = false;
    api
      .get("/onboarding/status")
      .then((resp) => {
        if (cancelled) return;
        const parsed = OnboardingStatus.safeParse(resp.data);
        if (!parsed.success) {
          setError("La respuesta del servidor no coincide con el contrato esperado.");
          return;
        }
        setStatus(parsed.data);
      })
      .catch(() => {
        if (cancelled) return;
        setError("No pudimos cargar tu progreso. Volvé a intentar en un momento.");
      });
    return () => {
      cancelled = true;
    };
  }, [auth.kind]);

  if (auth.kind === "unauthenticated") {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">No estás autenticado</h1>
        <p className="text-slate-600">
          Volvé a Telegram y mandá <code className="bg-slate-100 px-1 rounded">/setup</code> al
          bot para recibir un nuevo link.
        </p>
      </div>
    );
  }

  if (error) {
    return <p className="text-red-700">{error}</p>;
  }

  if (!status) {
    return <p className="text-slate-500">Cargando tu progreso...</p>;
  }

  const sections: SectionState[] = [
    {
      done: status.has_accounts,
      count: status.accounts_count,
      label: "Cuentas",
      cta: "Agregar una cuenta",
      href: "/accounts/new",
    },
    {
      done: status.has_incomes,
      count: status.incomes_count,
      label: "Ingresos recurrentes",
      cta: "Registrar un ingreso",
      href: "/incomes/new",
    },
    {
      done: status.has_debts,
      count: status.debts_count,
      label: "Deudas",
      cta: "Agregar una deuda",
      href: "/debts/new",
    },
    {
      done: status.has_recurring_bills,
      count: status.recurring_bills_count,
      label: "Gastos fijos",
      cta: "Registrar un gasto fijo",
      href: "/bills/new",
    },
  ];

  const pct = Math.round(status.completeness_score * 100);
  const createdLabel =
    created === "account"
      ? "Cuenta guardada"
      : created === "income"
        ? "Ingreso guardado"
        : created === "bill"
          ? "Gasto fijo guardado"
          : null;

  return (
    <div className="space-y-6">
      {createdLabel && (
        <div className="flex items-start justify-between gap-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          <span>
            {createdLabel}
            {createdName ? `: ${createdName}` : ""}.
          </span>
          <button
            type="button"
            className="font-semibold text-green-900"
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

      <section>
        <h1 className="text-2xl font-semibold text-slate-900">Configurá tu cuenta</h1>
        <p className="text-slate-600 mt-1">
          Vas {pct}% del camino. Podés llenar todo de una o ir agregando con calma —
          el bot funciona aunque la información no esté completa.
        </p>
      </section>

      <section className="space-y-3">
        {sections.map((s) => (
          <article
            key={s.label}
            className="border border-slate-200 rounded-lg bg-white p-4 flex items-center justify-between"
          >
            <div>
              <p className="font-medium text-slate-900">{s.label}</p>
              <p className="text-sm text-slate-500">
                {s.done ? `${s.count} registrados` : "Aún no agregaste ninguno"}
              </p>
            </div>
            <Link
              to={s.href}
              className="text-sm font-medium text-accent hover:text-accent-dark"
            >
              {s.cta} →
            </Link>
          </article>
        ))}
      </section>
    </div>
  );
}
