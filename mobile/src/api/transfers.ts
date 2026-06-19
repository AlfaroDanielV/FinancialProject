/**
 * Phase 7b B1 — transfers between own accounts.
 *
 * A credit-card payment IS a transfer (source account → credit account): the
 * two legs share a transfer_id and are excluded from income/expense math, so
 * paying the card never double-counts spending.
 */
import { api } from "./client";

export interface TransferCreate {
  from_account_id: string;
  to_account_id: string;
  amount: string;
  currency: string;
  fx_rate?: string | null;
  occurred_at?: string | null;
  notes?: string | null;
}

export interface TransferResponse {
  id: string;
  user_id: string;
  from_account_id: string;
  to_account_id: string;
  amount: string;
  currency: string;
  fx_rate: string | null;
  occurred_at: string;
  notes: string | null;
  created_at: string;
  debit_transaction_id: string | null;
  credit_transaction_id: string | null;
}

export async function createTransfer(
  payload: TransferCreate,
): Promise<TransferResponse> {
  const { data } = await api.post<TransferResponse>("/transfers", payload);
  return data;
}

export async function fetchTransfers(
  accountId?: string,
  limit = 50,
  offset = 0,
): Promise<TransferResponse[]> {
  const { data } = await api.get<TransferResponse[]>("/transfers", {
    params: {
      limit,
      offset,
      ...(accountId ? { account_id: accountId } : {}),
    },
  });
  return data;
}
