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
  transaction_date?: string; // YYYY-MM-DD
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
