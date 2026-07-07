/**
 * Phase 6f B9 — Transaction detail.
 *
 * Receives the TransactionResponse via navigation params (already in FlatList
 * cache — avoids an extra GET). Shows all fields. Archive/restore via bottom
 * action; shadow rows show a read-only notice instead of the archive button.
 */
import { useState } from "react";
import {
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp, NativeStackScreenProps } from "@react-navigation/native-stack";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveTransaction,
  deleteTransaction,
  restoreTransaction,
} from "../api/transactions";
import { fetchAccounts } from "../api/accounts";
import { ENVELOPE_CLASS_COLORS, fetchEnvelopes } from "../api/envelopes";
import { TransactionEditModal } from "../components/TransactionEditModal";
import { RegisterPaymentModal } from "../components/RegisterPaymentModal";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import type { TransactionsStackParamList } from "../navigation/TransactionsNavigator";

type Props = NativeStackScreenProps<TransactionsStackParamList, "TransactionDetail">;
type Nav = NativeStackNavigationProp<TransactionsStackParamList, "TransactionDetail">;

const SOURCE_LABELS: Record<string, string> = {
  manual: "Manual",
  telegram: "Telegram",
  email: "Correo",
  gmail: "Gmail",
  shortcut: "Atajo",
  import: "Importación",
  reconciled: "Conciliado",
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
  return d.toLocaleDateString("es-CR", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function DetailRow({
  icon,
  label,
  value,
  valueColor,
}: {
  icon: React.ComponentProps<typeof Feather>["name"];
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <View style={styles.detailRow}>
      <View style={styles.detailIcon}>
        <Feather name={icon} size={14} color={Colors.textMuted} />
      </View>
      <View style={styles.detailTexts}>
        <Text style={styles.detailLabel}>{label}</Text>
        <Text style={[styles.detailValue, valueColor ? { color: valueColor } : undefined]}>
          {value}
        </Text>
      </View>
    </View>
  );
}

export function TransactionDetailScreen({ route }: Props) {
  // Local copy so an inline edit reflects immediately (params are static).
  const [tx, setTx] = useState(route.params.transaction);
  const [editVisible, setEditVisible] = useState(false);
  const [registerVisible, setRegisterVisible] = useState(false);
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();

  const { data: accounts } = useQuery({
    queryKey: ["accounts", { archived: false }],
    queryFn: () => fetchAccounts(false),
    staleTime: 60_000,
  });
  const account = accounts?.find((a) => a.id === tx.account_id) ?? null;
  const accountName = account?.name ?? null;

  // Phase 7f: surface the envelope assignment prominently (named badge).
  const { data: envelopes } = useQuery({
    queryKey: ["envelopes", "active"],
    queryFn: () => fetchEnvelopes(false),
    enabled: tx.envelope_id != null,
    staleTime: 60_000,
  });
  const envelope =
    tx.envelope_id != null
      ? envelopes?.find((e) => e.id === tx.envelope_id) ?? null
      : null;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["transactions"] });
    qc.invalidateQueries({ queryKey: ["accounts"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const archiveMutation = useMutation({
    mutationFn: () => archiveTransaction(tx.id),
    onSuccess: () => { invalidate(); nav.goBack(); },
    onError: () => Alert.alert("Error", "No se pudo archivar el movimiento."),
  });

  const restoreMutation = useMutation({
    mutationFn: () => restoreTransaction(tx.id),
    onSuccess: () => { invalidate(); nav.goBack(); },
    onError: () => Alert.alert("Error", "No se pudo restaurar el movimiento."),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTransaction(tx.id),
    onSuccess: () => { invalidate(); nav.goBack(); },
    onError: (e: unknown) => {
      // Surface the backend 409 reason (shadow / transfer / goal / linked).
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      Alert.alert(
        "No se pudo eliminar",
        typeof detail === "string" ? detail : "Intentá de nuevo.",
      );
    },
  });

  const isPending =
    archiveMutation.isPending ||
    restoreMutation.isPending ||
    deleteMutation.isPending;
  const isExpense = tx.amount < 0;
  const isShadow = tx.status === "shadow";
  // A positive movement on a credit account is a card payment marked wrong as
  // income — offer to convert it into a real payment (transfer from a source
  // account). Exclude machine-managed flows (transfer legs — incl. the ones this
  // feature itself creates — and goal flows): they'd always 409. Shadow/archived
  // are already excluded by the surrounding action-bar branch.
  const isCreditIncome =
    tx.amount > 0 &&
    account?.account_type === "credit" &&
    tx.transfer_id == null &&
    tx.goal_id == null;

  const onArchive = () => {
    Alert.alert(
      "Archivar movimiento",
      "¿Archivar este movimiento? No se elimina del sistema.",
      [
        { text: "Cancelar", style: "cancel" },
        { text: "Archivar", style: "destructive", onPress: () => archiveMutation.mutate() },
      ],
    );
  };

  const onRestore = () => {
    Alert.alert(
      "Restaurar movimiento",
      "¿Restaurar y mostrar en el saldo activo?",
      [
        { text: "Cancelar", style: "cancel" },
        { text: "Restaurar", onPress: () => restoreMutation.mutate() },
      ],
    );
  };

  const onDelete = () => {
    Alert.alert(
      "Eliminar para siempre",
      "¿Eliminar este movimiento de forma permanente? No se puede deshacer.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Eliminar",
          style: "destructive",
          onPress: () => deleteMutation.mutate(),
        },
      ],
    );
  };

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        {/* ── amount hero ─────────────────────────────────────────────────── */}
        <View style={styles.amountHero}>
          <Text
            style={[
              styles.amountText,
              { color: isExpense ? Colors.expense : Colors.income },
            ]}
          >
            {isExpense ? "−" : "+"}
            {fmtAmt(Math.abs(tx.amount), tx.currency)}
          </Text>
          <Text style={styles.currencyLabel}>{tx.currency}</Text>
        </View>

        {/* ── badges ──────────────────────────────────────────────────────── */}
        <View style={styles.badges}>
          {tx.archived && (
            <View style={[styles.badge, styles.badgeNeutral]}>
              <Feather name="archive" size={11} color={Colors.textMuted} />
              <Text style={styles.badgeText}>Archivado</Text>
            </View>
          )}
          {isShadow && (
            <View style={[styles.badge, styles.badgeWarning]}>
              <Feather name="clock" size={11} color={Colors.warning} />
              <Text style={[styles.badgeText, { color: Colors.warning }]}>
                Pendiente de revisión
              </Text>
            </View>
          )}
          {envelope != null && (
            <View
              style={[
                styles.badge,
                {
                  backgroundColor:
                    ENVELOPE_CLASS_COLORS[envelope.envelope_class] + "22",
                },
              ]}
            >
              <Feather
                name="inbox"
                size={11}
                color={ENVELOPE_CLASS_COLORS[envelope.envelope_class]}
              />
              <Text
                style={[
                  styles.badgeText,
                  {
                    color: ENVELOPE_CLASS_COLORS[envelope.envelope_class],
                    fontWeight: "700",
                  },
                ]}
              >
                Sobre «{envelope.name}»
                {tx.envelope_auto_assigned ? " · auto" : ""}
              </Text>
            </View>
          )}
        </View>

        {/* ── detail rows ─────────────────────────────────────────────────── */}
        <View style={styles.card}>
          <DetailRow
            icon="calendar"
            label="Fecha"
            value={fmtDate(tx.transaction_date)}
          />
          {(tx.merchant ?? tx.description) != null && (
            <DetailRow
              icon="tag"
              label="Descripción"
              value={tx.merchant ?? tx.description ?? ""}
            />
          )}
          {tx.merchant != null && tx.description != null && tx.merchant !== tx.description && (
            <DetailRow
              icon="file-text"
              label="Detalle"
              value={tx.description}
            />
          )}
          {tx.category != null && (
            <DetailRow
              icon="grid"
              label="Categoría"
              value={
                tx.category_auto_assigned ? `${tx.category} (auto)` : tx.category
              }
            />
          )}
          {accountName != null && (
            <DetailRow
              icon="credit-card"
              label="Cuenta"
              value={accountName}
            />
          )}
          <DetailRow
            icon="zap"
            label="Origen"
            value={SOURCE_LABELS[tx.source] ?? tx.source}
          />
        </View>

        {/* ── shadow notice ────────────────────────────────────────────────── */}
        {isShadow && (
          <View style={styles.shadowNotice}>
            <Feather name="info" size={14} color={Colors.warning} />
            <Text style={styles.shadowNoticeText}>
              Este movimiento fue capturado en modo sombra. Aprobá o rechazá
              desde Telegram con{" "}
              <Text style={{ fontWeight: "700" }}>/aprobar_shadow</Text>.
            </Text>
          </View>
        )}
      </ScrollView>

      {/* ── bottom action ────────────────────────────────────────────────── */}
      {!isShadow && (
        <View style={styles.bottomBar}>
          {tx.archived ? (
            <Pressable
              onPress={onRestore}
              disabled={isPending}
              style={({ pressed }) => [
                styles.actionBtn,
                styles.actionBtnRestore,
                isPending && { opacity: 0.5 },
                pressed && { opacity: 0.75 },
              ]}
            >
              <Feather name="rotate-ccw" size={16} color={Colors.accent} />
              <Text style={[styles.actionBtnLabel, { color: Colors.accent }]}>
                Restaurar movimiento
              </Text>
            </Pressable>
          ) : (
            <>
              {isCreditIncome && (
                <Pressable
                  onPress={() => setRegisterVisible(true)}
                  disabled={isPending}
                  style={({ pressed }) => [
                    styles.actionBtn,
                    styles.actionBtnRestore,
                    { marginBottom: Spacing.sm },
                    isPending && { opacity: 0.5 },
                    pressed && { opacity: 0.75 },
                  ]}
                >
                  <Feather name="credit-card" size={16} color={Colors.accent} />
                  <Text style={[styles.actionBtnLabel, { color: Colors.accent }]}>
                    Registrar como pago
                  </Text>
                </Pressable>
              )}
              <View style={styles.bottomRow}>
                <Pressable
                  onPress={() => setEditVisible(true)}
                  disabled={isPending}
                  style={({ pressed }) => [
                    styles.actionBtn,
                    styles.actionBtnFlex,
                    styles.actionBtnEdit,
                    isPending && { opacity: 0.5 },
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  <Feather name="edit-2" size={16} color={Colors.bgCard} />
                  <Text style={[styles.actionBtnLabel, { color: Colors.bgCard }]}>
                    Editar
                  </Text>
                </Pressable>
                <Pressable
                  onPress={onArchive}
                  disabled={isPending}
                  style={({ pressed }) => [
                    styles.actionBtn,
                    styles.actionBtnFlex,
                    styles.actionBtnArchive,
                    isPending && { opacity: 0.5 },
                    pressed && { opacity: 0.75 },
                  ]}
                >
                  <Feather name="archive" size={16} color={Colors.textMuted} />
                  <Text style={styles.actionBtnLabel}>Archivar</Text>
                </Pressable>
              </View>
            </>
          )}
          {/* Permanent delete — distinct from Archivar (reversible). */}
          <Pressable
            onPress={onDelete}
            disabled={isPending}
            style={({ pressed }) => [
              styles.actionBtn,
              styles.actionBtnDelete,
              { marginTop: Spacing.sm },
              isPending && { opacity: 0.5 },
              pressed && { opacity: 0.75 },
            ]}
          >
            <Feather name="trash-2" size={16} color={Colors.expense} />
            <Text style={[styles.actionBtnLabel, { color: Colors.expense }]}>
              Eliminar definitivamente
            </Text>
          </Pressable>
        </View>
      )}

      <TransactionEditModal
        visible={editVisible}
        tx={tx}
        onClose={() => setEditVisible(false)}
        onSaved={(updated) => {
          setTx(updated);
          invalidate();
          setEditVisible(false);
        }}
      />

      <RegisterPaymentModal
        visible={registerVisible}
        tx={tx}
        onClose={() => setRegisterVisible(false)}
        onDone={() => {
          setRegisterVisible(false);
          invalidate();
          nav.goBack();
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  scroll: { flex: 1 },
  content: {
    padding: Spacing.md,
    gap: Spacing.md,
    paddingBottom: Spacing.xl,
  },

  // ── amount hero ───────────────────────────────────────────────────────────
  amountHero: {
    alignItems: "center",
    paddingVertical: Spacing.lg,
    gap: 4,
  },
  amountText: {
    fontSize: FontSize.display,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  currencyLabel: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    fontWeight: "500",
  },

  // ── badges ────────────────────────────────────────────────────────────────
  badges: {
    flexDirection: "row",
    justifyContent: "center",
    flexWrap: "wrap",
    gap: Spacing.sm,
    marginTop: -Spacing.sm,
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 3,
  },
  badgeNeutral: {
    backgroundColor: Colors.bgElevated,
  },
  badgeWarning: {
    backgroundColor: Colors.warningBg,
  },
  badgeText: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
  },

  // ── card ──────────────────────────────────────────────────────────────────
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    overflow: "hidden",
  },
  detailRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    gap: Spacing.sm,
  },
  detailIcon: {
    width: 20,
    alignItems: "center",
    marginTop: 2,
  },
  detailTexts: {
    flex: 1,
    gap: 2,
  },
  detailLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
  },
  detailValue: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },

  // ── shadow notice ─────────────────────────────────────────────────────────
  shadowNotice: {
    flexDirection: "row",
    gap: Spacing.sm,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.warning + "40",
    padding: Spacing.md,
    alignItems: "flex-start",
  },
  shadowNoticeText: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.warning,
    lineHeight: 20,
  },

  // ── bottom bar ────────────────────────────────────────────────────────────
  bottomBar: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  bottomRow: {
    flexDirection: "row",
    gap: Spacing.sm,
  },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.sm,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    borderWidth: 1,
  },
  actionBtnFlex: {
    flex: 1,
  },
  actionBtnEdit: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accent,
  },
  actionBtnArchive: {
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
  },
  actionBtnRestore: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  actionBtnDelete: {
    borderColor: Colors.expense + "55",
    backgroundColor: Colors.bg,
  },
  actionBtnLabel: {
    fontSize: FontSize.md,
    fontWeight: "500",
    color: Colors.textSecondary,
  },
});
