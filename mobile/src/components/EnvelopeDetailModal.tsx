/**
 * Envelope budgeting (Sobres) — detail + bulk-assign view.
 *
 * Opened by tapping an envelope on the Home tab. Shows the spend bar, an
 * "Editar" entry into the create/edit sheet, and **this month's expenses** with
 * a per-row toggle to assign / unassign the transaction to THIS envelope (a
 * transaction belongs to at most one envelope, so toggling on reassigns from
 * any other). Assignment goes through PATCH /transactions/{id}{envelope_id}.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  assignTransactionEnvelope,
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  envelopeProgress,
  type EnvelopeSummaryItem,
} from "../api/envelopes";
import { fetchMonthExpenses, type TransactionResponse } from "../api/transactions";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { EnvelopeEditModal } from "./EnvelopeEditModal";

interface Props {
  visible: boolean;
  item: EnvelopeSummaryItem | null;
  onClose: () => void;
}

export function EnvelopeDetailModal({ visible, item, onClose }: Props) {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);

  const expensesQuery = useQuery({
    queryKey: ["envelopes", "month-expenses"],
    queryFn: fetchMonthExpenses,
    enabled: visible && item != null,
  });

  const toggle = useMutation({
    mutationFn: ({ txId, envelopeId }: { txId: string; envelopeId: string | null }) =>
      assignTransactionEnvelope(txId, envelopeId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  if (item == null) return null;

  // Money-left bar: starts full, drains with each expense, red in the last 5%.
  const { remaining, fraction, low } = envelopeProgress(item);
  const color = ENVELOPE_CLASS_COLORS[item.envelope_class];

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable onPress={onClose} hitSlop={10} style={{ padding: 4 }}>
            <Feather name="x" size={22} color={Colors.textSecondary} />
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {item.name}
          </Text>
          <Pressable onPress={() => setEditOpen(true)} hitSlop={10} style={{ padding: 4 }}>
            <Feather name="edit-2" size={18} color={Colors.accent} />
          </Pressable>
        </View>

        {/* spend bar */}
        <View style={styles.summaryCard}>
          <View style={styles.summaryTop}>
            <View style={styles.classTag}>
              <View style={[styles.classDot, { backgroundColor: color }]} />
              <Text style={styles.classLabel}>
                {ENVELOPE_CLASS_LABELS[item.envelope_class]}
              </Text>
            </View>
            <Text style={[styles.summaryAmount, low && { color: Colors.expense }]}>
              {formatMoney(Math.max(0, remaining), item.currency)} de{" "}
              {formatMoney(item.limit_amount, item.currency)}
            </Text>
          </View>
          <View style={styles.barTrack}>
            <View
              style={[
                styles.barFill,
                { width: `${Math.round(fraction * 100)}%` as `${number}%` },
                { backgroundColor: low ? Colors.expense : color },
              ]}
            />
          </View>
          <Text style={styles.summarySub}>
            {item.over_limit
              ? `Te pasaste por ${formatMoney(item.spent - item.limit_amount, item.currency)}`
              : `Gastado: ${formatMoney(item.spent, item.currency)}`}
          </Text>
        </View>

        <Text style={styles.listTitle}>Gastos de este mes</Text>

        {expensesQuery.isLoading ? (
          <ActivityIndicator color={Colors.accent} style={{ marginTop: Spacing.xl }} />
        ) : expensesQuery.isError ? (
          <Text style={styles.muted}>No se pudieron cargar los gastos.</Text>
        ) : (expensesQuery.data ?? []).length === 0 ? (
          <Text style={styles.muted}>Sin gastos este mes.</Text>
        ) : (
          <FlatList
            data={expensesQuery.data}
            keyExtractor={(t) => t.id}
            contentContainerStyle={styles.listContent}
            renderItem={({ item: tx }) => (
              <ExpenseRow
                tx={tx}
                envelopeId={item.id}
                onToggle={(assigned) =>
                  toggle.mutate({
                    txId: tx.id,
                    envelopeId: assigned ? null : item.id,
                  })
                }
                pending={toggle.isPending}
              />
            )}
          />
        )}

        <EnvelopeEditModal
          visible={editOpen}
          envelope={item}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            onClose(); // the summary item is now stale; close back to the home tab
          }}
        />
      </SafeAreaView>
    </Modal>
  );
}

function ExpenseRow({
  tx,
  envelopeId,
  onToggle,
  pending,
}: {
  tx: TransactionResponse;
  envelopeId: string;
  onToggle: (assigned: boolean) => void;
  pending: boolean;
}) {
  const assignedHere = tx.envelope_id === envelopeId;
  const inOther = tx.envelope_id != null && tx.envelope_id !== envelopeId;
  const title = tx.merchant || tx.category || "Gasto";
  return (
    <Pressable
      onPress={() => !pending && onToggle(assignedHere)}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}
    >
      <Feather
        name={assignedHere ? "check-circle" : "circle"}
        size={20}
        color={assignedHere ? Colors.accent : Colors.border}
      />
      <View style={styles.rowMeta}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {title}
        </Text>
        <Text style={styles.rowSub}>
          {formatDate(tx.transaction_date)}
          {inOther ? " · en otro sobre" : ""}
        </Text>
      </View>
      <Text style={styles.rowAmount}>
        {formatMoney(Math.abs(tx.amount), tx.currency)}
      </Text>
    </Pressable>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short" });
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
  },
  headerTitle: {
    flex: 1,
    textAlign: "center",
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  summaryCard: {
    marginHorizontal: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  summaryTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  classTag: { flexDirection: "row", alignItems: "center", gap: Spacing.xs },
  classDot: { width: 9, height: 9, borderRadius: 5 },
  classLabel: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textSecondary,
  },
  summaryAmount: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  barTrack: { height: 6, backgroundColor: Colors.border, borderRadius: 3 },
  barFill: { height: 6, borderRadius: 3 },
  summarySub: { fontSize: FontSize.xs, color: Colors.textMuted },

  listTitle: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textMuted,
    paddingHorizontal: Spacing.md,
    marginTop: Spacing.lg,
    marginBottom: Spacing.xs,
  },
  listContent: { paddingHorizontal: Spacing.md, paddingBottom: Spacing.xl },
  muted: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    textAlign: "center",
    marginTop: Spacing.xl,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  rowMeta: { flex: 1 },
  rowTitle: { fontSize: FontSize.sm, color: Colors.textPrimary, fontWeight: "500" },
  rowSub: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 1 },
  rowAmount: {
    fontSize: FontSize.sm,
    color: Colors.expense,
    fontWeight: "500",
    fontVariant: ["tabular-nums"],
  },
});
