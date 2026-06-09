/**
 * Phase 6f B10 — Gastos fijos (Bills) list screen.
 *
 * Shows upcoming bill occurrences for the next 60 days with urgency coloring:
 *   - Overdue / past due → expense red
 *   - Due within 3 days → warning ochre
 *   - Due within 7 days → accent soft
 *   - Further out → neutral
 *
 * Tap an occurrence row to navigate to BillDetailScreen for mark-paid,
 * pause/resume, and archive actions.
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
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import {
  fetchBillOccurrences,
  fetchRecurringBills,
  ACTIONABLE_STATUSES,
  FREQUENCY_LABELS,
  type BillOccurrenceResponse,
  type RecurringBillResponse,
} from "../api/bills";
import { BillFormModal } from "../components/BillFormModal";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

// ── helpers ───────────────────────────────────────────────────────────────────

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysUntil(isoDate: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(isoDate + "T12:00:00");
  return Math.ceil((due.getTime() - today.getTime()) / 86_400_000);
}

function fmtDueDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short" });
}

function fmtAmount(amount: number | null, currency: string): string {
  if (amount === null) return "Variable";
  const symbol = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(amount);
  const formatted =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

type UrgencyLevel = "overdue" | "urgent" | "soon" | "normal";

function urgencyLevel(isoDate: string, status: string): UrgencyLevel {
  if (status === "overdue") return "overdue";
  const days = daysUntil(isoDate);
  if (days < 0) return "overdue";
  if (days < 3) return "urgent";
  if (days < 7) return "soon";
  return "normal";
}

const URGENCY_COLORS: Record<UrgencyLevel, { bg: string; border: string; label: string }> = {
  overdue: { bg: Colors.expense + "18", border: Colors.overdue, label: "Vencido" },
  urgent: { bg: Colors.warningBg, border: Colors.warning, label: "" },
  soon: { bg: Colors.accentBg, border: Colors.accentSoft, label: "" },
  normal: { bg: Colors.bgCard, border: Colors.border, label: "" },
};

// ── component ─────────────────────────────────────────────────────────────────

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "BillsList">;
};

interface OccurrenceRow {
  occurrence: BillOccurrenceResponse;
  bill: RecurringBillResponse;
  urgency: UrgencyLevel;
}

export function BillsScreen({ navigation }: Props) {
  const [refreshing, setRefreshing] = useState(false);
  const [viewMode, setViewMode] = useState<"upcoming" | "all">("upcoming");
  const [showInactive, setShowInactive] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const queryClient = useQueryClient();

  const billsQuery = useQuery({
    queryKey: ["recurring-bills", "active"],
    queryFn: () => fetchRecurringBills(false),
  });

  // The "Todos" management list: active by default, paused when toggled.
  const manageBillsQuery = useQuery({
    queryKey: ["recurring-bills", "manage", showInactive],
    queryFn: () => fetchRecurringBills(showInactive),
  });

  const occurrencesQuery = useQuery({
    queryKey: ["bill-occurrences", "upcoming"],
    queryFn: () =>
      fetchBillOccurrences({
        from_date: todayIso(),
        to_date: plusDaysIso(60),
      }),
  });

  const billsById = useMemo(() => {
    const map: Record<string, RecurringBillResponse> = {};
    for (const b of billsQuery.data ?? []) map[b.id] = b;
    return map;
  }, [billsQuery.data]);

  const rows = useMemo((): OccurrenceRow[] => {
    const occs = (occurrencesQuery.data ?? []).filter(
      (o) => o.status !== "paid" && o.status !== "skipped" && o.status !== "cancelled",
    );
    return occs
      .map((occ) => {
        const bill = billsById[occ.recurring_bill_id];
        if (!bill) return null;
        return {
          occurrence: occ,
          bill,
          urgency: urgencyLevel(occ.due_date, occ.status),
        };
      })
      .filter((r): r is OccurrenceRow => r !== null)
      .sort((a, b) => a.occurrence.due_date.localeCompare(b.occurrence.due_date));
  }, [occurrencesQuery.data, billsById]);

  const isLoading = billsQuery.isLoading || occurrencesQuery.isLoading;

  async function onRefresh() {
    setRefreshing(true);
    await Promise.all([
      billsQuery.refetch(),
      occurrencesQuery.refetch(),
      manageBillsQuery.refetch(),
    ]);
    setRefreshing(false);
  }

  function handlePressRow(row: OccurrenceRow) {
    navigation.navigate("BillDetail", {
      bill: row.bill,
      occurrence: row.occurrence,
    });
  }

  function handlePressBill(bill: RecurringBillResponse) {
    // No specific occurrence from the management list — the detail handles null.
    navigation.navigate("BillDetail", { bill, occurrence: null });
  }

  function onFormSaved() {
    setFormVisible(false);
    queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
    queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* ── header ── */}
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <Text style={styles.headerTitle}>Gastos fijos</Text>
          <Pressable
            style={({ pressed }) => [styles.newBtn, pressed && { opacity: 0.85 }]}
            onPress={() => setFormVisible(true)}
          >
            <Feather name="plus" size={15} color={Colors.textOnDark} />
            <Text style={styles.newBtnText}>Nuevo</Text>
          </Pressable>
        </View>
        <View style={styles.tabs}>
          {([
            ["upcoming", "Próximos pagos"],
            ["all", "Todos"],
          ] as const).map(([mode, label]) => (
            <Pressable
              key={mode}
              style={[styles.tab, viewMode === mode && styles.tabActive]}
              onPress={() => setViewMode(mode)}
            >
              <Text style={[styles.tabText, viewMode === mode && styles.tabTextActive]}>
                {label}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {viewMode === "upcoming" ? (
        isLoading && !refreshing ? (
          <View style={styles.center}>
            <ActivityIndicator color={Colors.accent} />
          </View>
        ) : (
          <FlatList
            data={rows}
            keyExtractor={(r) => r.occurrence.id}
            contentContainerStyle={rows.length === 0 ? styles.emptyContainer : styles.listContent}
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
                <Text style={styles.emptyTitle}>Sin pagos pendientes</Text>
                <Text style={styles.emptySub}>
                  No hay gastos fijos vencidos ni próximos en los siguientes 60 días.
                </Text>
              </View>
            }
            renderItem={({ item: row }) => {
              const colors = URGENCY_COLORS[row.urgency];
              const days = daysUntil(row.occurrence.due_date);
              return (
                <Pressable
                  style={({ pressed }) => [
                    styles.row,
                    { backgroundColor: colors.bg, borderColor: colors.border },
                    pressed && styles.rowPressed,
                  ]}
                  onPress={() => handlePressRow(row)}
                >
                  <View style={styles.rowLeft}>
                    <Text style={styles.rowName} numberOfLines={1}>
                      {row.bill.name}
                    </Text>
                    <Text style={styles.rowSub} numberOfLines={1}>
                      {row.bill.provider || row.bill.category || row.bill.frequency}
                    </Text>
                    {colors.label ? (
                      <Text style={styles.overdueLabel}>{colors.label}</Text>
                    ) : null}
                  </View>
                  <View style={styles.rowRight}>
                    <Text style={styles.rowDate}>{fmtDueDate(row.occurrence.due_date)}</Text>
                    <Text
                      style={[
                        styles.rowAmount,
                        row.urgency === "overdue" && styles.amountOverdue,
                      ]}
                    >
                      {fmtAmount(
                        row.occurrence.amount_expected ?? row.bill.amount_expected,
                        row.bill.currency,
                      )}
                    </Text>
                    {days >= 0 && days <= 7 ? (
                      <Text
                        style={[
                          styles.rowDaysLeft,
                          row.urgency === "overdue" && styles.amountOverdue,
                        ]}
                      >
                        {days === 0 ? "Hoy" : `en ${days}d`}
                      </Text>
                    ) : null}
                    <Feather
                      name="chevron-right"
                      size={16}
                      color={Colors.textMuted}
                      style={styles.rowChevron}
                    />
                  </View>
                </Pressable>
              );
            }}
          />
        )
      ) : (
        <FlatList
          data={manageBillsQuery.data ?? []}
          keyExtractor={(b) => b.id}
          contentContainerStyle={
            (manageBillsQuery.data ?? []).length === 0
              ? styles.emptyContainer
              : styles.listContent
          }
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={Colors.accent} />
          }
          ListHeaderComponent={
            <Pressable
              style={styles.inactiveToggle}
              onPress={() => setShowInactive((v) => !v)}
            >
              <Feather
                name={showInactive ? "check-square" : "square"}
                size={15}
                color={showInactive ? Colors.accent : Colors.textMuted}
              />
              <Text style={styles.inactiveToggleText}>Ver pausados</Text>
            </Pressable>
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="repeat" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>
                {showInactive ? "Sin gastos fijos pausados" : "Sin gastos fijos"}
              </Text>
              <Text style={styles.emptySub}>
                Registralo por el chat o tocá "+ Nuevo" para crear uno.
              </Text>
            </View>
          }
          renderItem={({ item: bill }) => (
            <Pressable
              style={({ pressed }) => [styles.manageRow, pressed && styles.rowPressed]}
              onPress={() => handlePressBill(bill)}
            >
              <View style={styles.rowLeft}>
                <Text style={styles.rowName} numberOfLines={1}>
                  {bill.name}
                </Text>
                <Text style={styles.rowSub} numberOfLines={1}>
                  {(bill.category ?? "—")} · {FREQUENCY_LABELS[bill.frequency] ?? bill.frequency}
                  {!bill.is_active ? "  ·  Pausado" : ""}
                </Text>
              </View>
              <View style={styles.rowRight}>
                <Text style={styles.rowAmount}>
                  {fmtAmount(bill.amount_expected, bill.currency)}
                </Text>
                <Feather name="chevron-right" size={16} color={Colors.textMuted} />
              </View>
            </Pressable>
          )}
        />
      )}

      <BillFormModal
        visible={formVisible}
        mode="create"
        onClose={() => setFormVisible(false)}
        onSaved={onFormSaved}
      />
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  header: {
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
  headerSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
  },
  headerTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  newBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 6,
  },
  newBtnText: { fontSize: FontSize.sm, color: Colors.textOnDark, fontWeight: "700" },
  tabs: {
    flexDirection: "row",
    gap: Spacing.xs,
    marginTop: Spacing.sm,
  },
  tab: {
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 1,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
  },
  tabActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  tabText: { fontSize: FontSize.sm, color: Colors.textSecondary, fontWeight: "600" },
  tabTextActive: { color: Colors.accent },
  inactiveToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: Spacing.xs,
    marginBottom: Spacing.xs,
  },
  inactiveToggleText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  manageRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    marginBottom: Spacing.sm,
    ...CardShadow,
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
    borderWidth: 1,
    borderRadius: Radius.md,
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
  rowSub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  overdueLabel: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.overdue,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 2,
  },
  rowRight: {
    alignItems: "flex-end",
    gap: 1,
    flexDirection: "column",
    marginLeft: Spacing.sm,
  },
  rowDate: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  rowAmount: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  amountOverdue: {
    color: Colors.overdue,
  },
  rowDaysLeft: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  rowChevron: {
    marginTop: 2,
  },
});
