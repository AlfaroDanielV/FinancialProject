/**
 * Phase 7c "UI 2.0" — Inicio tab, restructured around money clarity.
 *
 * The screen answers, in order:
 *   1. ¿Cuánto me queda este mes?   → hero from /envelopes/summary totals
 *   2. ¿Qué pagos vienen?           → bills + cuotas + mínimos de tarjeta,
 *                                     always visible (overdue first)
 *   3. ¿Cómo voy por sobre?         → SobresSection (drain bars)
 *   4. ¿Cómo me fue?                → Resumen (period picker lives HERE —
 *                                     the hero and pagos are always "now")
 *
 * Rams discipline: neutral surfaces, ink accent, color only for meaning,
 * no chart lib (bars are flex Views), details on demand.
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
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchDashboardSummary,
  fetchUpcomingFeed,
  type CategoryBreakdownItem,
  type DashboardPeriod,
  type UpcomingFeedItem,
} from "../api/dashboard";
import { fetchEnvelopeSummary } from "../api/envelopes";
import { SobresSection } from "../components/SobresSection";
import type { InicioStackParamList } from "../navigation/InicioNavigator";
import { formatMoney } from "../lib/format";
import { CardShadow, Colors, Fonts, FontSize, Radius, Spacing } from "../theme";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(value: string | number | null | undefined, currency: string): string {
  const num = typeof value === "string" ? parseFloat(value) : (value ?? 0);
  if (isNaN(num)) return "—";
  return formatMoney(Math.abs(num), currency);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysLeftInMonth(): number {
  const now = new Date();
  const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  return lastDay - now.getDate();
}

function headerDateLine(): string {
  const s = new Date().toLocaleDateString("es-CR", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatDueDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short" });
}

function relativeDue(iso: string): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(iso + "T00:00:00");
  const diff = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (diff < 0) return `hace ${-diff} día${diff === -1 ? "" : "s"}`;
  if (diff === 0) return "hoy";
  if (diff === 1) return "mañana";
  return `en ${diff} días`;
}

// ── hero — ¿cuánto me queda este mes? ─────────────────────────────────────────

function HeroCard() {
  const { data: summary, isLoading } = useQuery({
    queryKey: ["envelopes", "summary"],
    queryFn: fetchEnvelopeSummary,
  });

  const hasEnvelopes = (summary?.envelopes.length ?? 0) > 0;
  const available = summary?.total_available ?? 0;
  const limit = summary?.total_limit ?? 0;
  const reserved = summary?.total_reserved ?? 0;
  const overBudget = available < 0;
  const fraction = limit > 0 ? Math.max(0, Math.min(available / limit, 1)) : 0;
  const low = limit > 0 && available <= 0.05 * limit;
  const daysLeft = daysLeftInMonth();

  return (
    <View style={[styles.card, styles.heroCard]}>
      <Text style={styles.heroLabel}>TE QUEDA ESTE MES</Text>

      {isLoading ? (
        <ActivityIndicator color={Colors.accent} size="small" style={{ marginVertical: Spacing.md }} />
      ) : !hasEnvelopes ? (
        <>
          <Text style={styles.heroAmountEmpty}>—</Text>
          <Text style={styles.heroContext}>
            Creá tus sobres abajo para ver cuánto podés gastar.
          </Text>
        </>
      ) : (
        <>
          <Text style={[styles.heroAmount, overBudget && { color: Colors.expense }]}>
            {overBudget ? "−" : ""}
            {fmt(available, summary!.currency)}
          </Text>

          <View style={styles.heroBarTrack}>
            <View
              style={[
                styles.heroBarFill,
                { width: `${Math.round(fraction * 100)}%` as `${number}%` },
                low && { backgroundColor: Colors.expense },
              ]}
            />
          </View>

          <Text style={styles.heroContext}>
            de {fmt(limit, summary!.currency)} presupuestados ·{" "}
            {daysLeft === 0 ? "último día del mes" : `quedan ${daysLeft} días`}
          </Text>

          {overBudget ? (
            <Text style={styles.heroOverNote}>Te pasaste del presupuesto del mes.</Text>
          ) : reserved > 0 ? (
            <Text style={styles.heroReservedNote}>
              {fmt(reserved, summary!.currency)} ya apartado para gastos fijos
            </Text>
          ) : null}
        </>
      )}
    </View>
  );
}

// ── próximos pagos — siempre visibles, vencidos primero ──────────────────────

const PAYMENT_ICONS: Record<
  UpcomingFeedItem["item_type"],
  React.ComponentProps<typeof Feather>["name"]
> = {
  bill: "calendar",
  debt: "repeat",
  card_payment: "credit-card",
  event: "flag",
};

const INACTIONABLE = new Set(["paid", "skipped", "cancelled"]);
const PREVIEW_COUNT = 3;

function PaymentRow({ item }: { item: UpcomingFeedItem }) {
  return (
    <View style={styles.payRow}>
      <View style={styles.payIcon}>
        <Feather
          name={PAYMENT_ICONS[item.item_type] ?? "calendar"}
          size={15}
          color={item.is_overdue ? Colors.overdue : Colors.textMuted}
        />
      </View>
      <View style={styles.payMeta}>
        <Text
          style={[styles.payName, item.is_overdue && { color: Colors.overdue }]}
          numberOfLines={1}
        >
          {item.title}
        </Text>
        <Text style={[styles.payDate, item.is_overdue && { color: Colors.overdue }]}>
          {formatDueDate(item.date)} · {item.is_overdue ? "vencido " : ""}
          {relativeDue(item.date)}
        </Text>
      </View>
      <Text style={[styles.payAmount, item.is_overdue && { color: Colors.overdue }]}>
        {item.amount !== null ? fmt(item.amount, item.currency) : "Variable"}
      </Text>
    </View>
  );
}

function UpcomingCard() {
  const [expanded, setExpanded] = useState(false);

  const { data: upcoming, isLoading } = useQuery({
    queryKey: ["calendar", "upcoming"],
    queryFn: () => fetchUpcomingFeed(todayIso(), plusDaysIso(30)),
    staleTime: 5 * 60 * 1000,
  });

  // Everything the user still has to pay: bills + projected loan cuotas +
  // card minimums. Events stay out (they're reminders, not payments).
  const payments = (upcoming?.items ?? [])
    .filter((i) => i.item_type !== "event")
    .filter((i) => i.status === null || !INACTIONABLE.has(i.status));
  const sorted = [
    ...payments.filter((i) => i.is_overdue),
    ...payments.filter((i) => !i.is_overdue),
  ];
  const overdueCount = payments.filter((i) => i.is_overdue).length;
  const visible = expanded ? sorted : sorted.slice(0, PREVIEW_COUNT);
  const hiddenCount = sorted.length - PREVIEW_COUNT;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>Próximos pagos</Text>
        {overdueCount > 0 ? (
          <View style={[styles.badge, styles.badgeOverdue]}>
            <Text style={[styles.badgeText, { color: Colors.textOnDark }]}>
              {overdueCount} vencido{overdueCount > 1 ? "s" : ""}
            </Text>
          </View>
        ) : sorted.length > 0 ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{sorted.length}</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.cardBody}>
        {isLoading ? (
          <ActivityIndicator color={Colors.accent} style={{ paddingVertical: Spacing.md }} />
        ) : sorted.length === 0 ? (
          <Text style={styles.emptyText}>Nada pendiente en los próximos 30 días.</Text>
        ) : (
          <>
            {visible.map((item) => (
              <PaymentRow key={`${item.item_type}-${item.id}`} item={item} />
            ))}
            {hiddenCount > 0 && (
              <Pressable
                onPress={() => setExpanded((v) => !v)}
                style={({ pressed }) => [styles.expandBtn, pressed && { opacity: 0.6 }]}
              >
                <Text style={styles.expandText}>
                  {expanded ? "Ver menos" : `Ver los ${hiddenCount} restantes`}
                </Text>
                <Feather
                  name={expanded ? "chevron-up" : "chevron-down"}
                  size={14}
                  color={Colors.textSecondary}
                />
              </Pressable>
            )}
          </>
        )}
      </View>
    </View>
  );
}

// ── resumen — período seleccionable, categorías a demanda ────────────────────

const PERIODS: { key: DashboardPeriod; label: string }[] = [
  { key: "month_current", label: "Este mes" },
  { key: "month_prev", label: "Mes ant." },
  { key: "ytd", label: "Año" },
];

function CategoryList({
  items,
  currency,
}: {
  items: CategoryBreakdownItem[];
  currency: string;
}) {
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

function ResumenCard() {
  const [period, setPeriod] = useState<DashboardPeriod>("month_current");
  const [categoriesOpen, setCategoriesOpen] = useState(false);

  const { data: summary, isLoading } = useQuery({
    queryKey: ["dashboard", "summary", period],
    queryFn: () => fetchDashboardSummary(period),
  });

  const currency = summary?.display_currency ?? "CRC";
  const income = parseFloat(summary?.income_total ?? "0");
  const expense = parseFloat(summary?.expense_total ?? "0");
  const net = parseFloat(summary?.net_flow ?? "0");
  const savings = summary?.savings_rate != null ? parseFloat(summary.savings_rate) : null;
  // Phase 7h: "Disponible" excludes savings; savings shown as plata apartada.
  const available = parseFloat(summary?.available_balance ?? "0");
  const savingsBalance = parseFloat(summary?.savings_balance ?? "0");
  const categoryCount =
    summary?.category_breakdown.filter((i) => parseFloat(i.expense_total) > 0).length ?? 0;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>Resumen</Text>
        <View style={styles.periodRow}>
          {PERIODS.map((p) => {
            const active = p.key === period;
            return (
              <Pressable
                key={p.key}
                onPress={() => setPeriod(p.key)}
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
      </View>

      <View style={styles.cardBody}>
        <View style={styles.metricsGrid}>
          <View style={styles.metricCell}>
            <View style={styles.metricHeader}>
              <Feather name="arrow-down-left" size={13} color={Colors.income} />
              <Text style={styles.metricLabel}>INGRESOS</Text>
            </View>
            {isLoading ? (
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
              <Feather name="arrow-up-right" size={13} color={Colors.expense} />
              <Text style={styles.metricLabel}>GASTOS</Text>
            </View>
            {isLoading ? (
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
            <Text style={[styles.netValue, net < 0 && { color: Colors.expense }]}>
              {isLoading ? "—" : `${net < 0 ? "−" : ""}${fmt(net, currency)}`}
            </Text>
          </Text>
          {savings !== null && !isLoading && (
            <Text style={styles.netLabel}>
              Ahorro <Text style={styles.netValue}>{(savings * 100).toFixed(1)}%</Text>
            </Text>
          )}
        </View>

        <Pressable
          onPress={() => setCategoriesOpen((v) => !v)}
          style={({ pressed }) => [styles.subsectionHeader, pressed && { opacity: 0.7 }]}
        >
          <Text style={styles.subsectionTitle}>
            Categorías{categoryCount > 0 ? ` · ${categoryCount}` : ""}
          </Text>
          <Feather
            name={categoriesOpen ? "chevron-up" : "chevron-down"}
            size={16}
            color={Colors.textSecondary}
          />
        </Pressable>
        {categoriesOpen && (
          <CategoryList items={summary?.category_breakdown ?? []} currency={currency} />
        )}

        <View style={styles.balanceFooter}>
          <Text style={styles.balanceFooterLabel}>Disponible</Text>
          <Text style={[styles.balanceFooterValue, available < 0 && { color: Colors.expense }]}>
            {isLoading ? "—" : `${available < 0 ? "−" : ""}${fmt(available, currency)}`}
          </Text>
        </View>
        {!isLoading && savingsBalance > 0 && (
          <View style={styles.savingsAsideRow}>
            <Text style={styles.savingsAsideText}>
              Ahorros: {fmt(savingsBalance, currency)} (aparte)
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── disponible strip (Phase 7h) — compact balance under the hero ───────────────

function DisponibleStrip() {
  // Shares the month_current cache with ResumenCard's default period.
  const { data: summary, isLoading } = useQuery({
    queryKey: ["dashboard", "summary", "month_current"],
    queryFn: () => fetchDashboardSummary("month_current"),
  });
  const currency = summary?.display_currency ?? "CRC";
  const available = parseFloat(summary?.available_balance ?? "0");
  const savingsBalance = parseFloat(summary?.savings_balance ?? "0");

  return (
    <View style={styles.dispStrip}>
      <View style={styles.dispRow}>
        <Text style={styles.dispLabel}>DISPONIBLE</Text>
        {isLoading ? (
          <ActivityIndicator color={Colors.accent} size="small" />
        ) : (
          <Text style={[styles.dispValue, available < 0 && { color: Colors.expense }]}>
            {`${available < 0 ? "−" : ""}${fmt(available, currency)}`}
          </Text>
        )}
      </View>
      {!isLoading && savingsBalance > 0 && (
        <Text style={styles.dispSavings}>
          + {fmt(savingsBalance, currency)} en ahorros (aparte)
        </Text>
      )}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function DashboardScreen() {
  const qc = useQueryClient();
  const nav = useNavigation<NativeStackNavigationProp<InicioStackParamList>>();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["envelopes"] }),
        qc.invalidateQueries({ queryKey: ["dashboard"] }),
        qc.invalidateQueries({ queryKey: ["calendar"] }),
      ]);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={Colors.accent}
          />
        }
      >
        <Text style={styles.dateLine}>{headerDateLine()}</Text>

        <HeroCard />
        <DisponibleStrip />
        <UpcomingCard />
        <SobresSection onOpenAnalytics={() => nav.navigate("Analytics")} />
        <ResumenCard />
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
    gap: Spacing.sm + 2,
    paddingBottom: Spacing.xl,
  },

  dateLine: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginBottom: Spacing.xs,
  },

  // ── card scaffold ──────────────────────────────────────────────────────────
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    ...CardShadow,
    overflow: "hidden",
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
  },
  cardTitle: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  cardBody: {
    paddingHorizontal: Spacing.md,
    paddingBottom: Spacing.md,
  },

  // ── hero ───────────────────────────────────────────────────────────────────
  heroCard: {
    padding: Spacing.md,
    paddingBottom: Spacing.lg,
  },
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
  heroBarFill: {
    height: 5,
    borderRadius: 3,
    backgroundColor: Colors.accent,
  },
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

  // ── badges ─────────────────────────────────────────────────────────────────
  badge: {
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.sm,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  badgeOverdue: {
    backgroundColor: Colors.overdue,
  },
  badgeText: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
  },

  // ── próximos pagos ─────────────────────────────────────────────────────────
  payRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: Spacing.sm + 2,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    gap: Spacing.sm,
  },
  payIcon: {
    width: 28,
    alignItems: "center",
  },
  payMeta: {
    flex: 1,
  },
  payName: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
  },
  payDate: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 1,
  },
  payAmount: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
  expandBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.xs,
    paddingTop: Spacing.sm + 2,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  expandText: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },

  // ── resumen ────────────────────────────────────────────────────────────────
  periodRow: {
    flexDirection: "row",
    gap: Spacing.xs,
  },
  periodPill: {
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs,
    borderRadius: Radius.sm,
    backgroundColor: Colors.bgElevated,
  },
  periodPillActive: {
    backgroundColor: Colors.accent,
  },
  periodLabel: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
  },
  periodLabelActive: {
    color: Colors.textOnDark,
  },
  metricsGrid: {
    flexDirection: "row",
    paddingTop: Spacing.xs,
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
    fontFamily: Fonts.semibold,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    letterSpacing: 0.8,
  },
  metricAmount: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.lg,
    fontVariant: ["tabular-nums"],
  },
  metricFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingTop: Spacing.sm,
    marginTop: Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  netLabel: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  netValue: {
    fontFamily: Fonts.semibold,
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },

  subsectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: Spacing.md,
    marginTop: Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  subsectionTitle: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
  },

  balanceFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
    paddingTop: Spacing.md,
    marginTop: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  balanceFooterLabel: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  balanceFooterValue: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  savingsAsideRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    marginTop: Spacing.xs,
  },
  savingsAsideText: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontVariant: ["tabular-nums"],
  },

  // ── disponible strip (Phase 7h) ──────────────────────────────────────────────
  dispStrip: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    ...CardShadow,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
  },
  dispRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  dispLabel: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    letterSpacing: 1.2,
  },
  dispValue: {
    fontFamily: Fonts.bold,
    fontSize: FontSize.lg,
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  dispSavings: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 2,
    fontVariant: ["tabular-nums"],
  },

  // ── categorías ─────────────────────────────────────────────────────────────
  categoryList: {
    gap: Spacing.md,
    paddingTop: Spacing.md,
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
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    flex: 1,
    marginRight: Spacing.sm,
  },
  categoryAmount: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
  barTrack: {
    height: 4,
    backgroundColor: Colors.bgElevated,
    borderRadius: 2,
  },
  barFill: {
    height: 4,
    backgroundColor: Colors.accentSoft,
    borderRadius: 2,
  },

  // ── empty state ────────────────────────────────────────────────────────────
  emptyText: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    paddingVertical: Spacing.md,
  },
});
