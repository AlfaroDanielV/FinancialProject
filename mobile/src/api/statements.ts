/**
 * Bank-statement reconciliation (PDF → balance anchor).
 *
 * `parseStatement` uploads a statement PDF; the backend reads every product +
 * its closing balance and suggests a matching account/debt. `reconcileStatement`
 * applies the chosen mappings — anchoring each deposit/credit account to its
 * corte balance and setting each loan's current_balance. Mirrors the
 * parseDebtDocument FormData shape; "LLM extracts, rules decide".
 */
import { api, LLM_UPLOAD_TIMEOUT_MS } from "./client";

export type StatementKind = "deposit" | "credit" | "loan";

export interface StatementProduct {
  label: string | null;
  account_last4: string | null;
  iban: string | null;
  account_number: string | null;
  currency: string;
  kind: StatementKind;
  // null when no role candidate resolved — the row must not anchor a fabricated 0.
  closing_balance: number | null;
  suggested_account_id: string | null;
  suggested_debt_id: string | null;
  // Enrichment (generalized reconciliation): null = unverifiable, true =
  // conservation-checked, false = mismatch → needs review.
  conservation_ok?: boolean | null;
  needs_review?: boolean;
  review_reason?: string | null;
  target_role?: string | null;
  attributed_instruments?: string[];
  candidate_ids?: string[];
  // 1.0 = a confident identity match; only then do we self-stamp identity.
  match_confidence?: number;
}

export interface StatementExtraction {
  bank: string | null;
  corte_date: string | null; // YYYY-MM-DD
  products: StatementProduct[];
  confidence: number;
}

export interface StatementReconcileItem {
  kind: StatementKind;
  target_id: string;
  closing_balance: string; // decimal as string
  currency?: string | null; // writer guards a currency mismatch when present
  iban?: string | null; // self-stamped onto the target if its column is null
  account_number?: string | null;
  last4?: string | null;
  conservation_ok?: boolean | null;
  needs_review?: boolean;
}

export interface StatementReconcileResultItem {
  target_id: string;
  kind: StatementKind;
  name: string;
  delta: string | null;
  anchor_id: string | null;
  new_balance: string;
  conservation_ok?: boolean | null;
  needs_review?: boolean;
}

export interface StatementReconcileResponse {
  corte_date: string;
  results: StatementReconcileResultItem[];
}

export const STATEMENT_KIND_LABELS: Record<StatementKind, string> = {
  deposit: "Cuenta",
  credit: "Tarjeta",
  loan: "Préstamo",
};

/** Upload a statement PDF → parsed products + suggested targets. No write. */
export async function parseStatement(uri: string): Promise<StatementExtraction> {
  const form = new FormData();
  const filename = uri.split("/").pop() ?? "estado.pdf";
  form.append("file", {
    uri,
    type: "application/pdf",
    name: filename,
  } as unknown as Blob);
  const { data } = await api.post<StatementExtraction>(
    "/accounts/parse-statement",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: LLM_UPLOAD_TIMEOUT_MS,
    },
  );
  return data;
}

/** Apply the reconciliation: anchor accounts + set loan balances at the corte. */
export async function reconcileStatement(body: {
  corte_date: string;
  items: StatementReconcileItem[];
}): Promise<StatementReconcileResponse> {
  const { data } = await api.post<StatementReconcileResponse>(
    "/accounts/reconcile-statement",
    body,
  );
  return data;
}
