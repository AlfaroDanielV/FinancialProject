import { api } from "./client";

export interface RecurringBillResponse {
  id: string;
  user_id: string;
  name: string;
  provider: string | null;
  category: string | null;
  amount_expected: number | null;
  currency: string;
  is_variable_amount: boolean;
  account_id: string | null;
  frequency: string;
  day_of_month: number | null;
  recurrence_rule: string | null;
  start_date: string;
  end_date: string | null;
  lead_time_days: number;
  is_active: boolean;
  notes: string | null;
  envelope_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface BillOccurrenceResponse {
  id: string;
  recurring_bill_id: string;
  due_date: string;
  amount_expected: number | null;
  amount_paid: number | null;
  status: string;
  paid_at: string | null;
  transaction_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface MarkPaidPayload {
  amount_paid?: number;
  paid_at?: string;
  account_id?: string;
  description?: string;
  idempotency_key: string;
}

export interface MarkPaidResponse {
  occurrence: BillOccurrenceResponse;
  transaction_id: string;
  amount_delta_pct: number | null;
  warning: string | null;
  idempotent_replay: boolean;
}

export const FREQUENCY_LABELS: Record<string, string> = {
  weekly: "Semanal",
  biweekly: "Quincenal",
  monthly: "Mensual",
  bimonthly: "Bimestral",
  quarterly: "Trimestral",
  semiannual: "Semestral",
  annual: "Anual",
  custom: "Personalizado",
};

export const ACTIONABLE_STATUSES = new Set(["pending", "overdue", "partially_paid"]);

// 'custom' (RRULE) is excluded from the form — it needs a recurrence_rule the
// simple sheet doesn't gather (same exclusion the conversational creator makes).
export const BILL_FREQUENCIES = [
  "weekly",
  "biweekly",
  "monthly",
  "bimonthly",
  "quarterly",
  "semiannual",
  "annual",
] as const;
export type BillFrequency = (typeof BILL_FREQUENCIES)[number];

export interface RecurringBillCreate {
  name: string;
  category: string;
  amount_expected?: number | null;
  currency: string;
  is_variable_amount: boolean;
  frequency: BillFrequency;
  day_of_month?: number | null;
  start_date: string;
  provider?: string | null;
  account_id?: string | null;
  notes?: string | null;
}

export interface RecurringBillUpdate {
  name?: string;
  category?: string;
  amount_expected?: number | null;
  is_variable_amount?: boolean;
  frequency?: BillFrequency;
  day_of_month?: number | null;
  start_date?: string;
  provider?: string | null;
  account_id?: string | null;
  is_active?: boolean;
  notes?: string | null;
  // Fixed-expense attachment: a UUID attaches to that sobre; null detaches.
  envelope_id?: string | null;
}

/** Attach (or, with null, detach) a recurring bill to an envelope. */
export function attachBillToEnvelope(
  id: string,
  envelopeId: string | null
): Promise<RecurringBillResponse> {
  return updateRecurringBill(id, { envelope_id: envelopeId });
}

export function newIdempotencyKey(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 18)}`;
}

export async function fetchRecurringBills(
  includeInactive = false,
): Promise<RecurringBillResponse[]> {
  const { data } = await api.get<RecurringBillResponse[]>("/recurring-bills", {
    params: includeInactive ? { is_active: false } : { is_active: true },
  });
  return data;
}

export async function fetchBillOccurrences(filters: {
  status?: string;
  from_date?: string;
  to_date?: string;
  recurring_bill_id?: string;
}): Promise<BillOccurrenceResponse[]> {
  const { data } = await api.get<BillOccurrenceResponse[]>("/bill-occurrences", {
    params: filters,
  });
  return data;
}

export async function markBillPaid(
  billId: string,
  payload: MarkPaidPayload,
): Promise<MarkPaidResponse> {
  const { data } = await api.post<MarkPaidResponse>(
    `/recurring-bills/${billId}/mark-paid`,
    payload,
  );
  return data;
}

export async function createRecurringBill(
  payload: RecurringBillCreate,
): Promise<RecurringBillResponse> {
  const { data } = await api.post<RecurringBillResponse>("/recurring-bills", payload);
  return data;
}

export async function updateRecurringBill(
  billId: string,
  payload: RecurringBillUpdate,
): Promise<RecurringBillResponse> {
  const { data } = await api.patch<RecurringBillResponse>(
    `/recurring-bills/${billId}`,
    payload,
  );
  return data;
}

/** CR category suggestions (single source of truth — the backend list). */
export async function fetchBillCategories(): Promise<string[]> {
  const { data } = await api.get<{ categories: string[] }>("/onboarding/categories");
  return data.categories;
}

export async function pauseBill(billId: string): Promise<RecurringBillResponse> {
  const { data } = await api.patch<RecurringBillResponse>(
    `/recurring-bills/${billId}`,
    { is_active: false },
  );
  return data;
}

export async function resumeBill(billId: string): Promise<RecurringBillResponse> {
  const { data } = await api.patch<RecurringBillResponse>(
    `/recurring-bills/${billId}`,
    { is_active: true },
  );
  return data;
}

export async function archiveBill(billId: string): Promise<RecurringBillResponse> {
  const { data } = await api.delete<RecurringBillResponse>(
    `/recurring-bills/${billId}`,
  );
  return data;
}
