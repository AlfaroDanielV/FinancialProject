import { api } from "./client";

// Envelope budgeting ("Sobres") — spending-cap buckets. Spend is computed
// live on the backend (no stored balance); this client just renders it.

export type EnvelopeClass = "needs" | "wants" | "savings" | "investing";

export interface EnvelopeResponse {
  id: string;
  user_id: string;
  parent_id: string | null;
  depth: number;
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
  // Phase 7a: when set, nests this envelope under parent_id. The child inherits
  // the parent's class + currency server-side (the values above are ignored).
  parent_id?: string | null;
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
  parent_id: string | null;
  depth: number;
  name: string;
  envelope_class: EnvelopeClass;
  currency: string;
  limit_amount: number;
  spent: number; // rolled-up (this node + descendants) — the bar value
  direct_spent: number; // only what's tagged directly to this node
  // B2 fixed-expense attachment: `reserved` = attached bills/debts unpaid this
  // cycle; `available` = limit − reserved − spent (the truly free amount).
  reserved: number;
  available: number;
  remaining: number; // limit − spent (back-compat)
  pct: number; // rolled-up spent / limit; clamp in UI; > 1 = over
  over_limit: boolean;
  // Soft allocation: how this node's budget is split across direct children.
  allocated: number;
  unallocated: number; // limit − allocated; may be negative (over-allocated)
  over_allocated: boolean;
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
  // Phase 7c: grand totals (roots only, summary currency) — the home-screen
  // "Te queda este mes" headline. total_available can be negative.
  total_spent: number;
  total_reserved: number;
  total_available: number;
  monthly_income: number | null;
}

// ── display metadata (CR Spanish, neutral palette per theme.ts v2) ──────────

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
  needs: "#3E7A57", // green
  wants: "#A8763B", // amber
  savings: "#33708A", // steel teal
  investing: "#6E5A93", // muted violet
};

// The bar shows money LEFT: it starts full (100%) and drains with each expense.
// It goes red in the last 5% (and stays red once over the limit).
export const ENVELOPE_LOW_THRESHOLD = 0.05;

export interface EnvelopeProgress {
  available: number; // limit − reserved − spent (the truly free money) — bar fill
  remaining: number; // back-compat alias for `available` (existing callers)
  reserved: number; // attached fixed expenses, unpaid this cycle
  spent: number; // actual spend
  fraction: number; // available / limit as 0..1 (clamped) — the green segment
  reservedFraction: number; // reserved / limit as 0..1 — the reserved segment
  spentFraction: number; // spent / limit as 0..1 — the spent segment
  low: boolean; // available ≤ 5% of limit → red
}

export function envelopeProgress(
  item: Pick<EnvelopeSummaryItem, "limit_amount" | "remaining"> &
    Partial<Pick<EnvelopeSummaryItem, "spent" | "reserved" | "available">>
): EnvelopeProgress {
  const limit = item.limit_amount;
  const reserved = item.reserved ?? 0;
  const spent = item.spent ?? 0;
  // Back-compat: callers that only pass `remaining` (limit − spent) get that as
  // `available` (no reserved segment) until they pass the full summary item.
  const available = item.available ?? item.remaining;
  const clamp = (v: number) => (limit > 0 ? Math.max(0, Math.min(v / limit, 1)) : 0);
  return {
    available,
    remaining: available,
    reserved,
    spent,
    fraction: clamp(available),
    reservedFraction: clamp(reserved),
    spentFraction: clamp(spent),
    low: limit > 0 ? available <= ENVELOPE_LOW_THRESHOLD * limit : available <= 0,
  };
}

// ── nesting (Phase 7a) ───────────────────────────────────────────────────────
// The summary + list endpoints return a FLAT list linked by parent_id. Build
// the tree client-side, then flatten in pre-order so a parent renders right
// above its sub-sobres (indent by `depth`).

interface Nestable {
  id: string;
  parent_id: string | null;
}

export function flattenEnvelopeTree<T extends Nestable>(items: T[]): T[] {
  const childrenOf = new Map<string | null, T[]>();
  const ids = new Set(items.map((it) => it.id));
  for (const it of items) {
    // Treat a node whose parent isn't in this set (e.g. archived) as a root.
    const key = it.parent_id && ids.has(it.parent_id) ? it.parent_id : null;
    const list = childrenOf.get(key) ?? [];
    list.push(it);
    childrenOf.set(key, list);
  }
  const out: T[] = [];
  const walk = (parentId: string | null) => {
    for (const it of childrenOf.get(parentId) ?? []) {
      out.push(it);
      walk(it.id);
    }
  };
  walk(null);
  return out;
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
