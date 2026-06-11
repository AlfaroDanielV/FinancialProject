import { api } from "./client";

export type DashboardPeriod = "month_current" | "month_prev" | "ytd";

export interface CategoryBreakdownItem {
  category: string;
  income_total: string;
  expense_total: string;
  net_flow: string;
}

export interface DashboardSummary {
  period: DashboardPeriod;
  display_currency: string;
  income_total: string;
  expense_total: string;
  net_flow: string;
  savings_rate: string | null;
  balance_total: string;
  transaction_count: number;
  accounts_count: number;
  active_goals_count: number;
  category_breakdown: CategoryBreakdownItem[];
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
