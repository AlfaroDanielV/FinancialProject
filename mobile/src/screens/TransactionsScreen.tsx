/**
 * Phase 6f B9 — Global transaction list (Movimientos tab root).
 *
 * Filters: kind (all/income/expense) always visible as pills.
 *          Account filter behind a toggle — expands inline as account chips.
 * Pagination: cursor-based, date desc, locked (backend only emits next_cursor
 *             on date sort — switching sort would silently break pagination).
 * Deferred: CSV export, bulk-select (browser-native patterns, not mobile-native).
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation, useRoute } from "@react-navigation/native";
import type { RouteProp } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { Feather } from "@expo/vector-icons";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchTransactions,
  DEFAULT_FILTERS,
  type TransactionFilters,
  type TransactionKind,
  type TransactionResponse,
} from "../api/transactions";
import { fetchAccounts } from "../api/accounts";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import type { TransactionsStackParamList } from "../navigation/TransactionsNavigator";

type Nav = NativeStackNavigationProp<TransactionsStackParamList, "TransactionsList">;
type Route = RouteProp<TransactionsStackParamList, "TransactionsList">;

const KIND_LABELS: Record<TransactionKind, string> = {
  all: "Todo",
  income: "Ingresos",
  expense: "Egresos",
};

function fmtAmt(amount: number, currency: string): string {
  const sym = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(amount);
  const s =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${sym} ${s}`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "2-digit", month: "short" });
}

function TransactionRow({
  tx,
  onPress,
}: {
  tx: TransactionResponse;
  onPress: () => void;
}) {
  const isExpense = tx.amount < 0;
  const label = tx.merchant ?? tx.description ?? "Sin descripción";

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.75 }]}
    >
      <View style={styles.rowLeft}>
        <Text style={styles.rowLabel} numberOfLines={1}>
          {label}
        </Text>
        <View style={styles.rowMeta}>
          {tx.archived && (
            <View style={styles.archivedPill}>
              <Text style={styles.archivedPillText}>Archivado</Text>
            </View>
          )}
          {tx.status === "shadow" && (
            <View style={styles.shadowPill}>
              <Text style={styles.shadowPillText}>Pendiente</Text>
            </View>
          )}
          {tx.category != null && !tx.archived && tx.status !== "shadow" && (
            <Text style={styles.rowCategory} numberOfLines={1}>
              {tx.category}
            </Text>
          )}
        </View>
      </View>
      <View style={styles.rowRight}>
        <Text
          style={[
            styles.rowAmount,
            { color: isExpense ? Colors.expense : Colors.income },
          ]}
        >
          {isExpense ? "−" : "+"}
          {fmtAmt(Math.abs(tx.amount), tx.currency)}
        </Text>
        <Text style={styles.rowDate}>{fmtDate(tx.transaction_date)}</Text>
      </View>
    </Pressable>
  );
}

export function TransactionsScreen() {
  const nav = useNavigation<Nav>();
  const route = useRoute<Route>();
  const qc = useQueryClient();

  const [filters, setFilters] = useState<TransactionFilters>(DEFAULT_FILTERS);
  const [accountPickerOpen, setAccountPickerOpen] = useState(false);

  // Handoff from chat ("movimientos sin cuenta"): apply the Sin cuenta filter
  // once, then clear the param so a re-focus doesn't re-apply it.
  useEffect(() => {
    if (route.params?.filterNoAccount) {
      setFilters((f) => ({ ...f, noAccount: true, accountId: null }));
      setAccountPickerOpen(false);
      nav.setParams({ filterNoAccount: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.params?.filterNoAccount]);

  const { data: accounts } = useQuery({
    queryKey: ["accounts", { archived: false }],
    queryFn: () => fetchAccounts(false),
  });

  const {
    data: pages,
    isLoading,
    isFetchingNextPage,
    fetchNextPage,
    hasNextPage,
    refetch,
    isRefetching,
    error,
  } = useInfiniteQuery({
    queryKey: ["transactions", filters],
    queryFn: ({ pageParam }) =>
      fetchTransactions(filters, pageParam as string | undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const transactions = pages?.pages.flatMap((p) => p.items) ?? [];

  const setKind = (kind: TransactionKind) =>
    setFilters((f) => ({ ...f, kind }));

  const setAccount = (id: string | null) => {
    setFilters((f) => ({ ...f, accountId: id, noAccount: false }));
    setAccountPickerOpen(false);
  };

  const setNoAccount = () => {
    setFilters((f) => ({ ...f, accountId: null, noAccount: true }));
    setAccountPickerOpen(false);
  };

  const activeAccount = accounts?.find((a) => a.id === filters.accountId);
  const hasActiveFilter =
    filters.kind !== "all" || filters.accountId != null || filters.noAccount;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* ── header ──────────────────────────────────────────────────────── */}
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Movimientos</Text>
          {!isLoading && (
            <Text style={styles.headerSub}>
              {filters.noAccount
                ? "Sin cuenta"
                : hasActiveFilter
                  ? "Filtros activos"
                  : "Todos los movimientos"}
            </Text>
          )}
        </View>
        <View style={styles.headerActions}>
          {hasActiveFilter && (
            <Pressable
              onPress={() => {
                setFilters(DEFAULT_FILTERS);
                setAccountPickerOpen(false);
              }}
              style={({ pressed }) => [styles.iconBtn, pressed && { opacity: 0.7 }]}
            >
              <Feather name="x" size={15} color={Colors.expense} />
            </Pressable>
          )}
          <Pressable
            onPress={() => setAccountPickerOpen((v) => !v)}
            style={({ pressed }) => [
              styles.iconBtn,
              accountPickerOpen && styles.iconBtnActive,
              pressed && { opacity: 0.7 },
            ]}
          >
            <Feather
              name="filter"
              size={15}
              color={accountPickerOpen ? Colors.accent : Colors.textMuted}
            />
          </Pressable>
        </View>
      </View>

      {/* ── kind pills ──────────────────────────────────────────────────── */}
      <View style={styles.kindRow}>
        {(["all", "income", "expense"] as TransactionKind[]).map((k) => (
          <Pressable
            key={k}
            onPress={() => setKind(k)}
            style={({ pressed }) => [
              styles.kindPill,
              filters.kind === k && styles.kindPillActive,
              pressed && { opacity: 0.75 },
            ]}
          >
            <Text
              style={[
                styles.kindLabel,
                filters.kind === k && styles.kindLabelActive,
              ]}
            >
              {KIND_LABELS[k]}
            </Text>
          </Pressable>
        ))}
        {activeAccount && (
          <View style={styles.accountChip}>
            <Feather name="credit-card" size={11} color={Colors.accent} />
            <Text style={styles.accountChipLabel} numberOfLines={1}>
              {activeAccount.name}
            </Text>
          </View>
        )}
        {filters.noAccount && (
          <View style={styles.accountChip}>
            <Feather name="alert-circle" size={11} color={Colors.accent} />
            <Text style={styles.accountChipLabel} numberOfLines={1}>
              Sin cuenta
            </Text>
          </View>
        )}
      </View>

      {/* ── account picker ───────────────────────────────────────────────── */}
      {accountPickerOpen && (
        <View style={styles.accountPickerBox}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.accountPickerScroll}
          >
            <Pressable
              onPress={() => setAccount(null)}
              style={({ pressed }) => [
                styles.accountOption,
                filters.accountId == null &&
                  !filters.noAccount &&
                  styles.accountOptionActive,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Text
                style={[
                  styles.accountOptionLabel,
                  filters.accountId == null &&
                    !filters.noAccount &&
                    styles.accountOptionLabelActive,
                ]}
              >
                Todas
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setNoAccount()}
              style={({ pressed }) => [
                styles.accountOption,
                filters.noAccount && styles.accountOptionActive,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Text
                style={[
                  styles.accountOptionLabel,
                  filters.noAccount && styles.accountOptionLabelActive,
                ]}
              >
                Sin cuenta
              </Text>
            </Pressable>
            {(accounts ?? []).filter((a) => !a.archived).map((a) => (
              <Pressable
                key={a.id}
                onPress={() => setAccount(a.id)}
                style={({ pressed }) => [
                  styles.accountOption,
                  filters.accountId === a.id && styles.accountOptionActive,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Text
                  style={[
                    styles.accountOptionLabel,
                    filters.accountId === a.id && styles.accountOptionLabelActive,
                  ]}
                  numberOfLines={1}
                >
                  {a.name}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      )}

      {/* ── list ────────────────────────────────────────────────────────── */}
      <FlatList
        data={transactions}
        keyExtractor={(tx) => tx.id}
        renderItem={({ item }) => (
          <TransactionRow
            tx={item}
            onPress={() => nav.navigate("TransactionDetail", { transaction: item })}
          />
        )}
        ItemSeparatorComponent={() => <View style={styles.separator} />}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={async () => {
              await refetch();
              qc.invalidateQueries({ queryKey: ["dashboard"] });
            }}
            tintColor={Colors.accent}
          />
        }
        ListEmptyComponent={
          isLoading ? (
            <ActivityIndicator
              color={Colors.accent}
              style={{ marginTop: 60 }}
            />
          ) : error ? (
            <View style={styles.centerBox}>
              <Feather name="alert-circle" size={20} color={Colors.expense} />
              <Text style={styles.errorText}>No se pudo cargar los movimientos.</Text>
            </View>
          ) : (
            <View style={styles.centerBox}>
              <Feather name="inbox" size={32} color={Colors.border} />
              <Text style={styles.emptyTitle}>Sin movimientos</Text>
              <Text style={styles.emptyBody}>
                {filters.noAccount
                  ? "No tenés movimientos sin cuenta. Todo está asignado."
                  : hasActiveFilter
                    ? "No hay movimientos que coincidan con los filtros."
                    : "Registrá tu primer movimiento desde el chat."}
              </Text>
            </View>
          )
        }
        ListFooterComponent={
          hasNextPage ? (
            <Pressable
              onPress={() => fetchNextPage()}
              disabled={isFetchingNextPage}
              style={({ pressed }) => [styles.loadMore, pressed && { opacity: 0.7 }]}
            >
              {isFetchingNextPage ? (
                <ActivityIndicator size="small" color={Colors.accent} />
              ) : (
                <Text style={styles.loadMoreText}>Cargar más</Text>
              )}
            </Pressable>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },

  // ── header ────────────────────────────────────────────────────────────────
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bg,
  },
  headerTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerSub: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 1,
  },
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  iconBtn: {
    width: 36,
    height: 36,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
    justifyContent: "center",
    alignItems: "center",
  },
  iconBtnActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },

  // ── kind pills ────────────────────────────────────────────────────────────
  kindRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
    backgroundColor: Colors.bg,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  kindPill: {
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs + 2,
  },
  kindPillActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  kindLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  kindLabelActive: {
    color: Colors.accent,
    fontWeight: "600",
  },
  accountChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 2,
    maxWidth: 160,
  },
  accountChipLabel: {
    fontSize: FontSize.xs,
    color: Colors.accent,
    fontWeight: "500",
    flexShrink: 1,
  },

  // ── account picker ────────────────────────────────────────────────────────
  accountPickerBox: {
    backgroundColor: Colors.bgCard,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  accountPickerScroll: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
    flexDirection: "row",
  },
  accountOption: {
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs + 2,
    maxWidth: 160,
  },
  accountOptionActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  accountOptionLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  accountOptionLabelActive: {
    color: Colors.accent,
    fontWeight: "600",
  },

  // ── list ──────────────────────────────────────────────────────────────────
  listContent: {
    flexGrow: 1,
    paddingBottom: Spacing.xl,
  },
  separator: {
    height: 1,
    backgroundColor: Colors.borderLight,
    marginHorizontal: Spacing.md,
  },

  // ── row ───────────────────────────────────────────────────────────────────
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
    backgroundColor: Colors.bg,
  },
  rowLeft: {
    flex: 1,
    gap: 3,
  },
  rowLabel: {
    fontSize: FontSize.md,
    fontWeight: "500",
    color: Colors.textPrimary,
  },
  rowMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    flexWrap: "wrap",
  },
  rowCategory: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  archivedPill: {
    borderRadius: Radius.sm,
    backgroundColor: Colors.bgElevated,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  archivedPillText: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  shadowPill: {
    borderRadius: Radius.sm,
    backgroundColor: Colors.warningBg,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  shadowPillText: {
    fontSize: FontSize.xs,
    color: Colors.warning,
    fontWeight: "500",
  },
  rowRight: {
    alignItems: "flex-end",
    gap: 2,
  },
  rowAmount: {
    fontSize: FontSize.md,
    fontWeight: "600",
    fontVariant: ["tabular-nums"],
  },
  rowDate: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },

  // ── empty / load more ──────────────────────────────────────────────────────
  centerBox: {
    alignItems: "center",
    gap: Spacing.sm,
    paddingTop: 60,
    paddingHorizontal: Spacing.xl,
  },
  emptyTitle: {
    fontSize: FontSize.lg,
    fontWeight: "600",
    color: Colors.textSecondary,
  },
  emptyBody: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
  },
  errorText: {
    fontSize: FontSize.sm,
    color: Colors.expense,
  },
  loadMore: {
    alignItems: "center",
    paddingVertical: Spacing.md,
    marginTop: Spacing.sm,
  },
  loadMoreText: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "500",
  },
});
