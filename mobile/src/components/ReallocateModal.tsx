/**
 * Envelope budgeting (Sobres) — over-limit reallocation (Phase 8 B6).
 *
 * Reuses the B4 reallocation primitive (POST /envelopes/reallocate) to turn the
 * punitive "te pasaste por ₡X" dead-end into a decision: "¿cubrís moviendo de
 * otro sobre?". The user picks a SAME-LEVEL, same-currency source sobre with
 * budget to spare and an editable amount (default = the shortfall); the backend
 * does the deterministic, byte-lock-safe move and 422s an invalid one.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  ENVELOPE_CLASS_COLORS,
  reallocateEnvelopes,
  type EnvelopeSummaryItem,
} from "../api/envelopes";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";

interface Props {
  visible: boolean;
  over: EnvelopeSummaryItem | null;
  // Same-level, same-currency sobres with available > 0 (caller filters from
  // the summary). The source the user pulls budget FROM.
  candidates: EnvelopeSummaryItem[];
  onClose: () => void;
  onDone: () => void;
}

function availableOf(e: EnvelopeSummaryItem): number {
  return e.available ?? e.remaining ?? 0;
}

export function ReallocateModal({ visible, over, candidates, onClose, onDone }: Props) {
  const qc = useQueryClient();
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);

  const shortfall = over ? Math.max(0, Math.round(over.spent - over.limit_amount)) : 0;
  // Most slack first.
  const ranked = useMemo(
    () => [...candidates].sort((a, b) => availableOf(b) - availableOf(a)),
    [candidates],
  );

  useEffect(() => {
    if (visible) {
      setSourceId(ranked[0]?.id ?? null);
      setAmount(shortfall > 0 ? String(shortfall) : "");
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, over?.id]);

  const move = useMutation({
    mutationFn: ({ from, amt }: { from: string; amt: number }) =>
      reallocateEnvelopes(from, over!.id, amt),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      onDone();
    },
  });

  if (over == null) return null;
  const currency = over.currency;

  const submit = () => {
    if (sourceId == null) {
      setError("Elegí un sobre de origen.");
      return;
    }
    const parsed = Number(amount.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("Poné un monto mayor que cero.");
      return;
    }
    setError(null);
    move.mutate({ from: sourceId, amt: parsed });
  };

  const serverError =
    (move.error as { response?: { data?: { detail?: unknown } } } | null)?.response
      ?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (move.isError ? "No se pudo mover. Intentá de nuevo." : null);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>¿Cubrís moviendo de otro sobre?</Text>
          <Text style={styles.subtitle}>
            «{over.name}» se pasó por {formatMoney(shortfall, currency)}. Movés
            presupuesto desde otro sobre del mismo nivel para cubrirlo.
          </Text>

          {ranked.length === 0 ? (
            <Text style={styles.muted}>
              No tenés otro sobre del mismo nivel con saldo disponible para mover.
              Subí el límite de este sobre o ajustá tus gastos.
            </Text>
          ) : (
            <>
              <Text style={styles.label}>Mover desde</Text>
              <ScrollView style={styles.list} keyboardShouldPersistTaps="handled">
                {ranked.map((c) => {
                  const active = c.id === sourceId;
                  return (
                    <Pressable
                      key={c.id}
                      onPress={() => setSourceId(c.id)}
                      style={({ pressed }) => [
                        styles.sourceRow,
                        active && styles.sourceRowActive,
                        pressed && { opacity: 0.7 },
                      ]}
                    >
                      <View
                        style={[
                          styles.dot,
                          { backgroundColor: ENVELOPE_CLASS_COLORS[c.envelope_class] },
                        ]}
                      />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.sourceName} numberOfLines={1}>
                          {c.name}
                        </Text>
                        <Text style={styles.sourceSub}>
                          Disponible {formatMoney(availableOf(c), c.currency)}
                        </Text>
                      </View>
                      {active && (
                        <Feather name="check" size={18} color={Colors.accent} />
                      )}
                    </Pressable>
                  );
                })}
              </ScrollView>

              <Text style={styles.label}>Monto a mover ({currency})</Text>
              <AmountInput
                value={amount}
                onChangeValue={setAmount}
                style={styles.input}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
              />
            </>
          )}

          {errorText != null && <Text style={styles.error}>{errorText}</Text>}

          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={move.isPending}
              style={({ pressed }) => [styles.btn, styles.btnCancel, pressed && { opacity: 0.75 }]}
            >
              <Text style={styles.btnCancelText}>Cancelar</Text>
            </Pressable>
            {ranked.length > 0 && (
              <Pressable
                onPress={submit}
                disabled={move.isPending}
                style={({ pressed }) => [
                  styles.btn,
                  styles.btnSave,
                  move.isPending && { opacity: 0.6 },
                  pressed && { opacity: 0.85 },
                ]}
              >
                {move.isPending ? (
                  <ActivityIndicator color={Colors.bgCard} size="small" />
                ) : (
                  <Text style={styles.btnSaveText}>Mover</Text>
                )}
              </Pressable>
            )}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.xl,
    maxHeight: "82%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  title: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  subtitle: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    lineHeight: 17,
    marginTop: 4,
    marginBottom: Spacing.sm,
  },
  muted: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    lineHeight: 19,
    paddingVertical: Spacing.md,
  },
  label: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
    marginBottom: 4,
    marginTop: Spacing.sm,
  },
  list: { flexGrow: 0, maxHeight: 220 },
  sourceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    marginBottom: Spacing.xs,
    backgroundColor: Colors.bgCard,
  },
  sourceRowActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  dot: { width: 10, height: 10, borderRadius: 5 },
  sourceName: { fontSize: FontSize.md, color: Colors.textPrimary },
  sourceSub: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 1 },
  input: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  error: { color: Colors.expense, fontSize: FontSize.sm, marginTop: Spacing.sm },
  actions: { flexDirection: "row", gap: Spacing.sm, marginTop: Spacing.md },
  btn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
  },
  btnCancel: { borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.bg },
  btnCancelText: { color: Colors.textSecondary, fontSize: FontSize.md, fontWeight: "500" },
  btnSave: { backgroundColor: Colors.accent },
  btnSaveText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
