import { api } from "./client";
import { Transaction, TransactionList } from "../schemas/entities";

export type TransactionKind = "all" | "income" | "expense" | "transfer";
export type TransactionSortBy = "date" | "amount" | "category";
export type TransactionSortDir = "asc" | "desc";

export interface TransactionFilters {
  account_id?: string;
  account_ids?: string[];
  category_id?: string;
  category_ids?: string[];
  from_date?: string;
  to_date?: string;
  kind?: TransactionKind;
  currency?: string;
  min_amount?: string;
  max_amount?: string;
  q?: string;
  sort_by?: TransactionSortBy;
  sort_dir?: TransactionSortDir;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
  cursor?: string;
}

function toQueryParams(filters: TransactionFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.limit !== undefined) params.append("limit", String(filters.limit));
  if (filters.offset !== undefined && !filters.cursor)
    params.append("offset", String(filters.offset));
  if (filters.cursor) params.append("cursor", filters.cursor);
  if (filters.account_id) params.append("account_id", filters.account_id);
  filters.account_ids?.forEach((id) => params.append("account_ids", id));
  if (filters.category_id) params.append("category_id", filters.category_id);
  filters.category_ids?.forEach((id) => params.append("category_ids", id));
  if (filters.from_date) params.append("from_date", filters.from_date);
  if (filters.to_date) params.append("to_date", filters.to_date);
  if (filters.kind && filters.kind !== "all") params.append("kind", filters.kind);
  if (filters.currency) params.append("currency", filters.currency);
  if (filters.min_amount !== undefined && filters.min_amount !== "")
    params.append("min_amount", filters.min_amount);
  if (filters.max_amount !== undefined && filters.max_amount !== "")
    params.append("max_amount", filters.max_amount);
  if (filters.q) params.append("q", filters.q);
  if (filters.sort_by) params.append("sort_by", filters.sort_by);
  if (filters.sort_dir) params.append("sort_dir", filters.sort_dir);
  if (filters.include_archived) params.append("include_archived", "true");
  return params;
}

export async function fetchTransactions(filters: TransactionFilters = {}) {
  const params = toQueryParams({
    limit: 25,
    ...filters,
  });
  const resp = await api.get(`/transactions?${params.toString()}`);
  return TransactionList.parse(resp.data);
}

export interface TransactionUpdatePayload {
  amount?: string;
  merchant?: string;
  description?: string;
  category?: string;
  category_id?: string | null;
  transaction_date?: string;
}

export async function updateTransaction(
  id: string,
  payload: TransactionUpdatePayload,
) {
  const resp = await api.patch(`/transactions/${id}`, payload);
  return Transaction.parse(resp.data);
}

export interface BulkResponse {
  updated: number;
}

export async function bulkArchiveTransactions(transactionIds: string[]) {
  const resp = await api.post("/transactions/bulk/archive", {
    transaction_ids: transactionIds,
  });
  return resp.data as BulkResponse;
}

export async function bulkRestoreTransactions(transactionIds: string[]) {
  const resp = await api.post("/transactions/bulk/restore", {
    transaction_ids: transactionIds,
  });
  return resp.data as BulkResponse;
}

export async function bulkCategorizeTransactions(
  transactionIds: string[],
  categoryId: string | null,
) {
  const resp = await api.post("/transactions/bulk/categorize", {
    transaction_ids: transactionIds,
    category_id: categoryId,
  });
  return resp.data as BulkResponse;
}

export async function downloadTransactionsCsv(filters: TransactionFilters = {}) {
  const params = toQueryParams(filters);
  const resp = await api.get(`/transactions/export?${params.toString()}`, {
    responseType: "blob",
  });
  const blob = resp.data as Blob;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `transacciones-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
