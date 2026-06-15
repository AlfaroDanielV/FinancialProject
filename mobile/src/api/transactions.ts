/**
 * Phase 6f B9 — Global transactions API.
 *
 * Single source of truth for TransactionResponse + TransactionListResponse
 * (re-exported from api/accounts.ts for backwards compatibility).
 *
 * Sort is intentionally locked to date desc: the backend only returns a
 * next_cursor for sort_by=date. Switching sort would invalidate any cached
 * cursor and produce silent wrong results.
 */
import { api } from "./client";

export interface TransactionResponse {
  id: string;
  account_id: string | null;
  category_id: string | null;
  envelope_id: string | null;
  amount: number;
  currency: string;
  merchant: string | null;
  description: string | null;
  category: string | null;
  transaction_date: string;
  source: string;
  status: string;
  archived: boolean;
}

export interface TransactionListResponse {
  items: TransactionResponse[];
  next_cursor: string | null;
}

export type TransactionKind = "all" | "income" | "expense";

export interface TransactionFilters {
  kind: TransactionKind;
  accountId: string | null;
  includeArchived: boolean;
}

export const DEFAULT_FILTERS: TransactionFilters = {
  kind: "all",
  accountId: null,
  includeArchived: false,
};

const PAGE_SIZE = 30;

export async function fetchTransactions(
  filters: TransactionFilters,
  cursor?: string,
): Promise<TransactionListResponse> {
  const params: Record<string, string | boolean> = {
    sort_by: "date",
    sort_dir: "desc",
    limit: String(PAGE_SIZE),
  };
  if (filters.kind !== "all") {
    params.kind = filters.kind;
  }
  if (filters.accountId) {
    params.account_id = filters.accountId;
  }
  if (filters.includeArchived) {
    params.include_archived = true;
  }
  if (cursor) {
    params.cursor = cursor;
  }
  const { data } = await api.get<TransactionListResponse>("/transactions", { params });
  return data;
}

/**
 * Current-month confirmed expenses (date desc), for the envelope bulk-assign
 * view. Envelopes reset monthly, so we only surface this month's spend. One
 * page (no pagination) — months rarely exceed the limit, and stragglers can
 * still be tagged from the per-transaction edit sheet.
 */
export async function fetchMonthExpenses(): Promise<TransactionResponse[]> {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), 1)
    .toISOString()
    .slice(0, 10);
  const today = now.toISOString().slice(0, 10);
  const { data } = await api.get<TransactionListResponse>("/transactions", {
    params: {
      kind: "expense",
      from_date: first,
      to_date: today,
      sort_by: "date",
      sort_dir: "desc",
      limit: 200,
    },
  });
  // Only confirmed rows can be tagged — shadow rows 409 on PATCH. (kind=expense
  // already excludes transfer legs; archived is excluded by default.)
  return data.items.filter((t) => t.status === "confirmed");
}

export async function archiveTransaction(id: string): Promise<void> {
  // Body field is `transaction_ids` (TransactionBulkArchive), not `ids`.
  await api.post("/transactions/bulk/archive", { transaction_ids: [id] });
}

export async function restoreTransaction(id: string): Promise<void> {
  await api.post("/transactions/bulk/restore", { transaction_ids: [id] });
}

/** Editable fields on a confirmed, non-archived, non-transfer transaction. */
export interface TransactionUpdate {
  amount?: number;
  merchant?: string | null;
  description?: string | null;
  category?: string | null;
  // FK to a user_categories row (the Categorías screen). The edit dropdown sets
  // this alongside `category` (name) so the displayed string and the FK stay in
  // sync. Backend validates it belongs to the caller and is active (400 else).
  category_id?: string | null;
  // Reassign the movement to another account. Backend validates it belongs to
  // the caller and is active (400 else); if the destination account's currency
  // differs, the backend converts the amount and rewrites the row's currency.
  account_id?: string | null;
  transaction_date?: string; // YYYY-MM-DD
  // Envelope budgeting: which spending-cap envelope this expense counts
  // against. null clears it. Backend 400s on a foreign/archived envelope.
  envelope_id?: string | null;
}

export async function updateTransaction(
  id: string,
  payload: TransactionUpdate,
): Promise<TransactionResponse> {
  const { data } = await api.patch<TransactionResponse>(
    `/transactions/${id}`,
    payload,
  );
  return data;
}

/**
 * Permanently delete a movement (NOT archive). Distinct from
 * archiveTransaction, which is a reversible soft delete. The backend 409s on
 * shadow rows, transfer legs, goal flows, and bill/debt-linked rows — surface
 * `error.response.data.detail` in an Alert.
 */
export async function deleteTransaction(id: string): Promise<void> {
  await api.delete(`/transactions/${id}`);
}
