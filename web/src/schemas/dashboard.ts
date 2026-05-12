import { z } from "zod";

const NumberLike = z.union([z.number(), z.string()]).transform((value, ctx) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Número inválido." });
    return z.NEVER;
  }
  return parsed;
});

export const DashboardPeriod = z.enum([
  "month_current",
  "month_prev",
  "ytd",
  "last_6_months",
]);
export type DashboardPeriod = z.infer<typeof DashboardPeriod>;

export const CategoryBreakdownItem = z.object({
  category: z.string(),
  income_total: NumberLike,
  expense_total: NumberLike,
  net_flow: NumberLike,
});
export type CategoryBreakdownItem = z.infer<typeof CategoryBreakdownItem>;

export const DashboardSummary = z.object({
  period: DashboardPeriod,
  display_currency: z.string(),
  income_total: NumberLike,
  expense_total: NumberLike,
  net_flow: NumberLike,
  savings_rate: NumberLike.nullable(),
  balance_total: NumberLike,
  transaction_count: z.number().int(),
  transfer_rows_excluded: z.number().int(),
  accounts_count: z.number().int(),
  active_goals_count: z.number().int(),
  category_breakdown: z.array(CategoryBreakdownItem),
});
export type DashboardSummary = z.infer<typeof DashboardSummary>;

export const CashFlowPoint = z.object({
  month: z.string(),
  income_total: NumberLike,
  expense_total: NumberLike,
  net_flow: NumberLike,
});
export type CashFlowPoint = z.infer<typeof CashFlowPoint>;

export const CashFlowResponse = z.object({
  display_currency: z.string(),
  points: z.array(CashFlowPoint),
});
export type CashFlowResponse = z.infer<typeof CashFlowResponse>;

export const DailyCashFlowPoint = z.object({
  date: z.string(),
  income_total: NumberLike,
  expense_total: NumberLike,
  net_flow: NumberLike,
});
export type DailyCashFlowPoint = z.infer<typeof DailyCashFlowPoint>;

export const DailyCashFlowResponse = z.object({
  display_currency: z.string(),
  points: z.array(DailyCashFlowPoint),
});
export type DailyCashFlowResponse = z.infer<typeof DailyCashFlowResponse>;

export const UserMe = z.object({
  id: z.string().uuid(),
  email: z.string(),
  full_name: z.string(),
  currency: z.string(),
  display_currency: z.string(),
});
export type UserMe = z.infer<typeof UserMe>;

export const Goal = z.object({
  id: z.string().uuid(),
  name: z.string(),
  target_amount: NumberLike,
  target_currency: z.string(),
  current_amount: NumberLike,
  target_date: z.string().nullable(),
  status: z.string(),
});
export type Goal = z.infer<typeof Goal>;

export const GoalList = z.array(Goal);

export const UpcomingFeedItem = z.object({
  item_type: z.enum(["bill", "event"]),
  id: z.string().uuid(),
  date: z.string(),
  title: z.string(),
  amount: z.number().nullable(),
  currency: z.string(),
  status: z.string().nullable().optional(),
  category: z.string().nullable().optional(),
  provider: z.string().nullable().optional(),
  recurring_bill_id: z.string().uuid().nullable().optional(),
  is_overdue: z.boolean(),
});
export type UpcomingFeedItem = z.infer<typeof UpcomingFeedItem>;

export const UpcomingFeedResponse = z.object({
  items: z.array(UpcomingFeedItem),
  from_date: z.string(),
  to_date: z.string(),
});
export type UpcomingFeedResponse = z.infer<typeof UpcomingFeedResponse>;

export const UpcomingDebtPayment = z.object({
  debt_id: z.string().uuid(),
  name: z.string(),
  debt_type: z.string(),
  minimum_payment: z.number(),
  payment_due_day: z.number().int(),
  current_balance: z.number(),
});
export type UpcomingDebtPayment = z.infer<typeof UpcomingDebtPayment>;

export const DebtOverview = z.object({
  total_debt: z.number(),
  total_minimum_monthly: z.number(),
  debts_by_type: z.record(z.number()),
  upcoming_payments: z.array(UpcomingDebtPayment),
});
export type DebtOverview = z.infer<typeof DebtOverview>;

export const DashboardInsight = z.object({
  id: z.string(),
  insight_type: z.string(),
  group: z.string(),
  description: z.string(),
  confidence: NumberLike,
  source: z.string(),
  valid_until: z.string(),
  user_locked: z.boolean(),
  updated_at: z.string(),
});
export type DashboardInsight = z.infer<typeof DashboardInsight>;

export const DashboardInsightsResponse = z.object({
  items: z.array(DashboardInsight),
});
export type DashboardInsightsResponse = z.infer<typeof DashboardInsightsResponse>;
