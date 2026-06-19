/**
 * Debt edit modal — the Update half of debt CRUD.
 *
 * Edits the mutable fields the backend allows on PATCH /debts/{id}: name,
 * payment_due_day, and notes. The financial fields (amount, rate, term,
 * minimum payment) are immutable post-creation by design — they define the
 * amortization schedule — so they are shown read-only on the detail screen and
 * NOT editable here. Lifecycle (pause/resume, archive/restore) lives on the
 * detail screen as explicit actions. The backend's `extra="forbid"` 6-field
 * whitelist rejects anything else; a 4xx surfaces in the error line.
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
  type DebtResponse,
  type DebtUpdate,
  updateDebt,
} from "../api/debts";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";

interface Props {
  visible: boolean;
  debt: DebtResponse;
  onClose: () => void;
  onSaved: (updated: DebtResponse) => void;
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

export function DebtEditModal({ visible, debt, onClose, onSaved }: Props) {
  const [name, setName] = useState(debt.name);
  const [dueDay, setDueDay] = useState(String(debt.payment_due_day));
  const [minPayment, setMinPayment] = useState(String(debt.minimum_payment));
  const [notes, setNotes] = useState(debt.notes ?? "");
  const [error, setError] = useState<string | null>(null);

  // Interest accruing on the current balance each month. A cuota at or below
  // this never amortizes — the same rule the create form enforces. interest_rate
  // is the stored 0–1 fraction here (not a percent).
  const monthlyInterest = (debt.current_balance * debt.interest_rate) / 12;

  // Re-seed when a different debt is opened (or the modal re-opens).
  useEffect(() => {
    setName(debt.name);
    setDueDay(String(debt.payment_due_day));
    setMinPayment(String(debt.minimum_payment));
    setNotes(debt.notes ?? "");
    setError(null);
  }, [debt, visible]);

  const mutation = useMutation({
    mutationFn: (payload: DebtUpdate) => updateDebt(debt.id, payload),
    onSuccess: (updated) => onSaved(updated),
  });

  const save = () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      mutation.reset();
      setError("El nombre no puede estar vacío.");
      return;
    }
    const day = parseInt(dueDay, 10);
    if (!Number.isInteger(day) || day < 1 || day > 31) {
      mutation.reset();
      setError("El día de pago debe estar entre 1 y 31.");
      return;
    }
    const pay = Number(minPayment.replace(",", "."));
    if (!Number.isFinite(pay) || pay <= 0) {
      mutation.reset();
      setError("Ingresá una cuota mensual mayor que cero.");
      return;
    }
    if (debt.current_balance > 0 && pay >= debt.current_balance) {
      mutation.reset();
      setError("La cuota no puede ser mayor o igual al saldo de la deuda.");
      return;
    }
    if (pay <= monthlyInterest) {
      mutation.reset();
      setError(
        `La cuota no cubre ni el interés del mes (~${formatMoney(
          monthlyInterest,
          debt.currency,
        )}). Con ese monto el saldo nunca bajaría.`,
      );
      return;
    }
    setError(null);
    mutation.mutate({
      name: trimmedName,
      payment_due_day: day,
      minimum_payment: pay,
      notes: notes.trim() || null,
    });
  };

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo guardar. Intentá de nuevo." : null);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Editar deuda</Text>
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
                placeholder="Préstamo del carro…"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Día de pago (1–31)">
              <TextInput
                value={dueDay}
                onChangeText={setDueDay}
                keyboardType="number-pad"
                style={styles.input}
                placeholder="1"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label={`Cuota mensual (${debt.currency})`}>
              <AmountInput
                value={minPayment}
                onChangeValue={setMinPayment}
                style={styles.input}
                placeholder="126 000"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Text style={styles.hint}>
              Saldo {formatMoney(debt.current_balance, debt.currency)} · interés
              del mes ~{formatMoney(monthlyInterest, debt.currency)}.
            </Text>
            <Field label="Notas">
              <TextInput
                value={notes}
                onChangeText={setNotes}
                style={[styles.input, styles.inputMultiline]}
                placeholder="Opcional"
                placeholderTextColor={Colors.textMuted}
                multiline
              />
            </Field>
            <Text style={styles.immutableNote}>
              El monto, la tasa y el plazo no se pueden editar: definen la tabla
              de amortización.
            </Text>
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
                <Text style={styles.btnSaveText}>Guardar</Text>
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
  inputMultiline: {
    minHeight: 72,
    textAlignVertical: "top",
  },
  hint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: -Spacing.sm + 2,
    marginBottom: Spacing.md,
  },
  immutableNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontStyle: "italic",
    marginBottom: Spacing.sm,
  },
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
  btnCancelText: { color: Colors.textSecondary, fontSize: FontSize.md, fontWeight: "500" },
  btnSave: { backgroundColor: Colors.accent },
  btnSaveText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
