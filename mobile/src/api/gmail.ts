/**
 * Native Gmail surface — connect, sender whitelist, shadow review.
 *
 * Thin wrappers over /api/v1/gmail/*. OAuth connect reuses the existing
 * /oauth/start + /status (poll-based: open auth_url in a browser, then poll
 * status). Senders + shadow review hit the Phase-6-native endpoints.
 */
import { api } from "./client";
import type { TransactionResponse } from "./transactions";

export interface GmailStatus {
  connected: boolean;
  granted_at: string | null;
  activated_at: string | null;
  revoked_at: string | null;
  last_refresh_at: string | null;
  scopes: string[];
}

export interface OAuthStart {
  auth_url: string;
  expires_at: string;
  expires_in_seconds: number;
}

export interface GmailSender {
  id: string;
  sender_email: string;
  bank_name: string | null;
  added_at: string;
}

export interface SenderAddResult {
  added: string[];
  skipped: string[];
  at_cap: boolean;
}

export interface ShadowConfirmItem {
  id: string;
  amount?: number;
  merchant?: string;
  description?: string;
  category?: string;
  transaction_date?: string;
}

export interface GmailRunInfo {
  started_at: string;
  finished_at: string | null;
  running: boolean;
  mode: string;
  messages_scanned: number;
  transactions_created: number;
  transactions_matched: number;
  has_errors: boolean;
}

export interface GmailScanStatus {
  connected: boolean;
  revoked: boolean;
  senders_count: number;
  latest_run: GmailRunInfo | null;
}

export const SENDER_CAP = 8;

export async function oauthStart(): Promise<OAuthStart> {
  const { data } = await api.post<OAuthStart>("/gmail/oauth/start");
  return data;
}

export async function fetchGmailStatus(): Promise<GmailStatus> {
  const { data } = await api.get<GmailStatus>("/gmail/status");
  return data;
}

export async function scanGmail(days = 30): Promise<{ queued: boolean; days: number }> {
  const { data } = await api.post("/gmail/scan", null, { params: { days } });
  return data;
}

export async function fetchScanStatus(): Promise<GmailScanStatus> {
  const { data } = await api.get<GmailScanStatus>("/gmail/scan/status");
  return data;
}

export async function fetchSenders(): Promise<GmailSender[]> {
  const { data } = await api.get<GmailSender[]>("/gmail/senders");
  return data;
}

export async function addSenders(
  bankName: string | null,
  emails: string,
): Promise<SenderAddResult> {
  const { data } = await api.post<SenderAddResult>("/gmail/senders", {
    bank_name: bankName,
    emails,
  });
  return data;
}

export async function removeSender(id: string): Promise<void> {
  await api.delete(`/gmail/senders/${id}`);
}

export async function fetchShadow(): Promise<TransactionResponse[]> {
  const { data } = await api.get<TransactionResponse[]>("/gmail/shadow");
  return data;
}

export async function confirmShadow(
  items: ShadowConfirmItem[],
): Promise<{ confirmed: number }> {
  const { data } = await api.post("/gmail/shadow/confirm", { items });
  return data;
}

export async function discardShadow(ids: string[]): Promise<{ discarded: number }> {
  const { data } = await api.post("/gmail/shadow/discard", { ids });
  return data;
}
