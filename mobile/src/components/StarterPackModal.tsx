/**
 * Envelope budgeting (Sobres) — starter pack (Phase 8 B6).
 *
 * The empty-state shortcut: instead of designing a budget from a blank screen,
 * the user approves & tweaks a default pack of 5 sobres (a client-side const,
 * scaled to their monthly income when known). One tap creates them all via
 * POST /envelopes/starter-pack. Editable: name + limit + remove a row.
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createStarterPack,
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  type EnvelopeClass,
  type StarterPackItem,
} from "../api/envelopes";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";

interface Props {
  visible: boolean;
  currency: string;
  // Best-effort monthly income from the summary (drives the scaled limits).
  monthlyIncome?: number | null;
  onClose: () => void;
  onCreated: () => void;
}

// The default pack: 5 roots spanning the four classes. `weight` splits a known
// monthly income; `fixedCrc`/`fixedUsd` are the fallback when income is unknown.
const STARTER_DEFAULTS: {
  name: string;
  envelope_class: EnvelopeClass;
  weight: number;
  fixedCrc: number;
  fixedUsd: number;
}[] = [
  { name: "Comida", envelope_class: "needs", weight: 0.25, fixedCrc: 150000, fixedUsd: 300 },
  { name: "Servicios", envelope_class: "needs", weight: 0.2, fixedCrc: 100000, fixedUsd: 200 },
  { name: "Gustos", envelope_class: "wants", weight: 0.25, fixedCrc: 100000, fixedUsd: 200 },
  { name: "Ahorro", envelope_class: "savings", weight: 0.2, fixedCrc: 80000, fixedUsd: 150 },
  { name: "Inversión", envelope_class: "investing", weight: 0.1, fixedCrc: 50000, fixedUsd: 100 },
];

interface EditableItem {
  key: string;
  name: string;
  envelope_class: EnvelopeClass;
  limit: string;
}

function roundNice(value: number, currency: string): number {
  const step = currency === "USD" ? 5 : 1000;
  return Math.max(step, Math.round(value / step) * step);
}

function buildInitial(currency: string, monthlyIncome?: number | null): EditableItem[] {
  const income = monthlyIncome != null && monthlyIncome > 0 ? monthlyIncome : null;
  return STARTER_DEFAULTS.map((d, i) => {
    const limit = income
      ? roundNice(d.weight * income, currency)
      : currency === "USD"
        ? d.fixedUsd
        : d.fixedCrc;
    return {
      key: `${i}-${d.name}`,
      name: d.name,
      envelope_class: d.envelope_class,
      limit: String(limit),
    };
  });
}

export function StarterPackModal({
  visible,
  currency,
  monthlyIncome,
  onClose,
  onCreated,
}: Props) {
  const qc = useQueryClient();
  const [items, setItems] = useState<EditableItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setItems(buildInitial(currency, monthlyIncome));
      setError(null);
    }
  }, [visible, currency, monthlyIncome]);

  const createMutation = useMutation({
    mutationFn: (payload: StarterPackItem[]) => createStarterPack(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      onCreated();
    },
  });

  const setName = (key: string, name: string) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, name } : it)));
  const setLimit = (key: string, limit: string) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, limit } : it)));
  const remove = (key: string) =>
    setItems((prev) => prev.filter((it) => it.key !== key));

  const submit = () => {
    const payload: StarterPackItem[] = [];
    for (const it of items) {
      const name = it.name.trim();
      const parsed = Number(it.limit.replace(",", "."));
      if (!name) {
        setError("Cada sobre necesita un nombre.");
        return;
      }
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setError(`Poné un límite mayor que cero para "${name}".`);
        return;
      }
      payload.push({
        name,
        envelope_class: it.envelope_class,
        limit_amount: parsed,
      });
    }
    if (payload.length === 0) {
      setError("Dejá al menos un sobre.");
      return;
    }
    setError(null);
    createMutation.mutate(payload);
  };

  const serverError =
    (createMutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (createMutation.isError ? "No se pudieron crear los sobres. Intentá de nuevo." : null);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Armá tu presupuesto en 1 minuto</Text>
          <Text style={styles.subtitle}>
            Te dejamos 5 sobres para empezar. Cambiá los nombres y montos a tu
            gusto, o quitá los que no necesités.
          </Text>

          <ScrollView style={styles.body} keyboardShouldPersistTaps="handled">
            {items.map((it) => (
              <View key={it.key} style={styles.itemRow}>
                <View
                  style={[
                    styles.classDot,
                    { backgroundColor: ENVELOPE_CLASS_COLORS[it.envelope_class] },
                  ]}
                />
                <View style={styles.itemFields}>
                  <TextInput
                    value={it.name}
                    onChangeText={(v) => setName(it.key, v)}
                    style={styles.nameInput}
                    placeholder="Nombre"
                    placeholderTextColor={Colors.textMuted}
                  />
                  <Text style={styles.itemClass}>
                    {ENVELOPE_CLASS_LABELS[it.envelope_class]}
                  </Text>
                </View>
                <AmountInput
                  value={it.limit}
                  onChangeValue={(v) => setLimit(it.key, v)}
                  style={styles.limitInput}
                  placeholder="0"
                  placeholderTextColor={Colors.textMuted}
                />
                <Pressable
                  onPress={() => remove(it.key)}
                  hitSlop={8}
                  style={({ pressed }) => [styles.removeBtn, pressed && { opacity: 0.6 }]}
                >
                  <Feather name="x" size={16} color={Colors.textMuted} />
                </Pressable>
              </View>
            ))}

            {errorText != null && <Text style={styles.error}>{errorText}</Text>}
          </ScrollView>

          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={createMutation.isPending}
              style={({ pressed }) => [styles.btn, styles.btnCancel, pressed && { opacity: 0.75 }]}
            >
              <Text style={styles.btnCancelText}>Cancelar</Text>
            </Pressable>
            <Pressable
              onPress={submit}
              disabled={createMutation.isPending || items.length === 0}
              style={({ pressed }) => [
                styles.btn,
                styles.btnSave,
                (createMutation.isPending || items.length === 0) && { opacity: 0.6 },
                pressed && { opacity: 0.85 },
              ]}
            >
              {createMutation.isPending ? (
                <ActivityIndicator color={Colors.bgCard} size="small" />
              ) : (
                <Text style={styles.btnSaveText}>
                  Crear {items.length} {items.length === 1 ? "sobre" : "sobres"}
                </Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
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
    maxHeight: "90%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  subtitle: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    lineHeight: 17,
    marginTop: 4,
    marginBottom: Spacing.sm,
  },
  body: { flexGrow: 0 },
  itemRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.xs,
  },
  classDot: { width: 9, height: 9, borderRadius: 5 },
  itemFields: { flex: 1 },
  nameInput: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    paddingVertical: 2,
  },
  itemClass: { fontSize: FontSize.xs, color: Colors.textMuted },
  limitInput: {
    width: 110,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.sm,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    textAlign: "right",
  },
  removeBtn: { padding: 2 },
  error: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    marginTop: Spacing.sm,
  },
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
