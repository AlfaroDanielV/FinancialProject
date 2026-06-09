/**
 * Recurring-income create + edit form.
 *
 * Note on the architecture: the Conversational Creation decision makes chat the
 * primary way to create entities. Income gets a structured form anyway — the
 * same "field complexity" exception debt has — because a salary needs the gross
 * → net calculator. Chat creation stays the default; this is the manual path +
 * the calculator's home.
 *
 * `amount` is the NET (the stored take-home). For a CRC salary the calculator
 * fills it from the gross and we keep the gross alongside. income_type and
 * currency are immutable post-create (backend B8 whitelist), so edit shows them
 * read-only.
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
import { useMutation } from "@tanstack/react-query";

import {
  createRecurringIncome,
  updateRecurringIncome,
  FREQUENCY_LABELS,
  INCOME_TYPE_LABELS,
  type IncomeFrequency,
  type IncomeType,
  type RecurringIncomeResponse,
} from "../api/incomes";
import { SalaryCalculator } from "./SalaryCalculator";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  mode: "create" | "edit";
  income?: RecurringIncomeResponse;
  onClose: () => void;
  onSaved: () => void;
}

const CREATE_TYPES: IncomeType[] = ["salary", "freelance", "other"];
const FREQUENCIES: IncomeFrequency[] = ["weekly", "biweekly", "monthly", "annual"];
const DEFAULT_NAME: Record<string, string> = {
  salary: "Salario",
  freelance: "Freelance",
  other: "Ingreso",
};

export function IncomeFormModal({ visible, mode, income, onClose, onSaved }: Props) {
  const [incomeType, setIncomeType] = useState<IncomeType>(income?.income_type ?? "salary");
  const [currency, setCurrency] = useState<"CRC" | "USD">(
    (income?.currency as "CRC" | "USD") ?? "CRC",
  );
  const [name, setName] = useState(income?.name ?? "");
  const [amount, setAmount] = useState(income?.amount != null ? String(income.amount) : "");
  const [grossAmount, setGrossAmount] = useState<number | null>(income?.gross_monthly ?? null);
  const [frequency, setFrequency] = useState<IncomeFrequency>(income?.frequency ?? "monthly");
  const [nextDate, setNextDate] = useState(income?.next_payment_date ?? "");
  const [notes, setNotes] = useState(income?.notes ?? "");
  const [showCalc, setShowCalc] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSalaryCRC = incomeType === "salary" && currency === "CRC";

  useEffect(() => {
    const it = income?.income_type ?? "salary";
    setIncomeType(it);
    setCurrency((income?.currency as "CRC" | "USD") ?? "CRC");
    setName(income?.name ?? "");
    setAmount(income?.amount != null ? String(income.amount) : "");
    setGrossAmount(income?.gross_monthly ?? null);
    setFrequency(income?.frequency ?? "monthly");
    setNextDate(income?.next_payment_date ?? "");
    setNotes(income?.notes ?? "");
    // For a brand-new salary, lead with the calculator (net must be computed).
    setShowCalc(mode === "create" && it === "salary");
    setError(null);
  }, [income, visible, mode]);

  const mutation = useMutation({
    mutationFn: async () => {
      const amountNum = Number(amount.replace(",", "."));
      const effectiveName = name.trim() || DEFAULT_NAME[incomeType] || "Ingreso";
      if (mode === "create") {
        return createRecurringIncome({
          name: effectiveName,
          income_type: incomeType,
          amount: amountNum,
          gross_monthly: isSalaryCRC && grossAmount ? grossAmount : undefined,
          currency,
          frequency,
          next_payment_date: nextDate,
          notes: notes.trim() || null,
        });
      }
      return updateRecurringIncome(income!.id, {
        name: effectiveName,
        amount: amountNum,
        gross_monthly: isSalaryCRC ? grossAmount : undefined,
        frequency,
        next_payment_date: nextDate,
        notes: notes.trim() || null,
      });
    },
    onSuccess: () => onSaved(),
  });

  const save = () => {
    const amountNum = Number(amount.replace(",", "."));
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      mutation.reset();
      setError(
        isSalaryCRC
          ? "Calculá el salario neto desde el bruto antes de guardar."
          : "Ingresá un monto mayor que cero.",
      );
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(nextDate)) {
      mutation.reset();
      setError("Usá el formato de fecha AAAA-MM-DD para el próximo pago.");
      return;
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
            {mode === "create" ? "Nuevo ingreso" : "Editar ingreso"}
          </Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            {mode === "create" ? (
              <>
                <Field label="Tipo">
                  <Segmented
                    options={CREATE_TYPES.map((t) => ({ value: t, label: INCOME_TYPE_LABELS[t] }))}
                    value={incomeType}
                    onChange={(v) => setIncomeType(v as IncomeType)}
                  />
                </Field>
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
              </>
            ) : (
              <View style={styles.readonlyRow}>
                <ReadonlyChip label="Tipo" value={INCOME_TYPE_LABELS[incomeType] ?? incomeType} />
                <ReadonlyChip label="Moneda" value={currency} />
              </View>
            )}

            <Field label="Nombre">
              <TextInput
                value={name}
                onChangeText={setName}
                style={styles.input}
                placeholder={DEFAULT_NAME[incomeType] ?? "Ingreso"}
                placeholderTextColor={Colors.textMuted}
              />
            </Field>

            <Field label={isSalaryCRC ? "Monto neto" : "Monto"}>
              <TextInput
                value={amount}
                onChangeText={setAmount}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
                editable={!isSalaryCRC || !showCalc}
              />
            </Field>

            {isSalaryCRC && (
              <>
                <Pressable
                  style={styles.calcToggle}
                  onPress={() => setShowCalc((v) => !v)}
                >
                  <Feather
                    name={showCalc ? "chevron-up" : "chevron-down"}
                    size={16}
                    color={Colors.accent}
                  />
                  <Text style={styles.calcToggleText}>
                    {showCalc ? "Ocultar calculadora" : "Calcular desde salario bruto"}
                  </Text>
                </Pressable>
                {showCalc && (
                  <SalaryCalculator
                    currency={currency}
                    initialGross={grossAmount != null ? String(grossAmount) : undefined}
                    onResult={(net, gross) => {
                      setAmount(String(net));
                      setGrossAmount(gross);
                    }}
                  />
                )}
              </>
            )}

            <Field label="Frecuencia">
              <Segmented
                options={FREQUENCIES.map((f) => ({ value: f, label: FREQUENCY_LABELS[f] }))}
                value={frequency}
                onChange={(v) => setFrequency(v as IncomeFrequency)}
              />
            </Field>

            <Field label="Próximo pago (AAAA-MM-DD)">
              <TextInput
                value={nextDate}
                onChangeText={setNextDate}
                style={styles.input}
                placeholder="2026-06-15"
                placeholderTextColor={Colors.textMuted}
                autoCapitalize="none"
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

function ReadonlyChip({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.chip}>
      <Text style={styles.chipLabel}>{label}</Text>
      <Text style={styles.chipValue}>{value}</Text>
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
  segmented: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.xs,
  },
  segment: {
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 2,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
  },
  segmentActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  segmentText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  segmentTextActive: { color: Colors.accent, fontWeight: "700" },
  readonlyRow: { flexDirection: "row", gap: Spacing.sm, marginBottom: Spacing.md },
  chip: {
    flex: 1,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.xs,
    gap: 2,
  },
  chipLabel: { fontSize: FontSize.xs, color: Colors.textMuted },
  chipValue: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textPrimary },
  calcToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginBottom: Spacing.sm,
    marginTop: -Spacing.xs,
  },
  calcToggleText: { fontSize: FontSize.sm, color: Colors.accent, fontWeight: "600" },
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
