/**
 * Phase 6f B10 — Gastos fijos (Bills) list screen.
 *
 * Shows upcoming bill occurrences for the next 60 days with urgency coloring:
 *   - Overdue / past due → expense red
 *   - Due within 3 days → warning ochre
 *   - Due within 7 days → accent soft
 *   - Further out → neutral
 *
 * Phase 7f: "Próximos pagos" also merges the unified feed's projected debt
 * cuotas + card minimums (item_type "debt" / "card_payment") — they are
 * recurring obligations too. Debt rows navigate to DebtDetail; card rows are
 * informational. "Todos" lists active debts read-only below the bills.
 * Projected, never materialized — no RecurringBill rows exist for these.
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
import { fetchUpcomingFeed, type UpcomingFeedItem } from "../api/dashboard";
import { fetchDebts, type DebtSummary } from "../api/debts";
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
  kind: "occurrence";
  occurrence: BillOccurrenceResponse;
  bill: RecurringBillResponse;
  urgency: UrgencyLevel;
  date: string;
}

// Phase 7f — a projected obligation from the unified feed (debt cuota or
// card minimum). Derived live by the backend; there is no occurrence row.
interface ObligationRow {
  kind: "obligation";
  item: UpcomingFeedItem;
  urgency: UrgencyLevel;
  date: string;
}

type UpcomingRow = OccurrenceRow | ObligationRow;

const OBLIGATION_LABELS: Record<string, string> = {
  debt: "Cuota de préstamo",
  card_payment: "Pago mínimo de tarjeta",
};

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

  // Phase 7f: projected debt cuotas + card minimums come from the unified
  // feed (same source Inicio uses) — never materialized as bills.
  const feedQuery = useQuery({
    queryKey: ["upcoming-feed", "bills-screen"],
    queryFn: () => fetchUpcomingFeed(todayIso(), plusDaysIso(60)),
  });

  // Active debts for the read-only "Deudas" section in the "Todos" tab.
  const debtsQuery = useQuery({
    queryKey: ["debts", "bills-screen"],
    queryFn: () => fetchDebts(false),
  });

  const billsById = useMemo(() => {
    const map: Record<string, RecurringBillResponse> = {};
    for (const b of billsQuery.data ?? []) map[b.id] = b;
    return map;
  }, [billsQuery.data]);

  const rows = useMemo((): UpcomingRow[] => {
    const occs = (occurrencesQuery.data ?? []).filter(
      (o) => o.status !== "paid" && o.status !== "skipped" && o.status !== "cancelled",
    );
    const occRows = occs
      .map((occ) => {
        const bill = billsById[occ.recurring_bill_id];
        if (!bill) return null;
        return {
          kind: "occurrence" as const,
          occurrence: occ,
          bill,
          urgency: urgencyLevel(occ.due_date, occ.status),
          date: occ.due_date,
        };
      })
      .filter((r): r is OccurrenceRow => r !== null);
    const obligationRows: ObligationRow[] = (feedQuery.data?.items ?? [])
      .filter(
        (it) => it.item_type === "debt" || it.item_type === "card_payment",
      )
      .map((it) => ({
        kind: "obligation" as const,
        item: it,
        urgency: urgencyLevel(it.date, it.is_overdue ? "overdue" : ""),
        date: it.date,
      }));
    return [...occRows, ...obligationRows].sort((a, b) =>
      a.date.localeCompare(b.date),
    );
  }, [occurrencesQuery.data, billsById, feedQuery.data]);

  const activeDebts = useMemo(
    () => (debtsQuery.data ?? []).filter((d) => d.is_active && !d.archived),
    [debtsQuery.data],
  );

  const isLoading =
    billsQuery.isLoading || occurrencesQuery.isLoading || feedQuery.isLoading;

  async function onRefresh() {
    setRefreshing(true);
    await Promise.all([
      billsQuery.refetch(),
      occurrencesQuery.refetch(),
      manageBillsQuery.refetch(),
      feedQuery.refetch(),
      debtsQuery.refetch(),
    ]);
    setRefreshing(false);
  }

  function handlePressRow(row: OccurrenceRow) {
    navigation.navigate("BillDetail", {
      bill: row.bill,
      occurrence: row.occurrence,
    });
  }

  function handlePressObligation(row: ObligationRow) {
    // Debt cuotas navigate to the debt; card minimums are informational
    // (credit accounts live in the Cuentas tab stack).
    if (row.item.item_type === "debt" && row.item.debt_id) {
      navigation.navigate("DebtDetail", { debtId: row.item.debt_id });
    }
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
            keyExtractor={(r) =>
              r.kind === "occurrence"
                ? r.occurrence.id
                : `feed-${r.item.item_type}-${r.item.id}`
            }
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
              const days = daysUntil(row.date);
              const isDebtRow =
                row.kind === "obligation" &&
                row.item.item_type === "debt" &&
                row.item.debt_id != null;
              const name =
                row.kind === "occurrence" ? row.bill.name : row.item.title;
              const sub =
                row.kind === "occurrence"
                  ? row.bill.provider || row.bill.category || row.bill.frequency
                  : row.item.item_type === "card_payment"
                    ? row.item.payment_mode === "full"
                      ? "De contado · saldo completo"
                      : "Pago mínimo de tarjeta"
                    : OBLIGATION_LABELS[row.item.item_type] ??
                      row.item.item_type;
              const amountText =
                row.kind === "occurrence"
                  ? fmtAmount(
                      row.occurrence.amount_expected ?? row.bill.amount_expected,
                      row.bill.currency,
                    )
                  : fmtAmount(row.item.amount, row.item.currency);
              const tappable = row.kind === "occurrence" || isDebtRow;
              return (
                <Pressable
                  style={({ pressed }) => [
                    styles.row,
                    { backgroundColor: colors.bg, borderColor: colors.border },
                    pressed && tappable && styles.rowPressed,
                  ]}
                  onPress={() =>
                    row.kind === "occurrence"
                      ? handlePressRow(row)
                      : handlePressObligation(row)
                  }
                >
                  <View style={styles.rowLeft}>
                    <Text style={styles.rowName} numberOfLines={1}>
                      {name}
                    </Text>
                    <Text style={styles.rowSub} numberOfLines={1}>
                      {sub}
                    </Text>
                    {colors.label ? (
                      <Text style={styles.overdueLabel}>{colors.label}</Text>
                    ) : null}
                  </View>
                  <View style={styles.rowRight}>
                    <Text style={styles.rowDate}>{fmtDueDate(row.date)}</Text>
                    <Text
                      style={[
                        styles.rowAmount,
                        row.urgency === "overdue" && styles.amountOverdue,
                      ]}
                    >
                      {amountText}
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
                    {tappable ? (
                      <Feather
                        name="chevron-right"
                        size={16}
                        color={Colors.textMuted}
                        style={styles.rowChevron}
                      />
                    ) : null}
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
          ListFooterComponent={
            activeDebts.length > 0 ? (
              <View style={styles.debtsSection}>
                <Text style={styles.debtsSectionTitle}>Deudas</Text>
                <Text style={styles.debtsSectionSub}>
                  Cuotas mensuales con fecha de pago — se administran desde
                  Deudas.
                </Text>
                {activeDebts.map((debt: DebtSummary) => (
                  <Pressable
                    key={debt.id}
                    style={({ pressed }) => [
                      styles.manageRow,
                      pressed && styles.rowPressed,
                    ]}
                    onPress={() =>
                      navigation.navigate("DebtDetail", { debtId: debt.id })
                    }
                  >
                    <View style={styles.rowLeft}>
                      <Text style={styles.rowName} numberOfLines={1}>
                        {debt.name}
                      </Text>
                      <Text style={styles.rowSub} numberOfLines={1}>
                        Cuota de préstamo · día {debt.payment_due_day}
                      </Text>
                    </View>
                    <View style={styles.rowRight}>
                      <Text style={styles.rowAmount}>
                        {fmtAmount(debt.minimum_payment, "CRC")}
                      </Text>
                      <Feather
                        name="chevron-right"
                        size={16}
                        color={Colors.textMuted}
                      />
                    </View>
                  </Pressable>
                ))}
              </View>
            ) : null
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
  debtsSection: {
    marginTop: Spacing.lg,
  },
  debtsSectionTitle: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textMuted,
    marginBottom: 2,
  },
  debtsSectionSub: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginBottom: Spacing.sm,
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
