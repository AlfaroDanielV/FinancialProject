import { api } from "./client";
export type {
  TransactionResponse,
  TransactionListResponse,
} from "./transactions";
import type { TransactionListResponse } from "./transactions";

export interface AccountResponse {
  id: string;
  user_id: string;
  name: string;
  account_type: string;
  currency: string;
  initial_balance: string;
  is_active: boolean;
  archived: boolean;
  created_at: string;
  current_balance: string | null;
  month_start_balance: string | null;
}

export interface AccountCreate {
  name: string;
  account_type: string;
  currency: "CRC" | "USD";
  initial_balance: string;
}

export interface AccountUpdate {
  name?: string;
  account_type?: string;
  is_active?: boolean;
  archived?: boolean;
}

export const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  checking: "Cuenta corriente",
  savings: "Ahorros",
  credit: "Crédito",
  investment: "Inversión",
};

export async function fetchAccounts(
  includeArchived = false,
): Promise<AccountResponse[]> {
  const { data } = await api.get<AccountResponse[]>("/accounts", {
    params: { include_inactive: includeArchived },
  });
  return data;
}

export async function fetchAccount(id: string): Promise<AccountResponse> {
  const { data } = await api.get<AccountResponse>(`/accounts/${id}`);
  return data;
}

export async function createAccount(
  payload: AccountCreate,
): Promise<AccountResponse> {
  const { data } = await api.post<AccountResponse>("/accounts", payload);
  return data;
}

export async function updateAccount(
  id: string,
  payload: AccountUpdate,
): Promise<AccountResponse> {
  const { data } = await api.patch<AccountResponse>(`/accounts/${id}`, payload);
  return data;
}

export async function archiveAccount(id: string): Promise<AccountResponse> {
  const { data } = await api.delete<AccountResponse>(`/accounts/${id}`);
  return data;
}

// ── Phase 7b B2: TRUE hard delete ────────────────────────────────────────────

export interface AccountDeleteImpact {
  transactions: number;
  transfers: number;
  debts_detached: number;
  bills_detached: number;
  goals_detached: number;
}

export interface AccountHardDeleteResponse {
  account: AccountResponse;
  impact: AccountDeleteImpact;
}

export async function fetchDeleteImpact(
  id: string,
): Promise<AccountDeleteImpact> {
  const { data } = await api.get<AccountDeleteImpact>(
    `/accounts/${id}/delete-impact`,
  );
  return data;
}

/** Permanent delete — `confirmName` must equal the exact account name. */
export async function hardDeleteAccount(
  id: string,
  confirmName: string,
): Promise<AccountHardDeleteResponse> {
  const { data } = await api.delete<AccountHardDeleteResponse>(
    `/accounts/${id}`,
    { params: { hard: true, confirm: confirmName } },
  );
  return data;
}

export async function fetchAccountTransactions(
  accountId: string,
  cursor?: string,
): Promise<TransactionListResponse> {
  const { data } = await api.get<TransactionListResponse>("/transactions", {
    params: {
      account_id: accountId,
      sort_by: "date",
      sort_dir: "desc",
      limit: 30,
      ...(cursor ? { cursor } : {}),
    },
  });
  return data;
}
