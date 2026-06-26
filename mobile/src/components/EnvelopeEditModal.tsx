/**
 * Envelope budgeting (Sobres) — create / edit sheet.
 *
 * Create: name + class + monthly limit + currency (CRC/USD). Edit: name +
 * class + limit (currency is immutable post-create, enforced by the backend's
 * EnvelopeUpdate whitelist) + an Archivar action. On success invalidates the
 * envelopes + dashboard query caches so the home-tab bars refresh.
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  archiveEnvelope,
  createEnvelope,
  deleteEnvelope,
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  ENVELOPE_CLASS_ORDER,
  type EnvelopeClass,
  type EnvelopeEditable,
  updateEnvelope,
} from "../api/envelopes";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";

interface ParentContext {
  id: string;
  name: string;
  envelope_class: EnvelopeClass;
  currency: string;
  available?: number; // parent budget still unallocated (cap for a new child)
}

interface Props {
  visible: boolean;
  envelope: EnvelopeEditable | null; // null = create mode
  // Phase 7a: when set in create mode, the new envelope nests under this parent
  // and inherits its class + currency (class/currency pickers are hidden).
  parentEnvelope?: ParentContext | null;
  onClose: () => void;
  onSaved: () => void;
}

export function EnvelopeEditModal({
  visible,
  envelope,
  parentEnvelope = null,
  onClose,
  onSaved,
}: Props) {
  const isEdit = envelope != null;
  const creatingChild = !isEdit && parentEnvelope != null;
  const qc = useQueryClient();

  const [name, setName] = useState("");
  // Phase 8 B6 (progressive disclosure): the class is pre-selected to a sensible
  // default (matching the backend "wants" default) so it's never a required
  // upfront gate — the user can change it, but doesn't have to decide first.
  const [cls, setCls] = useState<EnvelopeClass>("wants");
  const [limit, setLimit] = useState("");
  const [currency, setCurrency] = useState<"CRC" | "USD">("CRC");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(envelope?.name ?? "");
    setCls(
      envelope?.envelope_class ?? parentEnvelope?.envelope_class ?? "wants",
    );
    setLimit(envelope ? String(envelope.limit_amount) : "");
    setCurrency(
      (envelope?.currency as "CRC" | "USD") ??
        (parentEnvelope?.currency as "CRC" | "USD") ??
        "CRC",
    );
    setError(null);
  }, [envelope, parentEnvelope, visible]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["envelopes"] });
    void qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const parsed = Number(limit.replace(",", "."));
      if (isEdit) {
        return updateEnvelope(envelope!.id, {
          name: name.trim(),
          envelope_class: cls,
          limit_amount: parsed,
        });
      }
      return createEnvelope({
        name: name.trim(),
        envelope_class: cls,
        limit_amount: parsed,
        currency,
        parent_id: parentEnvelope?.id ?? null,
      });
    },
    onSuccess: () => {
      invalidate();
      onSaved();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveEnvelope(envelope!.id),
    onSuccess: () => {
      invalidate();
      onSaved();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteEnvelope(envelope!.id),
    onSuccess: () => {
      invalidate();
      // Tagged transactions were unlinked — refresh their lists too.
      void qc.invalidateQueries({ queryKey: ["transactions"] });
      onSaved();
    },
  });

  const submit = () => {
    if (!name.trim()) {
      setError("Poné un nombre.");
      return;
    }
    const parsed = Number(limit.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("El límite debe ser mayor que cero.");
      return;
    }
    if (
      creatingChild &&
      parentEnvelope?.available != null &&
      parsed > parentEnvelope.available
    ) {
      setError(
        `El límite no puede superar lo disponible del sobre padre (${formatMoney(
          parentEnvelope.available,
          currency,
        )}).`,
      );
      return;
    }
    setError(null);
    saveMutation.mutate();
  };

  const confirmArchive = () => {
    Alert.alert(
      "Archivar sobre",
      `¿Archivar "${envelope?.name}"? Los gastos ya asignados se mantienen, pero el sobre deja de contar.`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Archivar",
          style: "destructive",
          onPress: () => archiveMutation.mutate(),
        },
      ],
    );
  };

  const confirmDelete = () => {
    Alert.alert(
      "Eliminar sobre",
      `¿Eliminar "${envelope?.name}" para siempre? Los gastos que tenía asignados se mantienen, pero quedan sin sobre. Esto no se puede deshacer.`,
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

  const busy =
    saveMutation.isPending || archiveMutation.isPending || deleteMutation.isPending;
  const serverError =
    (saveMutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (saveMutation.isError ? "No se pudo guardar. Intentá de nuevo." : null);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>
            {isEdit ? "Editar sobre" : creatingChild ? "Nuevo sub-sobre" : "Nuevo sobre"}
          </Text>

          <ScrollView style={styles.body} keyboardShouldPersistTaps="handled">
            {creatingChild && parentEnvelope && (
              <Text style={styles.parentNote}>
                Sub-sobre de <Text style={styles.parentNoteName}>{parentEnvelope.name}</Text>.
                Hereda el tipo ({ENVELOPE_CLASS_LABELS[parentEnvelope.envelope_class]}) y
                la moneda ({parentEnvelope.currency}); su límite es una parte del presupuesto
                del sobre padre.
                {parentEnvelope.available != null && (
                  <Text style={styles.parentNoteName}>
                    {" "}Disponible: {formatMoney(parentEnvelope.available, parentEnvelope.currency)}.
                  </Text>
                )}
              </Text>
            )}

            <Text style={styles.label}>Nombre</Text>
            <TextInput
              value={name}
              onChangeText={setName}
              style={styles.input}
              placeholder="Súper, Restaurantes, Gasolina…"
              placeholderTextColor={Colors.textMuted}
            />

            {!creatingChild && (
              <>
                <Text style={styles.label}>Tipo</Text>
                <View style={styles.classGrid}>
                  {ENVELOPE_CLASS_ORDER.map((c) => {
                    const active = c === cls;
                    return (
                      <Pressable
                        key={c}
                        onPress={() => setCls(c)}
                        style={[
                          styles.classChip,
                          active && {
                            borderColor: ENVELOPE_CLASS_COLORS[c],
                            backgroundColor: Colors.accentBg,
                          },
                        ]}
                      >
                        <View
                          style={[styles.classDot, { backgroundColor: ENVELOPE_CLASS_COLORS[c] }]}
                        />
                        <Text
                          style={[
                            styles.classChipText,
                            active && { color: Colors.textPrimary, fontWeight: "600" },
                          ]}
                        >
                          {ENVELOPE_CLASS_LABELS[c]}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </>
            )}

            <Text style={styles.label}>Límite mensual ({currency})</Text>
            <AmountInput
              value={limit}
              onChangeValue={setLimit}
              style={styles.input}
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
            />
            <Text style={styles.help}>
              Acá cargás la cantidad de dinero que disponés para gastar en este
              rubro. Empieza lleno (100%) y se va descontando con cada
              transacción que agregués o asignés a este sobre; cuando te
              queda poco, la barra se pone roja.
            </Text>

            {!isEdit && !creatingChild && (
              <>
                <Text style={styles.label}>Moneda</Text>
                <View style={styles.currencyRow}>
                  {(["CRC", "USD"] as const).map((c) => {
                    const active = c === currency;
                    return (
                      <Pressable
                        key={c}
                        onPress={() => setCurrency(c)}
                        style={[styles.currencyChip, active && styles.currencyChipActive]}
                      >
                        <Text
                          style={[
                            styles.currencyChipText,
                            active && styles.currencyChipTextActive,
                          ]}
                        >
                          {c}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </>
            )}

            {errorText != null && <Text style={styles.error}>{errorText}</Text>}

            {isEdit && (
              <View style={styles.dangerRow}>
                <Pressable
                  onPress={confirmArchive}
                  disabled={busy}
                  style={({ pressed }) => [styles.dangerBtn, pressed && { opacity: 0.7 }]}
                >
                  <Text style={styles.archiveText}>Archivar</Text>
                </Pressable>
                <Pressable
                  onPress={confirmDelete}
                  disabled={busy}
                  style={({ pressed }) => [styles.dangerBtn, pressed && { opacity: 0.7 }]}
                >
                  <Text style={styles.deleteText}>Eliminar</Text>
                </Pressable>
              </View>
            )}
          </ScrollView>

          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={busy}
              style={({ pressed }) => [styles.btn, styles.btnCancel, pressed && { opacity: 0.75 }]}
            >
              <Text style={styles.btnCancelText}>Cancelar</Text>
            </Pressable>
            <Pressable
              onPress={submit}
              disabled={busy}
              style={({ pressed }) => [
                styles.btn,
                styles.btnSave,
                busy && { opacity: 0.6 },
                pressed && { opacity: 0.85 },
              ]}
            >
              {saveMutation.isPending ? (
                <ActivityIndicator color={Colors.bgCard} size="small" />
              ) : (
                <Text style={styles.btnSaveText}>{isEdit ? "Guardar" : "Crear"}</Text>
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
    marginBottom: Spacing.md,
  },
  body: { flexGrow: 0 },
  label: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
    marginBottom: 4,
    marginTop: Spacing.sm,
  },
  help: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    lineHeight: 16,
    marginTop: 6,
  },
  parentNote: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    lineHeight: 17,
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.sm,
    padding: Spacing.sm,
    marginBottom: Spacing.xs,
  },
  parentNoteName: { fontWeight: "700", color: Colors.textPrimary },
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
  classGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.sm,
  },
  classChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  classDot: { width: 9, height: 9, borderRadius: 5 },
  classChipText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  currencyRow: { flexDirection: "row", gap: Spacing.sm },
  currencyChip: {
    flex: 1,
    alignItems: "center",
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    backgroundColor: Colors.bgCard,
  },
  currencyChipActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  currencyChipText: { fontSize: FontSize.md, color: Colors.textSecondary, fontWeight: "500" },
  currencyChipTextActive: { color: Colors.accent, fontWeight: "700" },
  error: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    marginTop: Spacing.sm,
  },
  dangerRow: {
    flexDirection: "row",
    justifyContent: "center",
    gap: Spacing.xl,
    marginTop: Spacing.lg,
  },
  dangerBtn: {
    alignItems: "center",
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.md,
  },
  archiveText: { color: Colors.textSecondary, fontSize: FontSize.sm, fontWeight: "600" },
  deleteText: { color: Colors.expense, fontSize: FontSize.sm, fontWeight: "700" },
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
