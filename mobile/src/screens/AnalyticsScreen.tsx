/**
 * Phase 7h — Analytics screen.
 *
 * Reached by tapping the budget-execution (Sobres) card on the home screen.
 * Charts of the financial data already exposed by the backend (no new
 * endpoints): monthly cash-flow, category breakdown, envelope execution by
 * class. Each chart card has an "Explícame" button that opens the in-app chat
 * with a pre-filled, auto-sent question; the LLM explains how to read it + a
 * summary using the existing read-only query tools (rules provide data, LLM
 * explains). On-palette (neutral Rams; color = meaning).
 */
import { ScrollView, StyleSheet, Text, View, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";
import { useQuery } from "@tanstack/react-query";

import {
  fetchCashFlow,
  fetchDashboardSummary,
  type CashFlowResponse,
  type DashboardSummary,
} from "../api/dashboard";
import {
  fetchEnvelopeSummary,
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  ENVELOPE_CLASS_ORDER,
  type EnvelopeClass,
  type EnvelopeSummaryResponse,
} from "../api/envelopes";
import { DonutChart, type DonutSegment } from "../components/charts/DonutChart";
import { LineChart } from "../components/charts/LineChart";
import { formatMoney } from "../lib/format";
import { CardShadow, Colors, Fonts, FontSize, Radius, Spacing } from "../theme";

const MONTHS_ES = [
  "ene", "feb", "mar", "abr", "may", "jun",
  "jul", "ago", "sep", "oct", "nov", "dic",
];

// On-palette rotation for category donut segments (muted, brand-aligned).
const CATEGORY_PALETTE = [
  "#3E7A57", "#A8763B", "#33708A", "#6E5A93", "#8B3A2A", "#52524C", "#2E7D54", "#8A6A1F",
];

function lastNMonths(n: number): { from: string; to: string; labels: string[] } {
  const now = new Date();
  const labels: string[] = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    labels.push(MONTHS_ES[d.getMonth()]);
  }
  const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  const from = new Date(now.getFullYear(), now.getMonth() - (n - 1), 1);
  return { from: ym(from), to: ym(now), labels };
}

function daysLeftInMonth(): number {
  const now = new Date();
  const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  return lastDay - now.getDate();
}

// Minimal cross-tab navigation typing: jump to the Chat tab's Chat screen with
// a prefilled question. (React Navigation resolves the tab from any nested
// screen; we keep the typing local to avoid a full RootParamList.)
type ChatJump = {
  navigate: (tab: "Chat", params: { screen: "Chat"; params: { initialMessage: string } }) => void;
};

function ChartCard({
  title,
  question,
  loading,
  empty,
  children,
}: {
  title: string;
  question: string;
  loading?: boolean;
  empty?: boolean;
  children: React.ReactNode;
}) {
  const nav = useNavigation() as unknown as ChatJump;
  const explain = () =>
    nav.navigate("Chat", { screen: "Chat", params: { initialMessage: question } });

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>{title}</Text>
        <Pressable
          onPress={explain}
          style={({ pressed }) => [styles.explainBtn, pressed && { opacity: 0.6 }]}
          hitSlop={8}
        >
          <Feather name="message-circle" size={13} color={Colors.accent} />
          <Text style={styles.explainText}>Explícame</Text>
        </Pressable>
      </View>
      <View style={styles.cardBody}>
        {loading ? (
          <ActivityIndicator color={Colors.accent} style={{ paddingVertical: Spacing.lg }} />
        ) : empty ? (
          <Text style={styles.emptyText}>Todavía no hay datos suficientes.</Text>
        ) : (
          children
        )}
      </View>
    </View>
  );
}

export function AnalyticsScreen() {
  const range = lastNMonths(6);

  const cashFlow = useQuery<CashFlowResponse>({
    queryKey: ["dashboard", "cash-flow", range.from, range.to],
    queryFn: () => fetchCashFlow(range.from, range.to),
  });
  const summary = useQuery<DashboardSummary>({
    queryKey: ["dashboard", "summary", "month_current"],
    queryFn: () => fetchDashboardSummary("month_current"),
  });
  const envelopes = useQuery<EnvelopeSummaryResponse>({
    queryKey: ["envelopes", "summary"],
    queryFn: fetchEnvelopeSummary,
  });

  const currency = summary.data?.display_currency ?? "CRC";

  // ── cash flow series ──
  const cfPoints = cashFlow.data?.points ?? [];
  const incomeSeries = cfPoints.map((p) => parseFloat(p.income_total));
  const expenseSeries = cfPoints.map((p) => parseFloat(p.expense_total));

  // ── category donut (this month's expenses) ──
  const catSegments: DonutSegment[] = (summary.data?.category_breakdown ?? [])
    .map((c) => ({ label: c.category || "Otros", value: parseFloat(c.expense_total) }))
    .filter((c) => c.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)
    .map((c, i) => ({ ...c, color: CATEGORY_PALETTE[i % CATEGORY_PALETTE.length] }));

  // ── budget hero (Phase 7h: "Te queda este mes" moved here from the home) ──
  const env = envelopes.data;
  const hasEnvelopes = (env?.envelopes.length ?? 0) > 0;
  const budgetAvailable = env?.total_available ?? 0;
  const budgetLimit = env?.total_limit ?? 0;
  const budgetReserved = env?.total_reserved ?? 0;
  const overBudget = budgetAvailable < 0;
  const budgetFraction =
    budgetLimit > 0 ? Math.max(0, Math.min(budgetAvailable / budgetLimit, 1)) : 0;
  const budgetLow = budgetLimit > 0 && budgetAvailable <= 0.05 * budgetLimit;
  const daysLeft = daysLeftInMonth();

  // ── envelope execution by class ──
  const byClass = envelopes.data?.by_class ?? [];
  const classRows = ENVELOPE_CLASS_ORDER.map((cls) => {
    const row = byClass.find((c) => c.envelope_class === cls);
    return row
      ? {
          cls,
          limit: row.limit_total,
          spent: row.spent_total,
          over: row.over_limit,
        }
      : null;
  }).filter((r): r is NonNullable<typeof r> => r != null && r.limit > 0);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.screenHeader}>
        <Text style={styles.screenTitle}>Análisis</Text>
        <Text style={styles.screenSubtitle}>Tu plata, en gráficos</Text>
      </View>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        {/* ── Budget hero: "Te queda este mes" (moved from home in 7h) ── */}
        <View style={[styles.card, styles.heroCard]}>
          <Text style={styles.heroLabel}>TE QUEDA ESTE MES</Text>
          {envelopes.isLoading ? (
            <ActivityIndicator color={Colors.accent} size="small" style={{ marginVertical: Spacing.md }} />
          ) : !hasEnvelopes ? (
            <>
              <Text style={styles.heroAmountEmpty}>—</Text>
              <Text style={styles.heroContext}>
                Creá tus sobres para ver cuánto podés gastar este mes.
              </Text>
            </>
          ) : (
            <>
              <Text style={[styles.heroAmount, overBudget && { color: Colors.expense }]}>
                {overBudget ? "−" : ""}
                {formatMoney(Math.abs(budgetAvailable), currency)}
              </Text>
              <View style={styles.heroBarTrack}>
                <View
                  style={[
                    styles.heroBarFill,
                    { width: `${Math.round(budgetFraction * 100)}%` as `${number}%` },
                    budgetLow && { backgroundColor: Colors.expense },
                  ]}
                />
              </View>
              <Text style={styles.heroContext}>
                de {formatMoney(budgetLimit, currency)} presupuestados ·{" "}
                {daysLeft === 0 ? "último día del mes" : `quedan ${daysLeft} días`}
              </Text>
              {overBudget ? (
                <Text style={styles.heroOverNote}>Te pasaste del presupuesto del mes.</Text>
              ) : budgetReserved > 0 ? (
                <Text style={styles.heroReservedNote}>
                  {formatMoney(budgetReserved, currency)} ya apartado para gastos fijos
                </Text>
              ) : null}
            </>
          )}
        </View>

        {/* ── Cash flow ── */}
        <ChartCard
          title="Flujo de caja (6 meses)"
          question="Explicame mi flujo de caja de los últimos 6 meses: ¿cómo leo este gráfico y qué me muestra en resumen? Usá mis ingresos y gastos por mes."
          loading={cashFlow.isLoading}
          empty={cfPoints.length === 0}
        >
          <LineChart
            labels={range.labels}
            series={[
              { color: Colors.income, values: incomeSeries },
              { color: Colors.expense, values: expenseSeries },
            ]}
          />
          <View style={styles.legendRow}>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: Colors.income }]} />
              <Text style={styles.legendText}>Ingresos</Text>
            </View>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: Colors.expense }]} />
              <Text style={styles.legendText}>Gastos</Text>
            </View>
          </View>
        </ChartCard>

        {/* ── Categories ── */}
        <ChartCard
          title="Gastos por categoría (este mes)"
          question="Explicame en qué se me va la plata por categoría este mes: ¿cómo leo este gráfico y qué me dice en resumen?"
          loading={summary.isLoading}
          empty={catSegments.length === 0}
        >
          <DonutChart data={catSegments} currency={currency} />
        </ChartCard>

        {/* ── Envelope execution by class ── */}
        <ChartCard
          title="Sobres por clase"
          question="Explicame cómo voy en mis sobres por clase (necesidades, gustos, ahorro, inversión): ¿cómo leo este gráfico y qué me muestra en resumen?"
          loading={envelopes.isLoading}
          empty={classRows.length === 0}
        >
          <View style={{ gap: Spacing.md }}>
            {classRows.map((r) => {
              const frac = r.limit > 0 ? Math.min(r.spent / r.limit, 1) : 0;
              const color = ENVELOPE_CLASS_COLORS[r.cls as EnvelopeClass];
              return (
                <View key={r.cls} style={{ gap: Spacing.xs }}>
                  <View style={styles.barMeta}>
                    <Text style={styles.barName}>{ENVELOPE_CLASS_LABELS[r.cls as EnvelopeClass]}</Text>
                    <Text style={[styles.barValue, r.over && { color: Colors.expense }]}>
                      {formatMoney(r.spent, currency)} / {formatMoney(r.limit, currency)}
                    </Text>
                  </View>
                  <View style={styles.barTrack}>
                    <View
                      style={[
                        styles.barFill,
                        { width: `${Math.round(frac * 100)}%` as `${number}%` },
                        { backgroundColor: r.over ? Colors.expense : color },
                      ]}
                    />
                  </View>
                </View>
              );
            })}
          </View>
        </ChartCard>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  screenHeader: { paddingHorizontal: Spacing.md, paddingTop: Spacing.sm, paddingBottom: Spacing.xs },
  screenTitle: { fontFamily: Fonts.bold, fontSize: FontSize.xl, color: Colors.textPrimary },
  screenSubtitle: { fontFamily: Fonts.regular, fontSize: FontSize.sm, color: Colors.textMuted, marginTop: 2 },
  scroll: { flex: 1 },
  content: { padding: Spacing.md, gap: Spacing.sm + 2, paddingBottom: Spacing.xl },

  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    ...CardShadow,
    overflow: "hidden",
  },

  // ── budget hero (moved from home, Phase 7h) ─────────────────────────────────
  heroCard: { padding: Spacing.md, paddingBottom: Spacing.lg },
  heroLabel: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    letterSpacing: 1.2,
  },
  heroAmount: {
    fontFamily: Fonts.bold,
    fontSize: FontSize.hero,
    color: Colors.textPrimary,
    marginTop: Spacing.xs,
    fontVariant: ["tabular-nums"],
  },
  heroAmountEmpty: {
    fontFamily: Fonts.bold,
    fontSize: FontSize.hero,
    color: Colors.textMuted,
    marginTop: Spacing.xs,
  },
  heroBarTrack: {
    height: 5,
    backgroundColor: Colors.bgElevated,
    borderRadius: 3,
    marginTop: Spacing.sm,
  },
  heroBarFill: { height: 5, borderRadius: 3, backgroundColor: Colors.accent },
  heroContext: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    marginTop: Spacing.sm,
  },
  heroReservedNote: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: Spacing.xs,
  },
  heroOverNote: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.xs,
    color: Colors.expense,
    marginTop: Spacing.xs,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
  },
  cardTitle: { fontFamily: Fonts.semibold, fontSize: FontSize.md, color: Colors.textPrimary, flex: 1 },
  explainBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  explainText: { fontFamily: Fonts.medium, fontSize: FontSize.xs, color: Colors.accent },
  cardBody: { paddingHorizontal: Spacing.md, paddingBottom: Spacing.md },

  legendRow: { flexDirection: "row", gap: Spacing.lg, marginTop: Spacing.sm, justifyContent: "center" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: Spacing.xs },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendText: { fontFamily: Fonts.regular, fontSize: FontSize.sm, color: Colors.textSecondary },

  barMeta: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" },
  barName: { fontFamily: Fonts.medium, fontSize: FontSize.sm, color: Colors.textPrimary },
  barValue: { fontFamily: Fonts.regular, fontSize: FontSize.xs, color: Colors.textSecondary, fontVariant: ["tabular-nums"] },
  barTrack: { height: 8, backgroundColor: Colors.bgElevated, borderRadius: 4 },
  barFill: { height: 8, borderRadius: 4 },

  emptyText: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    paddingVertical: Spacing.lg,
  },
});
