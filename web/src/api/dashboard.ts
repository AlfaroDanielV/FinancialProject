import { api } from "./client";
import {
  CashFlowResponse,
  DailyCashFlowResponse,
  DashboardInsightsResponse,
  DashboardPeriod,
  DashboardSummary,
  DebtOverview,
  GoalList,
  UpcomingFeedResponse,
  UserMe,
} from "../schemas/dashboard";
import { OnboardingStatus } from "../schemas/onboarding";

export function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

export function addDaysIso(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export function currentMonthIso() {
  return todayIso().slice(0, 7);
}

export function previousMonthsIso(countBack: number) {
  const date = new Date();
  date.setMonth(date.getMonth() - countBack);
  return date.toISOString().slice(0, 7);
}

export async function fetchMe() {
  const resp = await api.get("/users/me");
  return UserMe.parse(resp.data);
}

export async function fetchOnboardingStatus() {
  const resp = await api.get("/onboarding/status");
  return OnboardingStatus.parse(resp.data);
}

export async function fetchDashboardSummary(period: DashboardPeriod) {
  const resp = await api.get("/dashboard/summary", { params: { period } });
  return DashboardSummary.parse(resp.data);
}

export async function fetchCashFlow(from: string, to: string) {
  const resp = await api.get("/dashboard/cash-flow", { params: { from, to } });
  return CashFlowResponse.parse(resp.data);
}

export async function fetchDailyCashFlow(from: string, to: string) {
  const resp = await api.get("/dashboard/daily-cash-flow", {
    params: { from, to },
  });
  return DailyCashFlowResponse.parse(resp.data);
}

export async function fetchUpcomingFeed(from: string, to: string) {
  const resp = await api.get("/calendar/upcoming", {
    params: { from, to, include_overdue: true },
  });
  return UpcomingFeedResponse.parse(resp.data);
}

export async function fetchDebtOverview() {
  const resp = await api.get("/debts/overview");
  return DebtOverview.parse(resp.data);
}

export async function fetchGoals() {
  const resp = await api.get("/goals", { params: { status: "active" } });
  return GoalList.parse(resp.data);
}

export async function fetchDashboardInsights() {
  const resp = await api.get("/users/me/insights/summary", {
    params: { limit: 3 },
  });
  return DashboardInsightsResponse.parse(resp.data);
}
