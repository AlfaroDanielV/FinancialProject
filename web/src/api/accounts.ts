import { api } from "./client";
import {
  Account,
  AccountList,
  AccountType,
  UserCategoryList,
} from "../schemas/entities";

export async function fetchAccounts(includeArchived = false) {
  const resp = await api.get("/accounts", {
    params: includeArchived ? { include_inactive: true } : undefined,
  });
  return AccountList.parse(resp.data);
}

export async function fetchAccount(id: string) {
  const resp = await api.get(`/accounts/${id}`);
  return Account.parse(resp.data);
}

export interface AccountUpdatePayload {
  name?: string;
  account_type?: AccountType;
  archived?: boolean;
  is_active?: boolean;
}

export async function updateAccount(id: string, payload: AccountUpdatePayload) {
  const resp = await api.patch(`/accounts/${id}`, payload);
  return Account.parse(resp.data);
}

export async function archiveAccount(id: string) {
  return updateAccount(id, { archived: true, is_active: false });
}

export async function restoreAccount(id: string) {
  return updateAccount(id, { archived: false, is_active: true });
}

export async function fetchUserCategories() {
  const resp = await api.get("/categories");
  return UserCategoryList.parse(resp.data);
}

export interface TransferPayload {
  from_account_id: string;
  to_account_id: string;
  amount: string;
  currency: "CRC" | "USD";
  fx_rate?: string;
  occurred_at: string;
  notes?: string;
}

export async function createTransfer(payload: TransferPayload) {
  const resp = await api.post("/transfers", payload);
  return resp.data as { id: string };
}
