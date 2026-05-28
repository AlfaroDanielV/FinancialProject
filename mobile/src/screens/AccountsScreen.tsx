/**
 * Phase 6f B8 — Accounts list (Cuentas tab root).
 *
 * Shows active accounts with their current balance and month-start balance.
 * Tap an account → AccountDetailScreen.
 * "+" header button → AccountCreateScreen.
 * Toggle in header shows archived accounts.
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
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { Feather } from "@expo/vector-icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAccounts,
  ACCOUNT_TYPE_LABELS,
  type AccountResponse,
} from "../api/accounts";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { AccountsStackParamList } from "../navigation/AccountsNavigator";

type Nav = NativeStackNavigationProp<AccountsStackParamList, "AccountsList">;

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

function typeLabel(t: string): string {
  return ACCOUNT_TYPE_LABELS[t] ?? t;
}

function AccountCard({
  account,
  onPress,
}: {
  account: AccountResponse;
  onPress: () => void;
}) {
  const bal = parseFloat(account.current_balance ?? "0");
  const monthStart = parseFloat(account.month_start_balance ?? "0");
  const diff = bal - monthStart;
  const diffPositive = diff >= 0;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
    >
      <View style={styles.cardTop}>
        <View style={styles.cardLeft}>
          <Text style={styles.accountName} numberOfLines={1}>
            {account.name}
          </Text>
          <View style={styles.typePill}>
            <Text style={styles.typeLabel}>{typeLabel(account.account_type)}</Text>
          </View>
        </View>
        <View style={styles.cardRight}>
          <Text
            style={[
              styles.balance,
              bal < 0 && { color: Colors.expense },
            ]}
          >
            {fmt(bal, account.currency)}
          </Text>
          {account.month_start_balance != null && (
            <Text
              style={[
                styles.diffText,
                { color: diffPositive ? Colors.income : Colors.expense },
              ]}
            >
              {diffPositive ? "+" : "−"}
              {fmt(Math.abs(diff), account.currency)} este mes
            </Text>
          )}
        </View>
      </View>
      <View style={styles.cardArrow}>
        <Feather name="chevron-right" size={16} color={Colors.textMuted} />
      </View>
    </Pressable>
  );
}

export function AccountsScreen() {
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);

  const { data, isLoading, isRefetching, refetch, error } = useQuery({
    queryKey: ["accounts", { archived: showArchived }],
    queryFn: () => fetchAccounts(showArchived),
  });

  const active = (data ?? []).filter((a) => !a.archived);
  const archived = (data ?? []).filter((a) => a.archived);
  const displayed = showArchived ? data ?? [] : active;

  const totalBalance = active.reduce(
    (sum, a) => sum + parseFloat(a.current_balance ?? "0"),
    0,
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* ── header ──────────────────────────────────────────────────────── */}
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Cuentas</Text>
          {!isLoading && (
            <Text style={styles.headerSub}>
              {active.length} {active.length === 1 ? "cuenta activa" : "cuentas activas"}
            </Text>
          )}
        </View>
        <View style={styles.headerActions}>
          {archived.length > 0 && (
            <Pressable
              onPress={() => setShowArchived((v) => !v)}
              style={({ pressed }) => [
                styles.iconBtn,
                showArchived && styles.iconBtnActive,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Feather
                name="archive"
                size={16}
                color={showArchived ? Colors.accent : Colors.textMuted}
              />
            </Pressable>
          )}
          <Pressable
            onPress={() => nav.navigate("AccountCreate")}
            style={({ pressed }) => [styles.addBtn, pressed && { opacity: 0.8 }]}
          >
            <Feather name="plus" size={18} color={Colors.textOnDark} />
          </Pressable>
        </View>
      </View>

      {/* ── total balance strip ──────────────────────────────────────────── */}
      {!showArchived && !isLoading && active.length > 0 && (
        <View style={styles.totalStrip}>
          <Text style={styles.totalLabel}>SALDO CONSOLIDADO</Text>
          <Text
            style={[styles.totalAmount, totalBalance < 0 && { color: Colors.expense }]}
          >
            {fmt(totalBalance, "CRC")}
          </Text>
        </View>
      )}

      {/* ── list ────────────────────────────────────────────────────────── */}
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={async () => {
              await refetch();
              await qc.invalidateQueries({ queryKey: ["dashboard"] });
            }}
            tintColor={Colors.accent}
          />
        }
      >
        {isLoading ? (
          <ActivityIndicator color={Colors.accent} style={{ marginTop: 40 }} />
        ) : error ? (
          <View style={styles.errorBox}>
            <Feather name="alert-circle" size={20} color={Colors.expense} />
            <Text style={styles.errorText}>No se pudo cargar las cuentas.</Text>
          </View>
        ) : displayed.length === 0 ? (
          <View style={styles.empty}>
            <Feather name="credit-card" size={32} color={Colors.border} />
            <Text style={styles.emptyTitle}>Sin cuentas</Text>
            <Text style={styles.emptyBody}>
              Tocá{" "}
              <Feather name="plus" size={13} color={Colors.textMuted} />{" "}
              para agregar tu primera cuenta.
            </Text>
          </View>
        ) : (
          displayed.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              onPress={() => nav.navigate("AccountDetail", { accountId: account.id })}
            />
          ))
        )}
      </ScrollView>
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
  addBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: Colors.accent,
    justifyContent: "center",
    alignItems: "center",
  },

  // ── total strip ───────────────────────────────────────────────────────────
  totalStrip: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.bgCard,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
  },
  totalLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "600",
    letterSpacing: 0.8,
  },
  totalAmount: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },

  // ── list ──────────────────────────────────────────────────────────────────
  scroll: { flex: 1 },
  content: {
    padding: Spacing.md,
    gap: Spacing.sm,
    paddingBottom: Spacing.xl,
  },

  // ── account card ──────────────────────────────────────────────────────────
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    padding: Spacing.md,
    flexDirection: "row",
    alignItems: "center",
    ...CardShadow,
  },
  cardTop: {
    flex: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: Spacing.sm,
  },
  cardLeft: {
    flex: 1,
    gap: Spacing.xs,
  },
  cardRight: {
    alignItems: "flex-end",
    gap: 2,
  },
  cardArrow: {
    marginLeft: Spacing.sm,
  },
  accountName: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  typePill: {
    alignSelf: "flex-start",
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  typeLabel: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  balance: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  diffText: {
    fontSize: FontSize.xs,
    fontVariant: ["tabular-nums"],
  },

  // ── empty / error ─────────────────────────────────────────────────────────
  empty: {
    marginTop: 60,
    alignItems: "center",
    gap: Spacing.sm,
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
  errorBox: {
    marginTop: 40,
    alignItems: "center",
    gap: Spacing.sm,
  },
  errorText: {
    fontSize: FontSize.sm,
    color: Colors.expense,
  },
});
