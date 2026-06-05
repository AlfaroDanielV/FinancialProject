/**
 * Phase 6f B7 — Dashboard (Inicio tab).
 *
 * Bauhaus design: form follows function.
 *   - Balance and period metrics always visible.
 *   - Category breakdown + upcoming bills expand on demand (chevron tap).
 *   - No charts (bars built from flex View). No decoration.
 *   - Red only for overdue / negative values.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchDashboardSummary,
  fetchUpcomingFeed,
  type CategoryBreakdownItem,
  type DashboardPeriod,
  type UpcomingFeedItem,
} from "../api/dashboard";
import { SobresSection } from "../components/SobresSection";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(value: string | number | null | undefined, currency: string): string {
  const num = typeof value === "string" ? parseFloat(value) : (value ?? 0);
  if (isNaN(num)) return "—";
  const symbol = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(num);
  const formatted =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function formatDueDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short" });
}

// ── sub-components ────────────────────────────────────────────────────────────

interface PeriodPickerProps {
  value: DashboardPeriod;
  onChange: (p: DashboardPeriod) => void;
}

const PERIODS: { key: DashboardPeriod; label: string }[] = [
  { key: "month_current", label: "Este mes" },
  { key: "month_prev", label: "Mes ant." },
  { key: "ytd", label: "Año" },
];

function PeriodPicker({ value, onChange }: PeriodPickerProps) {
  return (
    <View style={styles.periodRow}>
      {PERIODS.map((p) => {
        const active = p.key === value;
        return (
          <Pressable
            key={p.key}
            onPress={() => onChange(p.key)}
            style={({ pressed }) => [
              styles.periodPill,
              active && styles.periodPillActive,
              pressed && !active && { opacity: 0.7 },
            ]}
          >
            <Text style={[styles.periodLabel, active && styles.periodLabelActive]}>
              {p.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

interface BalanceCardProps {
  balance: number;
  currency: string;
  loading: boolean;
}

function BalanceCard({ balance, currency, loading }: BalanceCardProps) {
  const isNeg = balance < 0;
  return (
    <View style={[styles.card, styles.balanceCard]}>
      <Text style={styles.balanceLabel}>SALDO TOTAL</Text>
      {loading ? (
        <ActivityIndicator color={Colors.accent} size="small" style={{ marginTop: 8 }} />
      ) : (
        <Text style={[styles.balanceAmount, isNeg && { color: Colors.expense }]}>
          {fmt(balance, currency)}
        </Text>
      )}
    </View>
  );
}

interface MetricsRowProps {
  income: number;
  expense: number;
  net: number;
  savings: number | null;
  currency: string;
  loading: boolean;
}

function MetricsRow({ income, expense, net, savings, currency, loading }: MetricsRowProps) {
  const netPositive = net >= 0;
  return (
    <View style={styles.card}>
      <View style={styles.metricsGrid}>
        <View style={styles.metricCell}>
          <View style={styles.metricHeader}>
            <Feather name="trending-up" size={14} color={Colors.income} />
            <Text style={styles.metricLabel}>INGRESOS</Text>
          </View>
          {loading ? (
            <ActivityIndicator color={Colors.accent} size="small" />
          ) : (
            <Text style={[styles.metricAmount, { color: Colors.income }]}>
              {fmt(income, currency)}
            </Text>
          )}
        </View>

        <View style={styles.metricDivider} />

        <View style={styles.metricCell}>
          <View style={styles.metricHeader}>
            <Feather name="trending-down" size={14} color={Colors.expense} />
            <Text style={styles.metricLabel}>GASTOS</Text>
          </View>
          {loading ? (
            <ActivityIndicator color={Colors.accent} size="small" />
          ) : (
            <Text style={[styles.metricAmount, { color: Colors.expense }]}>
              {fmt(expense, currency)}
            </Text>
          )}
        </View>
      </View>

      <View style={styles.metricFooter}>
        <Text style={styles.netLabel}>
          Neto{" "}
          <Text style={[styles.netValue, !netPositive && { color: Colors.expense }]}>
            {loading ? "—" : fmt(net, currency)}
          </Text>
        </Text>
        {savings !== null && !loading && (
          <Text style={styles.savingsLabel}>
            Ahorro{" "}
            <Text style={styles.savingsValue}>
              {(savings * 100).toFixed(1)}%
            </Text>
          </Text>
        )}
      </View>
    </View>
  );
}

interface ExpandableSectionProps {
  title: string;
  badge?: string;
  children: React.ReactNode;
}

function ExpandableSection({ title, badge, children }: ExpandableSectionProps) {
  const [open, setOpen] = useState(false);
  return (
    <View style={styles.card}>
      <Pressable
        onPress={() => setOpen((v) => !v)}
        style={({ pressed }) => [styles.sectionHeader, pressed && { opacity: 0.7 }]}
      >
        <View style={styles.sectionHeaderLeft}>
          <Text style={styles.sectionTitle}>{title}</Text>
          {badge ? (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{badge}</Text>
            </View>
          ) : null}
        </View>
        <Feather
          name={open ? "chevron-up" : "chevron-down"}
          size={18}
          color={Colors.textSecondary}
        />
      </Pressable>

      {open ? <View style={styles.sectionBody}>{children}</View> : null}
    </View>
  );
}

interface CategoryListProps {
  items: CategoryBreakdownItem[];
  currency: string;
}

function CategoryList({ items, currency }: CategoryListProps) {
  const expenseItems = items
    .filter((i) => parseFloat(i.expense_total) > 0)
    .sort((a, b) => parseFloat(b.expense_total) - parseFloat(a.expense_total))
    .slice(0, 8);

  const maxExpense = Math.max(...expenseItems.map((i) => parseFloat(i.expense_total)), 1);

  if (expenseItems.length === 0) {
    return <Text style={styles.emptyText}>Sin gastos registrados en este período.</Text>;
  }

  return (
    <View style={styles.categoryList}>
      {expenseItems.map((item) => {
        const expense = parseFloat(item.expense_total);
        const pct = Math.round((expense / maxExpense) * 100);
        return (
          <View key={item.category} style={styles.categoryRow}>
            <View style={styles.categoryMeta}>
              <Text style={styles.categoryName} numberOfLines={1}>
                {item.category || "Sin categoría"}
              </Text>
              <Text style={styles.categoryAmount}>{fmt(expense, currency)}</Text>
            </View>
            <View style={styles.barTrack}>
              <View style={[styles.barFill, { width: `${pct}%` as `${number}%` }]} />
            </View>
          </View>
        );
      })}
    </View>
  );
}

interface BillsListProps {
  items: UpcomingFeedItem[];
}

function BillsList({ items }: BillsListProps) {
  const bills = items
    .filter((i) => i.item_type === "bill")
    .slice(0, 10);

  if (bills.length === 0) {
    return <Text style={styles.emptyText}>Sin pagos próximos en los próximos 30 días.</Text>;
  }

  return (
    <View>
      {bills.map((item) => (
        <View key={item.id} style={styles.billRow}>
          <View style={styles.billIcon}>
            <Feather
              name="calendar"
              size={14}
              color={item.is_overdue ? Colors.overdue : Colors.textMuted}
            />
          </View>
          <View style={styles.billMeta}>
            <Text
              style={[styles.billName, item.is_overdue && { color: Colors.overdue }]}
              numberOfLines={1}
            >
              {item.title}
            </Text>
            <Text style={styles.billDate}>{formatDueDate(item.date)}</Text>
          </View>
          {item.amount !== null ? (
            <Text style={[styles.billAmount, item.is_overdue && { color: Colors.overdue }]}>
              {fmt(item.amount, item.currency)}
            </Text>
          ) : (
            <Text style={styles.billAmount}>Variable</Text>
          )}
        </View>
      ))}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function DashboardScreen() {
  const [period, setPeriod] = useState<DashboardPeriod>("month_current");
  const qc = useQueryClient();

  const {
    data: summary,
    isLoading: summaryLoading,
    refetch: refetchSummary,
    isRefetching,
  } = useQuery({
    queryKey: ["dashboard", "summary", period],
    queryFn: () => fetchDashboardSummary(period),
  });

  const { data: upcoming, refetch: refetchUpcoming } = useQuery({
    queryKey: ["calendar", "upcoming"],
    queryFn: () => fetchUpcomingFeed(todayIso(), plusDaysIso(30)),
    staleTime: 5 * 60 * 1000,
  });

  const currency = summary?.display_currency ?? "CRC";
  const balance = parseFloat(summary?.balance_total ?? "0");
  const income = parseFloat(summary?.income_total ?? "0");
  const expense = parseFloat(summary?.expense_total ?? "0");
  const net = parseFloat(summary?.net_flow ?? "0");
  const savings = summary?.savings_rate != null ? parseFloat(summary.savings_rate) : null;

  const pendingBills = upcoming?.items.filter(
    (i) => i.item_type === "bill" && i.status !== "paid",
  ) ?? [];
  const overdueBills = pendingBills.filter((i) => i.is_overdue);

  const onRefresh = async () => {
    await Promise.all([
      refetchSummary(),
      refetchUpcoming(),
      qc.invalidateQueries({ queryKey: ["envelopes"] }),
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={onRefresh}
            tintColor={Colors.accent}
          />
        }
      >
        <PeriodPicker value={period} onChange={setPeriod} />

        <BalanceCard balance={balance} currency={currency} loading={summaryLoading} />

        <MetricsRow
          income={income}
          expense={expense}
          net={net}
          savings={savings}
          currency={currency}
          loading={summaryLoading}
        />

        <SobresSection />

        <ExpandableSection
          title="Categorías"
          badge={
            summary && !summaryLoading
              ? `${summary.category_breakdown.filter((i) => parseFloat(i.expense_total) > 0).length}`
              : undefined
          }
        >
          <CategoryList
            items={summary?.category_breakdown ?? []}
            currency={currency}
          />
        </ExpandableSection>

        <ExpandableSection
          title="Próximos pagos"
          badge={
            overdueBills.length > 0
              ? `${overdueBills.length} vencido${overdueBills.length > 1 ? "s" : ""}`
              : pendingBills.length > 0
              ? `${pendingBills.length}`
              : undefined
          }
        >
          <BillsList items={pendingBills} />
        </ExpandableSection>
      </ScrollView>
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  scroll: {
    flex: 1,
  },
  content: {
    padding: Spacing.md,
    gap: Spacing.sm,
    paddingBottom: Spacing.xl,
  },

  // ── period picker ──────────────────────────────────────────────────────────
  periodRow: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginBottom: Spacing.xs,
  },
  periodPill: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs + 2,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
  },
  periodPillActive: {
    backgroundColor: Colors.accentBg,
    borderColor: Colors.accent,
  },
  periodLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  periodLabelActive: {
    color: Colors.accent,
    fontWeight: "600",
  },

  // ── card ───────────────────────────────────────────────────────────────────
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    ...CardShadow,
    overflow: "hidden",
  },

  // ── balance card ───────────────────────────────────────────────────────────
  balanceCard: {
    padding: Spacing.md,
    paddingBottom: Spacing.lg,
  },
  balanceLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "600",
    letterSpacing: 1,
  },
  balanceAmount: {
    fontSize: FontSize.display,
    color: Colors.textPrimary,
    fontWeight: "700",
    marginTop: Spacing.xs,
    fontVariant: ["tabular-nums"],
  },

  // ── metrics row ────────────────────────────────────────────────────────────
  metricsGrid: {
    flexDirection: "row",
    padding: Spacing.md,
    gap: 0,
  },
  metricCell: {
    flex: 1,
    gap: Spacing.xs,
  },
  metricDivider: {
    width: 1,
    backgroundColor: Colors.borderLight,
    marginHorizontal: Spacing.md,
  },
  metricHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
  },
  metricLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "600",
    letterSpacing: 0.8,
  },
  metricAmount: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  metricFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingBottom: Spacing.md,
    paddingTop: Spacing.xs,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  netLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  netValue: {
    fontWeight: "600",
    color: Colors.income,
  },
  savingsLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  savingsValue: {
    fontWeight: "600",
    color: Colors.textPrimary,
  },

  // ── expandable section ─────────────────────────────────────────────────────
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: Spacing.md,
  },
  sectionHeaderLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  sectionTitle: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontWeight: "600",
  },
  sectionBody: {
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    backgroundColor: Colors.bgElevated,
    padding: Spacing.md,
  },
  badge: {
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: FontSize.xs,
    color: Colors.accent,
    fontWeight: "600",
  },

  // ── categories ─────────────────────────────────────────────────────────────
  categoryList: {
    gap: Spacing.md,
  },
  categoryRow: {
    gap: Spacing.xs,
  },
  categoryMeta: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  categoryName: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
    flex: 1,
    marginRight: Spacing.sm,
  },
  categoryAmount: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
  barTrack: {
    height: 4,
    backgroundColor: Colors.border,
    borderRadius: 2,
  },
  barFill: {
    height: 4,
    backgroundColor: Colors.accentSoft,
    borderRadius: 2,
  },

  // ── bills list ─────────────────────────────────────────────────────────────
  billRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    gap: Spacing.sm,
  },
  billIcon: {
    width: 28,
    alignItems: "center",
  },
  billMeta: {
    flex: 1,
  },
  billName: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
  },
  billDate: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 1,
  },
  billAmount: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
    fontVariant: ["tabular-nums"],
  },

  // ── empty state ────────────────────────────────────────────────────────────
  emptyText: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    paddingVertical: Spacing.md,
  },
});
