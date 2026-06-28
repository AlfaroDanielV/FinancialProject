/**
 * Phase 7b B3/B4 — credit-card terms + analysis.
 *
 * Terms are ACCOUNT PARAMETERS (1:1 with the credit account), not a Debt.
 * Rates travel as 0–1 fractions (the UI shows percent and converts).
 * The analysis is computed server-side by the deterministic revolving engine
 * over the LIVE balance — the app never reimplements the interest math.
 */
import { api, LLM_UPLOAD_TIMEOUT_MS } from "./client";

export interface CardTermsPayload {
  annual_interest_rate: string; // 0–1 fraction
  cash_advance_rate?: string | null;
  minimum_payment_pct: string; // 0–1 fraction
  minimum_payment_floor?: string | null;
  credit_limit?: string | null;
  statement_day?: number | null;
  payment_due_day: number;
  /** 'minimum' (must-pay floor) or 'full' (pago de contado — full balance).
   * Selects which live amount the upcoming-payment reminder + agent surface. */
  payment_mode?: "minimum" | "full";
  /** B5 attachment — omit to keep the current envelope; null detaches. */
  envelope_id?: string | null;
  notes?: string | null;
}

export interface CardTermsResponse {
  id: string;
  user_id: string;
  account_id: string;
  annual_interest_rate: string;
  cash_advance_rate: string | null;
  minimum_payment_pct: string;
  minimum_payment_floor: string | null;
  credit_limit: string | null;
  statement_day: number | null;
  payment_due_day: number;
  payment_mode: "minimum" | "full";
  envelope_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface CardTermsExtraction {
  issuer: string | null;
  credit_limit: number | null;
  statement_balance: number | null;
  annual_interest_rate: number | null; // 0–1 fraction (colones terms)
  cash_advance_rate: number | null;
  // Dual-currency CR cards: present only when the contract lists separate
  // dollar terms — their presence is the "this card is ₡ + $" signal.
  annual_interest_rate_usd: number | null;
  credit_limit_usd: number | null;
  statement_balance_usd: number | null;
  minimum_payment_pct: number | null; // 0–1 fraction
  minimum_payment_amount: number | null;
  statement_day: number | null;
  payment_due_day: number | null;
  currency: string | null;
  confidence: number;
}

export interface CardPaymentStrategy {
  label: string;
  monthly_payment: string;
  months: number | null;
  total_interest: string;
  interest_saved_vs_minimum: string | null;
}

export interface CardAnalysisResponse {
  account_id: string;
  currency: string;
  as_of: string;
  balance_owed: string;
  credit_limit: string | null;
  available_credit: string | null;
  minimum_payment: string;
  monthly_interest_cost: string;
  months_to_payoff_minimum: number | null;
  total_interest_minimum: string | null;
  total_paid_minimum: string | null;
  never_pays_off: boolean;
  strategies: CardPaymentStrategy[];
}

export async function fetchCardTerms(
  accountId: string,
): Promise<CardTermsResponse | null> {
  try {
    const { data } = await api.get<CardTermsResponse>(
      `/accounts/${accountId}/card-terms`,
    );
    return data;
  } catch (err) {
    const status = (err as { response?: { status?: number } }).response?.status;
    if (status === 404) return null; // no terms yet — the UI offers "Agregar"
    throw err;
  }
}

export async function upsertCardTerms(
  accountId: string,
  payload: CardTermsPayload,
): Promise<CardTermsResponse> {
  const { data } = await api.put<CardTermsResponse>(
    `/accounts/${accountId}/card-terms`,
    payload,
  );
  return data;
}

export async function parseCardDocument(
  uri: string,
): Promise<CardTermsExtraction> {
  const form = new FormData();
  const filename = uri.split("/").pop() ?? "estado.pdf";
  form.append("file", {
    uri,
    type: "application/pdf",
    name: filename,
  } as unknown as Blob);
  const { data } = await api.post<CardTermsExtraction>(
    "/accounts/parse-card-document",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: LLM_UPLOAD_TIMEOUT_MS,
    },
  );
  return data;
}

export async function fetchCardAnalysis(
  accountId: string,
): Promise<CardAnalysisResponse> {
  const { data } = await api.get<CardAnalysisResponse>(
    `/accounts/${accountId}/card-analysis`,
  );
  return data;
}
