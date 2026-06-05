import { api } from "./client";

// Envelope budgeting ("Sobres") — spending-cap buckets. Spend is computed
// live on the backend (no stored balance); this client just renders it.

export type EnvelopeClass = "needs" | "wants" | "savings" | "investing";

export interface EnvelopeResponse {
  id: string;
  user_id: string;
  name: string;
  envelope_class: EnvelopeClass;
  limit_amount: number;
  currency: string;
  period: string;
  is_active: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface EnvelopeCreate {
  name: string;
  envelope_class: EnvelopeClass;
  limit_amount: number;
  currency?: "CRC" | "USD";
}

/**
 * Minimal shape the edit sheet needs. Both a full `EnvelopeResponse` and an
 * `EnvelopeSummaryItem` satisfy it, so the home-tab section can open the editor
 * straight from the summary it already has — no second fetch, no dead taps.
 */
export type EnvelopeEditable = Pick<
  EnvelopeResponse,
  "id" | "name" | "envelope_class" | "limit_amount" | "currency"
>;

export interface EnvelopeUpdate {
  name?: string;
  envelope_class?: EnvelopeClass;
  limit_amount?: number;
  is_active?: boolean;
  archived?: boolean;
}

// ── summary (home-tab feed) ─────────────────────────────────────────────────

export interface EnvelopeSummaryItem {
  id: string;
  name: string;
  envelope_class: EnvelopeClass;
  currency: string;
  limit_amount: number;
  spent: number;
  remaining: number;
  pct: number; // spent / limit; clamp in UI; > 1 = over
  over_limit: boolean;
}

export interface EnvelopeClassSubtotal {
  envelope_class: EnvelopeClass;
  limit_total: number;
  spent_total: number;
  over_limit: boolean;
}

export interface EnvelopeSummaryResponse {
  period: string;
  currency: string;
  envelopes: EnvelopeSummaryItem[];
  by_class: EnvelopeClassSubtotal[];
  total_limit: number;
  monthly_income: number | null;
}

// ── display metadata (CR Spanish, earth-tone palette per theme.ts) ──────────

export const ENVELOPE_CLASS_ORDER: EnvelopeClass[] = [
  "needs",
  "wants",
  "savings",
  "investing",
];

export const ENVELOPE_CLASS_LABELS: Record<EnvelopeClass, string> = {
  needs: "Necesidades",
  wants: "Gustos",
  savings: "Ahorro",
  investing: "Inversión",
};

export const ENVELOPE_CLASS_COLORS: Record<EnvelopeClass, string> = {
  needs: "#4A6741", // sage green
  wants: "#7A6228", // ochre
  savings: "#3B6B7A", // teal
  investing: "#6B4A7A", // plum
};

// The bar shows money LEFT: it starts full (100%) and drains with each expense.
// It goes red in the last 5% (and stays red once over the limit).
export const ENVELOPE_LOW_THRESHOLD = 0.05;

export interface EnvelopeProgress {
  remaining: number; // limit − spent, in the envelope's currency (may be < 0)
  fraction: number; // money left as 0..1 (clamped) — the bar fill width
  low: boolean; // remaining ≤ 5% of limit → red
}

export function envelopeProgress(
  item: Pick<EnvelopeSummaryItem, "limit_amount" | "remaining">
): EnvelopeProgress {
  const limit = item.limit_amount;
  const remaining = item.remaining;
  const fraction = limit > 0 ? Math.max(0, Math.min(remaining / limit, 1)) : 0;
  const low =
    limit > 0 ? remaining <= ENVELOPE_LOW_THRESHOLD * limit : remaining <= 0;
  return { remaining, fraction, low };
}

// ── API ─────────────────────────────────────────────────────────────────────

export async function fetchEnvelopes(
  includeArchived = false
): Promise<EnvelopeResponse[]> {
  const res = await api.get<EnvelopeResponse[]>("/envelopes", {
    params: includeArchived ? { include_archived: true } : undefined,
  });
  return res.data;
}

export async function fetchEnvelopeSummary(): Promise<EnvelopeSummaryResponse> {
  const res = await api.get<EnvelopeSummaryResponse>("/envelopes/summary");
  return res.data;
}

export async function createEnvelope(
  payload: EnvelopeCreate
): Promise<EnvelopeResponse> {
  const res = await api.post<EnvelopeResponse>("/envelopes", payload);
  return res.data;
}

export async function updateEnvelope(
  id: string,
  payload: EnvelopeUpdate
): Promise<EnvelopeResponse> {
  const res = await api.patch<EnvelopeResponse>(`/envelopes/${id}`, payload);
  return res.data;
}

/** Soft archive — hides the envelope and stops it counting; tagged
 * transactions keep their link. */
export async function archiveEnvelope(id: string): Promise<EnvelopeResponse> {
  const res = await api.delete<EnvelopeResponse>(`/envelopes/${id}`);
  return res.data;
}

/** Hard delete — permanently removes the envelope. Tagged transactions are
 * unlinked (envelope_id → NULL), never deleted. */
export async function deleteEnvelope(id: string): Promise<EnvelopeResponse> {
  const res = await api.delete<EnvelopeResponse>(`/envelopes/${id}`, {
    params: { hard: true },
  });
  return res.data;
}

/**
 * Assign (or clear, with `null`) the envelope an expense counts against.
 * Goes through PATCH /transactions/{id} — the backend validates the envelope
 * belongs to the caller and is not archived (400 "Sobre inválido." otherwise),
 * and 409s on shadow/transfer/archived rows.
 */
export async function assignTransactionEnvelope(
  transactionId: string,
  envelopeId: string | null
): Promise<void> {
  await api.patch(`/transactions/${transactionId}`, {
    envelope_id: envelopeId,
  });
}
