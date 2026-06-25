import { api } from "./client";

export type DashboardPeriod = "month_current" | "month_prev" | "ytd";

export interface CategoryBreakdownItem {
  category: string;
  income_total: string;
  expense_total: string;
  net_flow: string;
}

export interface CurrencyBalance {
  currency: string;
  available: string;
  savings: string;
}

export interface DashboardSummary {
  period: DashboardPeriod;
  display_currency: string;
  income_total: string;
  expense_total: string;
  net_flow: string;
  savings_rate: string | null;
  balance_total: string;
  // Phase 7h: savings is "plata apartada". `available_balance` excludes
  // savings accounts (the home-screen figure); `savings_balance` is shown
  // separately. `balance_total` stays for back-compat.
  available_balance: string;
  savings_balance: string;
  // D3: balances in currencies OTHER than display_currency, shown apart
  // («(+ $Y en cuentas en dólares)»). Empty for single-currency users.
  other_currency_balances: CurrencyBalance[];
  transaction_count: number;
  accounts_count: number;
  active_goals_count: number;
  category_breakdown: CategoryBreakdownItem[];
}

// ── Phase 7h: cash-flow time series for the Analytics screen ────────────────

export interface CashFlowPoint {
  month: string; // "YYYY-MM"
  income_total: string;
  expense_total: string;
  net_flow: string;
}

export interface CashFlowResponse {
  display_currency: string;
  points: CashFlowPoint[];
}

export interface UpcomingFeedItem {
  // "debt" = projected loan cuota; "card_payment" = card minimum (7b B5).
  item_type: "bill" | "event" | "debt" | "card_payment";
  id: string;
  date: string;
  title: string;
  amount: number | null;
  currency: string;
  status: string | null;
  category: string | null;
  provider: string | null;
  debt_id: string | null;
  account_id: string | null;
  // 'minimum' | 'full' when item_type === "card_payment" — "de contado" vs "mínimo".
  payment_mode: "minimum" | "full" | null;
  is_overdue: boolean;
}

export interface UpcomingFeedResponse {
  items: UpcomingFeedItem[];
  from_date: string;
  to_date: string;
}

export async function fetchDashboardSummary(
  period: DashboardPeriod,
): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/dashboard/summary", {
    params: { period },
  });
  return data;
}

export async function fetchUpcomingFeed(
  fromDate: string,
  toDate: string,
): Promise<UpcomingFeedResponse> {
  const { data } = await api.get<UpcomingFeedResponse>("/calendar/upcoming", {
    params: { from: fromDate, to: toDate, include_overdue: true },
  });
  return data;
}

/** Phase 7h — monthly cash-flow series for the Analytics screen.
 *  `from`/`to` are "YYYY-MM" (backend caps at 36 months). */
export async function fetchCashFlow(
  fromMonth: string,
  toMonth: string,
): Promise<CashFlowResponse> {
  const { data } = await api.get<CashFlowResponse>("/dashboard/cash-flow", {
    params: { from: fromMonth, to: toMonth },
  });
  return data;
}
