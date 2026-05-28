/**
 * Phase 6f B8 — Account detail.
 *
 * Shows balance header, month diff, and paginated transaction list.
 * Swipe-down pulls the next page (load more button). Archive/restore via
 * header action.
 */
import { useState, useCallback } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation, useRoute } from "@react-navigation/native";
import type { NativeStackNavigationProp, NativeStackScreenProps } from "@react-navigation/native-stack";
import { Feather } from "@expo/vector-icons";
import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from "@tanstack/react-query";

import {
  fetchAccount,
  updateAccount,
  archiveAccount,
  fetchAccountTransactions,
  ACCOUNT_TYPE_LABELS,
  type TransactionResponse,
} from "../api/accounts";
import { Colors, FontSize, Radius, Spacing, CardShadow } from "../theme";
import type { AccountsStackParamList } from "../navigation/AccountsNavigator";

type Props = NativeStackScreenProps<AccountsStackParamList, "AccountDetail">;
type Nav = NativeStackNavigationProp<AccountsStackParamList, "AccountDetail">;

function fmt(value: string | number | null | undefined, currency: string): string {
  const num =
    value == null ? 0 : typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "—";
  const sym = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(num);
  const s =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${num < 0 ? "−" : ""}${sym} ${s}`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "2-digit", month: "short" });
}

function TransactionRow({ tx }: { tx: TransactionResponse }) {
  const isExpense = tx.amount < 0;
  const amtStr = fmt(Math.abs(tx.amount), tx.currency);

  return (
    <View style={styles.txRow}>
      <View style={styles.txLeft}>
        <Text style={styles.txMerchant} numberOfLines={1}>
          {tx.merchant ?? tx.description ?? "Sin descripción"}
        </Text>
        {tx.category != null && (
          <Text style={styles.txCategory} numberOfLines={1}>
            {tx.category}
          </Text>
        )}
      </View>
      <View style={styles.txRight}>
        <Text
          style={[
            styles.txAmount,
            { color: isExpense ? Colors.expense : Colors.income },
          ]}
        >
          {isExpense ? "−" : "+"}
          {amtStr}
        </Text>
        <Text style={styles.txDate}>{fmtDate(tx.transaction_date)}</Text>
      </View>
    </View>
  );
}

export function AccountDetailScreen({ route }: Props) {
  const { accountId } = route.params;
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();

  const { data: account, isLoading: accountLoading, refetch: refetchAccount } = useQuery({
    queryKey: ["account", accountId],
    queryFn: () => fetchAccount(accountId),
  });

  const {
    data: txPages,
    isLoading: txLoading,
    isFetchingNextPage,
    fetchNextPage,
    hasNextPage,
    refetch: refetchTx,
  } = useInfiniteQuery({
    queryKey: ["accountTransactions", accountId],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      fetchAccountTransactions(accountId, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const transactions = txPages?.pages.flatMap((p) => p.items) ?? [];

  const archiveMutation = useMutation({
    mutationFn: () =>
      account?.archived
        ? updateAccount(accountId, { archived: false, is_active: true })
        : archiveAccount(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account", accountId] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      nav.goBack();
    },
    onError: () => {
      Alert.alert("Error", "No se pudo actualizar la cuenta. Intentá de nuevo.");
    },
  });

  const onArchivePress = () => {
    const isArchived = account?.archived ?? false;
    Alert.alert(
      isArchived ? "Restaurar cuenta" : "Archivar cuenta",
      isArchived
        ? "¿Restaurar esta cuenta y hacerla activa?"
        : "¿Archivar esta cuenta? Podés restaurarla después.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: isArchived ? "Restaurar" : "Archivar",
          style: isArchived ? "default" : "destructive",
          onPress: () => archiveMutation.mutate(),
        },
      ],
    );
  };

  const onRefresh = useCallback(async () => {
    await Promise.all([refetchAccount(), refetchTx()]);
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  }, [refetchAccount, refetchTx, qc]);

  if (accountLoading || !account) {
    return (
      <SafeAreaView style={styles.safe} edges={["bottom"]}>
        <ActivityIndicator color={Colors.accent} style={{ marginTop: 60 }} />
      </SafeAreaView>
    );
  }

  const bal = parseFloat(account.current_balance ?? "0");
  const monthStart = parseFloat(account.month_start_balance ?? "0");
  const diff = bal - monthStart;
  const diffPositive = diff >= 0;

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      {/* ── archive action ───────────────────────────────────────────────── */}
      <Pressable
        onPress={onArchivePress}
        style={styles.archiveHitArea}
        disabled={archiveMutation.isPending}
      >
        <Text style={styles.archiveLink}>
          {account.archived ? "Restaurar" : "Archivar"}
        </Text>
      </Pressable>

      <FlatList
        data={transactions}
        keyExtractor={(tx) => tx.id}
        renderItem={({ item }) => <TransactionRow tx={item} />}
        refreshControl={
          <RefreshControl
            refreshing={false}
            onRefresh={onRefresh}
            tintColor={Colors.accent}
          />
        }
        ListHeaderComponent={
          <>
            {/* ── balance header ──────────────────────────────────────────── */}
            <View style={styles.balanceHeader}>
              <Text style={styles.accountName}>{account.name}</Text>
              <Text style={styles.accountType}>
                {ACCOUNT_TYPE_LABELS[account.account_type] ?? account.account_type}
                {" · "}
                {account.currency}
              </Text>
              <Text
                style={[styles.balance, bal < 0 && { color: Colors.expense }]}
              >
                {fmt(bal, account.currency)}
              </Text>
              {account.month_start_balance != null && (
                <Text
                  style={[
                    styles.monthDiff,
                    { color: diffPositive ? Colors.income : Colors.expense },
                  ]}
                >
                  {diffPositive ? "+" : "−"}
                  {fmt(Math.abs(diff), account.currency)} este mes
                </Text>
              )}
              {account.archived && (
                <View style={styles.archivedBadge}>
                  <Feather name="archive" size={12} color={Colors.textMuted} />
                  <Text style={styles.archivedBadgeText}>Archivada</Text>
                </View>
              )}
            </View>

            {/* ── transactions heading ─────────────────────────────────────── */}
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>MOVIMIENTOS</Text>
              {txLoading && (
                <ActivityIndicator size="small" color={Colors.accent} />
              )}
            </View>
          </>
        }
        ListEmptyComponent={
          !txLoading ? (
            <View style={styles.emptyTx}>
              <Feather name="inbox" size={28} color={Colors.border} />
              <Text style={styles.emptyTxText}>Sin movimientos registrados.</Text>
            </View>
          ) : null
        }
        ListFooterComponent={
          hasNextPage ? (
            <Pressable
              onPress={() => fetchNextPage()}
              disabled={isFetchingNextPage}
              style={({ pressed }) => [
                styles.loadMore,
                pressed && { opacity: 0.7 },
              ]}
            >
              {isFetchingNextPage ? (
                <ActivityIndicator size="small" color={Colors.accent} />
              ) : (
                <Text style={styles.loadMoreText}>Cargar más</Text>
              )}
            </Pressable>
          ) : null
        }
        contentContainerStyle={styles.listContent}
        ItemSeparatorComponent={() => <View style={styles.separator} />}
        showsVerticalScrollIndicator={false}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },

  archiveHitArea: {
    alignSelf: "flex-end",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  archiveLink: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },

  // ── balance header ────────────────────────────────────────────────────────
  balanceHeader: {
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.lg,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    marginBottom: Spacing.sm,
    gap: Spacing.xs,
  },
  accountName: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  accountType: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  balance: {
    fontSize: FontSize.xl + 4,
    fontWeight: "700",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
    marginTop: Spacing.xs,
  },
  monthDiff: {
    fontSize: FontSize.sm,
    fontVariant: ["tabular-nums"],
  },
  archivedBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: Spacing.xs,
  },
  archivedBadgeText: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },

  // ── section ───────────────────────────────────────────────────────────────
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    marginBottom: Spacing.xs,
  },
  sectionTitle: {
    fontSize: FontSize.xs,
    fontWeight: "600",
    color: Colors.textMuted,
    letterSpacing: 0.8,
  },

  // ── transaction row ───────────────────────────────────────────────────────
  txRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
  },
  txLeft: {
    flex: 1,
    gap: 2,
  },
  txMerchant: {
    fontSize: FontSize.md,
    fontWeight: "500",
    color: Colors.textPrimary,
  },
  txCategory: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  txRight: {
    alignItems: "flex-end",
    gap: 2,
  },
  txAmount: {
    fontSize: FontSize.md,
    fontWeight: "600",
    fontVariant: ["tabular-nums"],
  },
  txDate: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  separator: {
    height: 1,
    backgroundColor: Colors.borderLight,
    marginHorizontal: Spacing.md,
  },

  // ── empty / load more ──────────────────────────────────────────────────────
  emptyTx: {
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.xl,
  },
  emptyTxText: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
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

  listContent: {
    flexGrow: 1,
    paddingBottom: Spacing.xl,
  },
});
