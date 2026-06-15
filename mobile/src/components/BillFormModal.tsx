/**
 * Gastos fijos (recurring bill) create + edit form.
 *
 * Like the income/debt forms, this is the structured "field complexity"
 * exception to chat-first creation ([[Decision - Conversational Creation Over
 * Forms]]): chat stays the default entry; this is the manual path + the place to
 * edit a bill's schedule. Editing schedule fields makes the backend regenerate
 * the future occurrences (PATCH /recurring-bills). 'custom'/RRULE cadences are
 * out of scope here.
 *
 * Keyboard: bottom sheet wraps KeyboardAvoidingView + ScrollView so inputs don't
 * render behind the keyboard (see AGENT_CONTEXT operational lessons).
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
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation } from "@tanstack/react-query";
import { Feather } from "@expo/vector-icons";

import {
  createRecurringBill,
  updateRecurringBill,
  BILL_FREQUENCIES,
  FREQUENCY_LABELS,
  type BillFrequency,
  type RecurringBillResponse,
} from "../api/bills";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { CategoryPickerModal } from "./CategoryPickerModal";
import { AmountInput } from "./fields/AmountInput";
import { DateField } from "./fields/DateField";

interface Props {
  visible: boolean;
  mode: "create" | "edit";
  bill?: RecurringBillResponse;
  onClose: () => void;
  onSaved: () => void;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function BillFormModal({ visible, mode, bill, onClose, onSaved }: Props) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("servicios");
  const [isVariable, setIsVariable] = useState(false);
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState<"CRC" | "USD">("CRC");
  const [frequency, setFrequency] = useState<BillFrequency>("monthly");
  const [dayOfMonth, setDayOfMonth] = useState("");
  const [startDate, setStartDate] = useState(todayIso());
  const [provider, setProvider] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [categoryPickerVisible, setCategoryPickerVisible] = useState(false);

  // Seed from the bill on edit, or reset to defaults on create.
  useEffect(() => {
    setName(bill?.name ?? "");
    setCategory(bill?.category ?? "servicios");
    setIsVariable(bill?.is_variable_amount ?? false);
    setAmount(bill?.amount_expected != null ? String(bill.amount_expected) : "");
    setCurrency((bill?.currency as "CRC" | "USD") ?? "CRC");
    setFrequency(((bill?.frequency as BillFrequency) ?? "monthly"));
    setDayOfMonth(bill?.day_of_month != null ? String(bill.day_of_month) : "");
    setStartDate(bill?.start_date ?? todayIso());
    setProvider(bill?.provider ?? "");
    setNotes(bill?.notes ?? "");
    setError(null);
  }, [bill, visible]);

  const mutation = useMutation({
    mutationFn: async () => {
      const amountNum = isVariable ? null : Number(amount.replace(",", "."));
      const day = dayOfMonth.trim() ? parseInt(dayOfMonth, 10) : null;
      if (mode === "create") {
        return createRecurringBill({
          name: name.trim(),
          category,
          amount_expected: amountNum,
          currency,
          is_variable_amount: isVariable,
          frequency,
          day_of_month: day,
          start_date: startDate,
          provider: provider.trim() || null,
          notes: notes.trim() || null,
        });
      }
      return updateRecurringBill(bill!.id, {
        name: name.trim(),
        category,
        amount_expected: amountNum,
        is_variable_amount: isVariable,
        frequency,
        day_of_month: day,
        start_date: startDate,
        provider: provider.trim() || null,
        notes: notes.trim() || null,
      });
    },
    onSuccess: () => onSaved(),
  });

  const save = () => {
    if (!name.trim()) {
      mutation.reset();
      setError("Poné un nombre al gasto fijo.");
      return;
    }
    if (!isVariable) {
      const amountNum = Number(amount.replace(",", "."));
      if (!Number.isFinite(amountNum) || amountNum <= 0) {
        mutation.reset();
        setError("Ingresá un monto mayor que cero (o marcalo como variable).");
        return;
      }
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(startDate)) {
      mutation.reset();
      setError("Usá el formato de fecha AAAA-MM-DD para el inicio.");
      return;
    }
    if (dayOfMonth.trim()) {
      const day = parseInt(dayOfMonth, 10);
      if (!Number.isInteger(day) || day < 1 || day > 31) {
        mutation.reset();
        setError("El día de cobro debe estar entre 1 y 31.");
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
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>
            {mode === "create" ? "Nuevo gasto fijo" : "Editar gasto fijo"}
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
                placeholder="Ej: Luz, Netflix, Alquiler"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>

            <Field label="Categoría">
              <Pressable
                onPress={() => setCategoryPickerVisible(true)}
                style={({ pressed }) => [
                  styles.input,
                  styles.selectRow,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Text style={styles.selectText}>{category}</Text>
                <Feather name="chevron-right" size={18} color={Colors.textMuted} />
              </Pressable>
            </Field>

            <View style={styles.switchRow}>
              <Text style={styles.switchLabel}>Monto variable (cambia cada mes)</Text>
              <Switch
                value={isVariable}
                onValueChange={setIsVariable}
                trackColor={{ false: Colors.border, true: Colors.accentSoft }}
                thumbColor={isVariable ? Colors.accent : Colors.bgCard}
              />
            </View>

            {!isVariable && (
              <Field label={`Monto (${currency})`}>
                <AmountInput
                  value={amount}
                  onChangeValue={setAmount}
                  style={styles.input}
                  placeholder="18 000"
                  placeholderTextColor={Colors.textMuted}
                />
              </Field>
            )}

            {mode === "create" && (
              <Field label="Moneda">
                <Segmented
                  options={[
                    { value: "CRC", label: "₡ Colones" },
                    { value: "USD", label: "$ Dólares" },
                  ]}
                  value={currency}
                  onChange={(v) => setCurrency(v as "CRC" | "USD")}
                />
              </Field>
            )}

            <Field label="Frecuencia">
              <Segmented
                options={BILL_FREQUENCIES.map((f) => ({
                  value: f,
                  label: FREQUENCY_LABELS[f] ?? f,
                }))}
                value={frequency}
                onChange={(v) => setFrequency(v as BillFrequency)}
              />
            </Field>

            <Field label="Día de cobro (1–31, opcional)">
              <TextInput
                value={dayOfMonth}
                onChangeText={setDayOfMonth}
                keyboardType="number-pad"
                style={styles.input}
                placeholder="5"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>

            <Field label="Inicio">
              <DateField
                value={startDate}
                onChange={setStartDate}
                style={styles.input}
              />
            </Field>

            <Field label="Proveedor (opcional)">
              <TextInput
                value={provider}
                onChangeText={setProvider}
                style={styles.input}
                placeholder="ICE, AyA, Kolbi…"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>

            <Field label="Notas">
              <TextInput
                value={notes}
                onChangeText={setNotes}
                style={[styles.input, styles.multiline]}
                placeholder="Opcional"
                placeholderTextColor={Colors.textMuted}
                multiline
              />
            </Field>

            {errorText != null && <Text style={styles.error}>{errorText}</Text>}
          </ScrollView>

          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={mutation.isPending}
              style={({ pressed }) => [styles.btn, styles.btnCancel, pressed && { opacity: 0.75 }]}
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

      <CategoryPickerModal
        visible={categoryPickerVisible}
        kind="expense"
        allowClear={false}
        currentCategoryId={null}
        onClose={() => setCategoryPickerVisible(false)}
        onSelect={(cat) => {
          if (cat) setCategory(cat.name);
          setCategoryPickerVisible(false);
        }}
      />
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function Segmented({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <View style={styles.segmented}>
      {options.map((o) => (
        <Pressable
          key={o.value}
          onPress={() => onChange(o.value)}
          style={({ pressed }) => [
            styles.segment,
            value === o.value && styles.segmentActive,
            pressed && { opacity: 0.8 },
          ]}
        >
          <Text style={[styles.segmentText, value === o.value && styles.segmentTextActive]}>
            {o.label}
          </Text>
        </Pressable>
      ))}
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
    maxHeight: "92%",
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
  field: { marginBottom: Spacing.md, gap: Spacing.xs },
  fieldLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
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
  multiline: { minHeight: 64, textAlignVertical: "top" },
  selectRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  selectText: { fontSize: FontSize.md, color: Colors.textPrimary },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: Spacing.md,
    gap: Spacing.sm,
  },
  switchLabel: { flex: 1, fontSize: FontSize.sm, color: Colors.textSecondary },
  segmented: { flexDirection: "row", flexWrap: "wrap", gap: Spacing.xs },
  segment: {
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 2,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
  },
  segmentActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  segmentText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  segmentTextActive: { color: Colors.accent, fontWeight: "700" },
  error: { color: Colors.expense, fontSize: FontSize.sm, marginBottom: Spacing.sm },
  actions: { flexDirection: "row", gap: Spacing.sm, marginTop: Spacing.sm },
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
