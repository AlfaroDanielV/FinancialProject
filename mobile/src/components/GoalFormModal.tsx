/**
 * Phase 7d — goal create + edit modal (full CRUD parity).
 *
 * Create mode ("+ Nueva meta") is a structured-form exception like Incomes/
 * Bills — chat creation stays the default entry. Edit mode covers name,
 * target, date, priority, and monthly plan; currency is create-only (the
 * funded contributions are same-currency v1). Keyboard scaffold copied from
 * TransactionEditModal (KeyboardAvoidingView + ScrollView with
 * keyboardShouldPersistTaps, or the sheet renders behind the keyboard).
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
import { useMutation } from "@tanstack/react-query";

import {
  createGoal,
  updateGoal,
  type GoalResponse,
} from "../api/goals";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";
import { DateField } from "./fields/DateField";

interface Props {
  visible: boolean;
  /** When present → edit mode; otherwise create mode. */
  goal?: GoalResponse | null;
  onClose: () => void;
  onSaved: (saved: GoalResponse) => void;
}

function parseNum(s: string): number {
  return parseFloat(s.replace(",", "."));
}

export function GoalFormModal({ visible, goal, onClose, onSaved }: Props) {
  const isEdit = goal != null;
  const [name, setName] = useState(goal?.name ?? "");
  const [targetAmount, setTargetAmount] = useState(
    goal != null ? String(goal.target_amount) : "",
  );
  const [currency, setCurrency] = useState<"CRC" | "USD">(
    goal?.target_currency === "USD" ? "USD" : "CRC",
  );
  const [targetDate, setTargetDate] = useState(goal?.target_date ?? "");
  const [priority, setPriority] = useState(goal?.priority ?? 3);
  const [monthly, setMonthly] = useState(
    goal?.monthly_contribution != null ? String(goal.monthly_contribution) : "",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setName(goal?.name ?? "");
      setTargetAmount(goal != null ? String(goal.target_amount) : "");
      setCurrency(goal?.target_currency === "USD" ? "USD" : "CRC");
      setTargetDate(goal?.target_date ?? "");
      setPriority(goal?.priority ?? 3);
      setMonthly(
        goal?.monthly_contribution != null
          ? String(goal.monthly_contribution)
          : "",
      );
      setError(null);
    }
  }, [goal, visible]);

  const mutation = useMutation({
    mutationFn: async () => {
      const payload = {
        name: name.trim(),
        target_amount: parseNum(targetAmount),
        target_date: targetDate.trim() ? targetDate.trim() : null,
        priority,
        monthly_contribution: monthly.trim() ? parseNum(monthly) : null,
      };
      if (isEdit) return updateGoal(goal!.id, payload);
      return createGoal({ ...payload, target_currency: currency });
    },
    onSuccess: (saved) => onSaved(saved),
  });

  const save = () => {
    if (!name.trim()) {
      setError("Ingresá un nombre para la meta.");
      return;
    }
    const target = parseNum(targetAmount);
    if (!Number.isFinite(target) || target <= 0) {
      setError("Ingresá un monto objetivo mayor que cero.");
      return;
    }
    if (targetDate.trim() && !/^\d{4}-\d{2}-\d{2}$/.test(targetDate.trim())) {
      setError("Usá el formato de fecha AAAA-MM-DD.");
      return;
    }
    if (monthly.trim()) {
      const m = parseNum(monthly);
      if (!Number.isFinite(m) || m <= 0) {
        setError("El aporte mensual planeado debe ser mayor que cero.");
        return;
      }
    }
    setError(null);
    mutation.mutate();
  };

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo guardar. Intentá de nuevo." : null);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>
            {isEdit ? "Editar meta" : "Nueva meta"}
          </Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <Field label="Nombre">
              <TextInput
                value={name}
                onChangeText={setName}
                style={styles.input}
                placeholder="Vacaciones, fondo de emergencia…"
                placeholderTextColor={Colors.textMuted}
                maxLength={120}
              />
            </Field>
            <Field label={`Monto objetivo (${currency})`}>
              <AmountInput
                value={targetAmount}
                onChangeValue={setTargetAmount}
                style={styles.input}
                placeholder="500 000"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            {!isEdit && (
              <Field label="Moneda">
                <View style={styles.segmentRow}>
                  {(["CRC", "USD"] as const).map((c) => (
                    <Pressable
                      key={c}
                      onPress={() => setCurrency(c)}
                      style={({ pressed }) => [
                        styles.segment,
                        currency === c && styles.segmentActive,
                        pressed && { opacity: 0.8 },
                      ]}
                    >
                      <Text
                        style={[
                          styles.segmentText,
                          currency === c && styles.segmentTextActive,
                        ]}
                      >
                        {c === "CRC" ? "₡ Colones" : "$ Dólares"}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              </Field>
            )}
            <Field label="Fecha objetivo (opcional)">
              <DateField
                value={targetDate}
                onChange={setTargetDate}
                style={styles.input}
                placeholder="Sin fecha"
                minimumDate={new Date()}
              />
            </Field>
            <Field label="Prioridad (1 = más alta)">
              <View style={styles.segmentRow}>
                {[1, 2, 3, 4, 5].map((p) => (
                  <Pressable
                    key={p}
                    onPress={() => setPriority(p)}
                    style={({ pressed }) => [
                      styles.segment,
                      styles.segmentSmall,
                      priority === p && styles.segmentActive,
                      pressed && { opacity: 0.8 },
                    ]}
                  >
                    <Text
                      style={[
                        styles.segmentText,
                        priority === p && styles.segmentTextActive,
                      ]}
                    >
                      {p}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </Field>
            <Field label="Aporte mensual planeado (opcional)">
              <AmountInput
                value={monthly}
                onChangeValue={setMonthly}
                style={styles.input}
                placeholder="50 000"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            {errorText != null && <Text style={styles.error}>{errorText}</Text>}
          </ScrollView>
          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={mutation.isPending}
              style={({ pressed }) => [
                styles.btn,
                styles.btnCancel,
                pressed && { opacity: 0.75 },
              ]}
            >
              <Text style={styles.btnCancelText}>Cancelar</Text>
            </Pressable>
            <Pressable
              onPress={save}
              disabled={mutation.isPending}
              style={({ pressed }) => [
                styles.btn,
                styles.btnSave,
                mutation.isPending && { opacity: 0.6 },
                pressed && { opacity: 0.85 },
              ]}
            >
              {mutation.isPending ? (
                <ActivityIndicator color={Colors.bgCard} size="small" />
              ) : (
                <Text style={styles.btnSaveText}>
                  {isEdit ? "Guardar" : "Crear meta"}
                </Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
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
    maxHeight: "88%",
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
  field: { marginBottom: Spacing.md },
  fieldLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
    marginBottom: 4,
  },
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
  segmentRow: { flexDirection: "row", flexWrap: "wrap", gap: Spacing.xs },
  segment: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 2,
    backgroundColor: Colors.bgCard,
  },
  segmentSmall: { minWidth: 40, alignItems: "center" },
  segmentActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  segmentText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  segmentTextActive: { color: Colors.accent, fontWeight: "600" },
  error: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    marginBottom: Spacing.sm,
  },
  actions: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginTop: Spacing.sm,
  },
  btn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
  },
  btnCancel: {
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
  },
  btnCancelText: {
    color: Colors.textSecondary,
    fontSize: FontSize.md,
    fontWeight: "500",
  },
  btnSave: { backgroundColor: Colors.accent },
  btnSaveText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
