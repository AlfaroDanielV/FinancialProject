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
import { fetchCardAnalysis, fetchCardTerms } from "../api/cardTerms";
import { AccountDeleteConfirmModal } from "../components/AccountDeleteConfirmModal";
import { AccountEditModal } from "../components/AccountEditModal";
import { CardTermsEditModal } from "../components/CardTermsEditModal";
import { ReanchorModal } from "../components/ReanchorModal";
import { TransferModal } from "../components/TransferModal";
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
  const [payVisible, setPayVisible] = useState(false);
  const [editVisible, setEditVisible] = useState(false);
  const [deleteVisible, setDeleteVisible] = useState(false);
  const [termsVisible, setTermsVisible] = useState(false);
  const [reanchorVisible, setReanchorVisible] = useState(false);

  const { data: account, isLoading: accountLoading, refetch: refetchAccount } = useQuery({
    queryKey: ["account", accountId],
    queryFn: () => fetchAccount(accountId),
  });

  const isCredit = account?.account_type === "credit";
  const { data: cardTerms } = useQuery({
    queryKey: ["cardTerms", accountId],
    queryFn: () => fetchCardTerms(accountId),
    enabled: isCredit,
  });
  const { data: analysis } = useQuery({
    queryKey: ["cardAnalysis", accountId],
    queryFn: () => fetchCardAnalysis(accountId),
    enabled: isCredit && cardTerms != null,
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
      {/* ── edit + archive actions ───────────────────────────────────────── */}
      <View style={styles.headerActions}>
        <Pressable
          onPress={() => setEditVisible(true)}
          style={styles.archiveHitArea}
        >
          <Text style={styles.archiveLink}>Editar</Text>
        </Pressable>
        <Pressable
          onPress={onArchivePress}
          style={styles.archiveHitArea}
          disabled={archiveMutation.isPending}
        >
          <Text style={styles.archiveLink}>
            {account.archived ? "Restaurar" : "Archivar"}
          </Text>
        </Pressable>
      </View>

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
            {/* ── confirm-your-balance nudge (migrated accounts, A6) ───────── */}
            {account.needs_balance_confirmation &&
              !isCredit &&
              !account.archived && (
                <Pressable
                  onPress={() => setReanchorVisible(true)}
                  style={({ pressed }) => [
                    styles.nudge,
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  <Feather name="alert-circle" size={16} color={Colors.accent} />
                  <Text style={styles.nudgeText}>
                    Confirmá tu saldo real — tocá para poner el que te aparece
                    hoy en el banco.
                  </Text>
                </Pressable>
              )}
            {/* ── balance header ──────────────────────────────────────────── */}
            <View style={styles.balanceHeader}>
              <Text style={styles.accountName}>{account.name}</Text>
              <Text style={styles.accountType}>
                {ACCOUNT_TYPE_LABELS[account.account_type] ?? account.account_type}
                {" · "}
                {account.currency}
              </Text>
              {isCredit ? (
                // Phase 7b: card clarity for the least-engaged user — show
                // what's OWED as a plain positive figure, never a raw negative.
                <>
                  <Text
                    style={[
                      styles.balance,
                      bal < 0 && { color: Colors.expense },
                    ]}
                  >
                    {bal < 0
                      ? `Debés ${fmt(Math.abs(bal), account.currency)}`
                      : bal === 0
                        ? "Sin deuda"
                        : `A favor ${fmt(bal, account.currency)}`}
                  </Text>
                  {cardTerms?.credit_limit != null && bal <= 0 && (
                    <Text style={styles.availableLine}>
                      Disponible{" "}
                      {fmt(
                        Math.max(
                          0,
                          parseFloat(cardTerms.credit_limit) + bal,
                        ),
                        account.currency,
                      )}{" "}
                      de {fmt(parseFloat(cardTerms.credit_limit), account.currency)}
                    </Text>
                  )}
                </>
              ) : (
                <Text
                  style={[styles.balance, bal < 0 && { color: Colors.expense }]}
                >
                  {fmt(bal, account.currency)}
                </Text>
              )}
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
              {!isCredit && !account.archived && (
                <Pressable
                  onPress={() => setReanchorVisible(true)}
                  style={({ pressed }) => [
                    styles.reanchorLink,
                    pressed && { opacity: 0.7 },
                  ]}
                >
                  <Feather name="edit-3" size={13} color={Colors.accent} />
                  <Text style={styles.reanchorLinkText}>Corregí mi saldo</Text>
                </Pressable>
              )}
              {account.archived && (
                <View style={styles.archivedBadge}>
                  <Feather name="archive" size={12} color={Colors.textMuted} />
                  <Text style={styles.archivedBadgeText}>Archivada</Text>
                </View>
              )}
              {account.account_type === "credit" && !account.archived && (
                <Pressable
                  onPress={() => setPayVisible(true)}
                  style={({ pressed }) => [
                    styles.payBtn,
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  <Feather name="arrow-down-circle" size={16} color={Colors.textOnDark} />
                  <Text style={styles.payBtnText}>Registrar pago</Text>
                </Pressable>
              )}
            </View>

            {/* ── card terms (credit accounts, Phase 7b) ───────────────────── */}
            {isCredit && (
              <View style={styles.termsCard}>
                <View style={styles.termsHeader}>
                  <Text style={styles.sectionTitle}>TÉRMINOS DE LA TARJETA</Text>
                  <Pressable onPress={() => setTermsVisible(true)}>
                    <Text style={styles.termsEditLink}>
                      {cardTerms ? "Editar" : "Agregar"}
                    </Text>
                  </Pressable>
                </View>
                {cardTerms ? (
                  <View style={styles.termsGrid}>
                    <TermRow
                      label="Tasa anual de compras"
                      value={`${(parseFloat(cardTerms.annual_interest_rate) * 100).toFixed(2).replace(/\.00$/, "")}%`}
                    />
                    <TermRow
                      label="Pago mínimo"
                      value={`${(parseFloat(cardTerms.minimum_payment_pct) * 100).toFixed(2).replace(/\.00$/, "")}% del saldo${
                        cardTerms.minimum_payment_floor != null
                          ? ` (mín. ${fmt(parseFloat(cardTerms.minimum_payment_floor), account.currency)})`
                          : ""
                      }`}
                    />
                    {cardTerms.statement_day != null && (
                      <TermRow
                        label="Día de corte"
                        value={`${cardTerms.statement_day}`}
                      />
                    )}
                    <TermRow
                      label="Día límite de pago"
                      value={`${cardTerms.payment_due_day}`}
                    />
                  </View>
                ) : (
                  <Text style={styles.termsEmpty}>
                    Agregá la tasa y el pago mínimo para ver cuánto te cuesta
                    pagar solo el mínimo.
                  </Text>
                )}
              </View>
            )}

            {/* ── card analysis (Phase 7b B4) ──────────────────────────────── */}
            {isCredit && analysis != null && parseFloat(analysis.balance_owed) > 0 && (
              <View style={styles.termsCard}>
                <Text style={styles.sectionTitle}>SI PAGÁS SOLO EL MÍNIMO</Text>
                <Text style={styles.analysisLine}>
                  Pago mínimo de hoy:{" "}
                  <Text style={styles.analysisStrong}>
                    {fmt(parseFloat(analysis.minimum_payment), analysis.currency)}
                  </Text>
                </Text>
                <Text style={styles.analysisLine}>
                  Este mes en intereses:{" "}
                  <Text style={styles.analysisStrong}>
                    {fmt(parseFloat(analysis.monthly_interest_cost), analysis.currency)}
                  </Text>
                </Text>
                {analysis.never_pays_off ? (
                  <View style={styles.neverCard}>
                    <Feather name="alert-triangle" size={15} color={Colors.expense} />
                    <Text style={styles.neverText}>
                      Pagando solo el mínimo, esta tarjeta NUNCA se termina de
                      pagar — el mínimo no cubre ni los intereses del mes.
                    </Text>
                  </View>
                ) : (
                  analysis.months_to_payoff_minimum != null && (
                    <Text style={styles.analysisLine}>
                      Terminás de pagarla en{" "}
                      <Text style={styles.analysisStrong}>
                        {analysis.months_to_payoff_minimum} meses
                      </Text>
                      {analysis.total_interest_minimum != null && (
                        <>
                          {" "}y pagás{" "}
                          <Text style={[styles.analysisStrong, { color: Colors.expense }]}>
                            {fmt(
                              parseFloat(analysis.total_interest_minimum),
                              analysis.currency,
                            )}
                          </Text>{" "}
                          solo en intereses.
                        </>
                      )}
                    </Text>
                  )
                )}
                {analysis.strategies.map((s) => (
                  <View key={s.label} style={styles.strategyRow}>
                    <Text style={styles.strategyLabel}>
                      {s.label} ({fmt(parseFloat(s.monthly_payment), analysis.currency)}/mes)
                    </Text>
                    <Text style={styles.strategyValue}>
                      {s.months != null ? `${s.months} meses` : "no alcanza"}
                      {s.interest_saved_vs_minimum != null &&
                        parseFloat(s.interest_saved_vs_minimum) > 0 &&
                        ` · ahorrás ${fmt(
                          parseFloat(s.interest_saved_vs_minimum),
                          analysis.currency,
                        )}`}
                    </Text>
                  </View>
                ))}
              </View>
            )}

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
          <>
            {hasNextPage && (
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
            )}
            {/* ── danger zone: permanent delete (Phase 7b B2) ──────────── */}
            <Pressable
              onPress={() => setDeleteVisible(true)}
              style={({ pressed }) => [
                styles.dangerRow,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Feather name="trash-2" size={15} color={Colors.expense} />
              <Text style={styles.dangerText}>
                Eliminar cuenta definitivamente
              </Text>
            </Pressable>
          </>
        }
        contentContainerStyle={styles.listContent}
        ItemSeparatorComponent={() => <View style={styles.separator} />}
        showsVerticalScrollIndicator={false}
      />

      <TransferModal
        visible={payVisible}
        onClose={() => setPayVisible(false)}
        initialToAccountId={accountId}
      />
      <AccountEditModal
        visible={editVisible}
        account={account}
        onClose={() => setEditVisible(false)}
        onSaved={() => {
          setEditVisible(false);
          qc.invalidateQueries({ queryKey: ["accounts"] });
          qc.invalidateQueries({ queryKey: ["account", accountId] });
        }}
      />
      <AccountDeleteConfirmModal
        visible={deleteVisible}
        account={account}
        onClose={() => setDeleteVisible(false)}
        onDeleted={() => {
          setDeleteVisible(false);
          qc.invalidateQueries({ queryKey: ["accounts"] });
          qc.invalidateQueries({ queryKey: ["transactions"] });
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          nav.goBack();
        }}
      />
      {isCredit && (
        <CardTermsEditModal
          visible={termsVisible}
          accountId={accountId}
          terms={cardTerms ?? null}
          onClose={() => setTermsVisible(false)}
          onSaved={() => {
            setTermsVisible(false);
            qc.invalidateQueries({ queryKey: ["cardTerms", accountId] });
            qc.invalidateQueries({ queryKey: ["cardAnalysis", accountId] });
          }}
        />
      )}
      <ReanchorModal
        visible={reanchorVisible}
        account={account}
        onClose={() => setReanchorVisible(false)}
        onSaved={(delta) => {
          setReanchorVisible(false);
          qc.invalidateQueries({ queryKey: ["accounts"] });
          qc.invalidateQueries({ queryKey: ["account", accountId] });
          qc.invalidateQueries({ queryKey: ["accountTransactions", accountId] });
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          if (delta !== 0) {
            const sign = delta > 0 ? "+" : "−";
            Alert.alert(
              "Saldo actualizado",
              `Listo. Registré una diferencia de ${sign}${fmt(
                Math.abs(delta),
                account.currency,
              )} como ajuste de reconciliación para que cuadre con tu banco.`,
            );
          }
        }}
      />
    </SafeAreaView>
  );
}

function TermRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.termRow}>
      <Text style={styles.termLabel}>{label}</Text>
      <Text style={styles.termValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },

  headerActions: {
    flexDirection: "row",
    alignSelf: "flex-end",
  },
  archiveHitArea: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  archiveLink: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  dangerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.xs,
    marginTop: Spacing.xl,
    paddingVertical: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
  },
  dangerText: {
    fontSize: FontSize.sm,
    color: Colors.expense,
    fontWeight: "500",
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
  reanchorLink: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: Spacing.xs,
  },
  reanchorLinkText: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "500",
  },
  nudge: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    marginHorizontal: Spacing.md,
    marginTop: Spacing.sm,
  },
  nudgeText: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    lineHeight: 18,
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
  payBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.xs,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    marginTop: Spacing.sm,
  },
  payBtnText: {
    color: Colors.textOnDark,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
  availableLine: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },

  // ── card terms (Phase 7b) ─────────────────────────────────────────────────
  termsCard: {
    marginHorizontal: Spacing.md,
    marginBottom: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  termsHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  termsEditLink: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "600",
  },
  termsGrid: { gap: Spacing.xs },
  termRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: Spacing.sm,
  },
  termLabel: { fontSize: FontSize.sm, color: Colors.textMuted },
  termValue: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
    flexShrink: 1,
    textAlign: "right",
  },
  termsEmpty: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    lineHeight: 19,
  },

  // ── card analysis (Phase 7b B4) ───────────────────────────────────────────
  analysisLine: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 20,
  },
  analysisStrong: {
    fontWeight: "700",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  neverCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: Spacing.xs,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    padding: Spacing.sm,
  },
  neverText: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.expense,
    lineHeight: 19,
    fontWeight: "500",
  },
  strategyRow: {
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    paddingTop: Spacing.xs,
    gap: 1,
  },
  strategyLabel: { fontSize: FontSize.xs, color: Colors.textMuted },
  strategyValue: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
    fontVariant: ["tabular-nums"],
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
