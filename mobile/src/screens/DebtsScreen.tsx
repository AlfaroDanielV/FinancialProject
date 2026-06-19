/**
 * Phase 6f B11 — Deudas list screen.
 *
 * Shows the overview metrics strip (total balance, total monthly payment,
 * DTI%) and a list of active debts sorted by current balance descending.
 * Tap a debt row → DebtDetailScreen.
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import {
  DEBT_TYPE_LABELS,
  fetchDebts,
  fetchDebtOverview,
  type DebtSummary,
} from "../api/debts";
import { api } from "../api/client";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(value: number, currency: string): string {
  const symbol = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(value);
  const formatted =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function dtiColor(ratio: number): string {
  if (ratio > 0.4) return Colors.overdue;
  if (ratio > 0.3) return Colors.warning;
  return Colors.income;
}

async function fetchTotalMonthlyIncome(): Promise<number> {
  try {
    const { data } = await api.get<Array<{ is_active?: boolean; amount?: unknown }>>("/recurring-incomes");
    if (!Array.isArray(data)) return 0;
    return data
      .filter((r) => r.is_active !== false)
      .reduce((sum, r) => {
        const v = Number(r.amount ?? 0);
        return sum + (Number.isFinite(v) ? v : 0);
      }, 0);
  } catch {
    return 0;
  }
}

// ── component ─────────────────────────────────────────────────────────────────

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "DebtsList">;
};

export function DebtsScreen({ navigation }: Props) {
  const [showArchived, setShowArchived] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const debtsQuery = useQuery({
    queryKey: ["debts", showArchived],
    queryFn: () => fetchDebts(showArchived),
  });

  const overviewQuery = useQuery({
    queryKey: ["debts", "overview"],
    queryFn: fetchDebtOverview,
  });

  const incomeQuery = useQuery({
    queryKey: ["recurring-incomes", "total"],
    queryFn: fetchTotalMonthlyIncome,
  });

  const debts = useMemo(
    () => [...(debtsQuery.data ?? [])].sort((a, b) => b.current_balance - a.current_balance),
    [debtsQuery.data],
  );
  const overview = overviewQuery.data;
  const monthlyIncome = incomeQuery.data ?? 0;
  const dtiRatio =
    overview && monthlyIncome > 0 ? overview.total_minimum_monthly / monthlyIncome : null;

  const isLoading = debtsQuery.isLoading || overviewQuery.isLoading;

  async function onRefresh() {
    setRefreshing(true);
    await Promise.all([
      debtsQuery.refetch(),
      overviewQuery.refetch(),
      incomeQuery.refetch(),
    ]);
    setRefreshing(false);
  }

  const listHeader = overview ? (
    <View style={styles.metricsStrip}>
      <MetricCard
        label="Deuda total"
        value={fmt(overview.total_debt, "CRC")}
      />
      <MetricCard
        label="Pago mensual"
        value={fmt(overview.total_minimum_monthly, "CRC")}
      />
      <MetricCard
        label="DTI"
        value={dtiRatio !== null ? `${(dtiRatio * 100).toFixed(1)}%` : "—"}
        valueColor={dtiRatio !== null ? dtiColor(dtiRatio) : Colors.textMuted}
      />
    </View>
  ) : null;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Deudas</Text>
        <View style={styles.headerActions}>
          <Pressable
            style={styles.archivedToggle}
            onPress={() => setShowArchived((v) => !v)}
          >
            <Text style={styles.archivedToggleText}>
              {showArchived ? "Ocultar archivadas" : "Ver archivadas"}
            </Text>
          </Pressable>
          <Pressable
            style={({ pressed }) => [styles.newBtn, pressed && { opacity: 0.85 }]}
            onPress={() => navigation.navigate("DebtCreate")}
          >
            <Feather name="plus" size={15} color={Colors.textOnDark} />
            <Text style={styles.newBtnText}>Nueva</Text>
          </Pressable>
        </View>
      </View>

      {isLoading && !refreshing ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      ) : (
        <FlatList
          data={debts}
          keyExtractor={(d) => d.id}
          ListHeaderComponent={listHeader}
          contentContainerStyle={
            debts.length === 0 && !overview ? styles.emptyContainer : styles.listContent
          }
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={Colors.accent}
            />
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="check-circle" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin deudas registradas</Text>
              <Text style={styles.emptySub}>
                Registrá tus deudas para ver la amortización, la calculadora de
                cancelación y que el agente las tome en cuenta en tus planes.
              </Text>
              <Pressable
                style={({ pressed }) => [styles.emptyCta, pressed && { opacity: 0.85 }]}
                onPress={() => navigation.navigate("DebtCreate")}
              >
                <Feather name="plus" size={16} color={Colors.textOnDark} />
                <Text style={styles.emptyCtaText}>Nueva deuda</Text>
              </Pressable>
            </View>
          }
          renderItem={({ item: debt }) => (
            <Pressable
              style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
              onPress={() => navigation.navigate("DebtDetail", { debtId: debt.id })}
            >
              <View style={styles.rowLeft}>
                <Text style={styles.rowName} numberOfLines={1}>
                  {debt.name}
                </Text>
                <Text style={styles.rowType}>
                  {DEBT_TYPE_LABELS[debt.debt_type] ?? debt.debt_type}
                </Text>
                {!debt.is_active && (
                  <Text style={styles.pausedBadge}>Pausada</Text>
                )}
              </View>
              <View style={styles.rowRight}>
                <Text style={styles.rowBalance}>{fmt(debt.current_balance, "CRC")}</Text>
                <Text style={styles.rowPayment}>
                  {fmt(debt.minimum_payment, "CRC")}/mes
                </Text>
                <Feather name="chevron-right" size={16} color={Colors.textMuted} />
              </View>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

function MetricCard({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <View style={metricStyles.card}>
      <Text style={metricStyles.label}>{label}</Text>
      <Text style={[metricStyles.value, valueColor ? { color: valueColor } : null]}>
        {value}
      </Text>
    </View>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const metricStyles = StyleSheet.create({
  card: {
    flex: 1,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.sm,
    alignItems: "center",
    gap: 2,
    ...CardShadow,
  },
  label: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
  },
  value: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
    textAlign: "center",
  },
});

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  archivedToggle: {
    paddingVertical: Spacing.xs,
  },
  archivedToggleText: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "500",
  },
  newBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.xs + 2,
  },
  newBtnText: {
    fontSize: FontSize.sm,
    color: Colors.textOnDark,
    fontWeight: "700",
  },
  emptyCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    marginTop: Spacing.sm,
  },
  emptyCtaText: {
    fontSize: FontSize.md,
    color: Colors.textOnDark,
    fontWeight: "700",
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  listContent: {
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: "center",
    padding: Spacing.md,
  },
  metricsStrip: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginBottom: Spacing.md,
  },
  emptyCard: {
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.xl,
    gap: Spacing.sm,
    ...CardShadow,
  },
  emptyTitle: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
    marginTop: Spacing.sm,
  },
  emptySub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    textAlign: "center",
    lineHeight: 20,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    marginBottom: Spacing.sm,
    ...CardShadow,
  },
  rowPressed: {
    opacity: 0.78,
  },
  rowLeft: {
    flex: 1,
    gap: 2,
  },
  rowName: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  rowType: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  pausedBadge: {
    alignSelf: "flex-start",
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.warning,
    marginTop: 2,
  },
  rowRight: {
    alignItems: "flex-end",
    gap: 2,
    marginLeft: Spacing.sm,
  },
  rowBalance: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  rowPayment: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
});
