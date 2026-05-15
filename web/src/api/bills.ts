import { z } from "zod";

import { api } from "./client";
import { RecurringBill, RecurringBillList } from "../schemas/entities";

export const BillOccurrence = z.object({
  id: z.string().uuid(),
  recurring_bill_id: z.string().uuid(),
  due_date: z.string(),
  amount_expected: z.number().nullable(),
  amount_paid: z.number().nullable(),
  status: z.string(),
  paid_at: z.string().nullable(),
  transaction_id: z.string().uuid().nullable(),
  notes: z.string().nullable(),
});
export type BillOccurrence = z.infer<typeof BillOccurrence>;

export const BillOccurrenceList = z.array(BillOccurrence);

export interface BillOccurrenceFilters {
  status?: string;
  from_date?: string;
  to_date?: string;
  recurring_bill_id?: string;
  category?: string;
}

export async function fetchRecurringBills(includeInactive = false) {
  const resp = await api.get("/recurring-bills", {
    params: includeInactive ? { is_active: false } : { is_active: true },
  });
  return RecurringBillList.parse(resp.data);
}

export async function fetchRecurringBill(id: string) {
  const resp = await api.get(`/recurring-bills/${id}`);
  return RecurringBill.parse(resp.data);
}

export async function fetchBillOccurrences(filters: BillOccurrenceFilters = {}) {
  const params: Record<string, string> = {};
  if (filters.status) params.status = filters.status;
  if (filters.from_date) params.from_date = filters.from_date;
  if (filters.to_date) params.to_date = filters.to_date;
  if (filters.recurring_bill_id)
    params.recurring_bill_id = filters.recurring_bill_id;
  if (filters.category) params.category = filters.category;
  const resp = await api.get("/bill-occurrences", { params });
  return BillOccurrenceList.parse(resp.data);
}

export interface MarkPaidPayload {
  amount_paid?: number;
  paid_at?: string;
  account_id?: string;
  category_id?: string;
  description?: string;
  idempotency_key: string;
}

export const MarkPaidResponse = z.object({
  occurrence: BillOccurrence,
  transaction_id: z.string().uuid(),
  amount_delta_pct: z.number().nullable().optional(),
  warning: z.string().nullable().optional(),
  idempotent_replay: z.boolean(),
});
export type MarkPaidResponse = z.infer<typeof MarkPaidResponse>;

export async function markBillPaid(billId: string, payload: MarkPaidPayload) {
  const resp = await api.post(`/recurring-bills/${billId}/mark-paid`, payload);
  return MarkPaidResponse.parse(resp.data);
}

export async function pauseBill(billId: string) {
  const resp = await api.patch(`/recurring-bills/${billId}`, {
    is_active: false,
  });
  return RecurringBill.parse(resp.data);
}

export async function resumeBill(billId: string) {
  const resp = await api.patch(`/recurring-bills/${billId}`, {
    is_active: true,
  });
  return RecurringBill.parse(resp.data);
}

export async function archiveBill(billId: string) {
  const resp = await api.delete(`/recurring-bills/${billId}`);
  return RecurringBill.parse(resp.data);
}
