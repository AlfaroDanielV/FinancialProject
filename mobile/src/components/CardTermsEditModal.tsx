/**
 * Phase 7b B3 — edit (or add) a credit card's terms from the account detail.
 * Rates entered as PERCENT, converted to the 0–1 fraction the API stores.
 * Keyboard scaffold copied from TransactionEditModal.
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
import { useMutation, useQuery } from "@tanstack/react-query";

import { fetchEnvelopes } from "../api/envelopes";
import {
  upsertCardTerms,
  type CardTermsResponse,
} from "../api/cardTerms";
import { EnvelopePickerModal } from "./EnvelopePickerModal";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  accountId: string;
  terms: CardTermsResponse | null;
  onClose: () => void;
  onSaved: (saved: CardTermsResponse) => void;
}

function pctString(fraction: string | null | undefined): string {
  if (!fraction) return "";
  const n = parseFloat(fraction);
  if (isNaN(n)) return "";
  return String(Math.round(n * 10000) / 100);
}

function parseNum(s: string): number {
  return parseFloat(s.replace(",", "."));
}

export function CardTermsEditModal({
  visible,
  accountId,
  terms,
  onClose,
  onSaved,
}: Props) {
  const [ratePct, setRatePct] = useState(pctString(terms?.annual_interest_rate));
  const [cashPct, setCashPct] = useState(pctString(terms?.cash_advance_rate));
  const [minPct, setMinPct] = useState(pctString(terms?.minimum_payment_pct));
  const [minFloor, setMinFloor] = useState(terms?.minimum_payment_floor ?? "");
  const [creditLimit, setCreditLimit] = useState(terms?.credit_limit ?? "");
  const [statementDay, setStatementDay] = useState(
    terms?.statement_day != null ? String(terms.statement_day) : "",
  );
  const [dueDay, setDueDay] = useState(
    terms?.payment_due_day != null ? String(terms.payment_due_day) : "",
  );
  const [envelopeId, setEnvelopeId] = useState<string | null>(
    terms?.envelope_id ?? null,
  );
  const [pickerVisible, setPickerVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: envelopes } = useQuery({
    queryKey: ["envelopes", "active"],
    queryFn: () => fetchEnvelopes(false),
    enabled: visible,
  });
  const selectedEnvelope = envelopes?.find((e) => e.id === envelopeId) ?? null;

  useEffect(() => {
    if (visible) {
      setRatePct(pctString(terms?.annual_interest_rate));
      setCashPct(pctString(terms?.cash_advance_rate));
      setMinPct(pctString(terms?.minimum_payment_pct));
      setMinFloor(terms?.minimum_payment_floor ?? "");
      setCreditLimit(terms?.credit_limit ?? "");
      setStatementDay(
        terms?.statement_day != null ? String(terms.statement_day) : "",
      );
      setDueDay(
        terms?.payment_due_day != null ? String(terms.payment_due_day) : "",
      );
      setEnvelopeId(terms?.envelope_id ?? null);
      setError(null);
    }
  }, [terms, visible]);

  const mutation = useMutation({
    mutationFn: () => {
      const rate = parseNum(ratePct);
      const min = parseNum(minPct);
      const due = parseInt(dueDay, 10);
      return upsertCardTerms(accountId, {
        annual_interest_rate: String(rate / 100),
        cash_advance_rate: cashPct.trim() ? String(parseNum(cashPct) / 100) : null,
        minimum_payment_pct: String(min / 100),
        minimum_payment_floor: minFloor.trim() ? String(parseNum(minFloor)) : null,
        credit_limit: creditLimit.trim() ? String(parseNum(creditLimit)) : null,
        statement_day: statementDay.trim() ? parseInt(statementDay, 10) : null,
        payment_due_day: due,
        envelope_id: envelopeId,
      });
    },
    onSuccess: (saved) => onSaved(saved),
  });

  const save = () => {
    const rate = parseNum(ratePct);
    const min = parseNum(minPct);
    const due = parseInt(dueDay, 10);
    if (isNaN(rate) || rate < 0 || rate > 100) {
      setError("Ingresá la tasa anual de compras (0–100%).");
      return;
    }
    if (isNaN(min) || min <= 0 || min > 100) {
      setError("Ingresá el pago mínimo como % del saldo (0–100%).");
      return;
    }
    if (isNaN(due) || due < 1 || due > 31) {
      setError("Ingresá el día límite de pago (1–31).");
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
            {terms ? "Editar términos de la tarjeta" : "Agregar términos de la tarjeta"}
          </Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <Field label="Tasa anual de compras (%)">
              <TextInput
                value={ratePct}
                onChangeText={setRatePct}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="45"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Tasa de adelantos de efectivo (%, opcional)">
              <TextInput
                value={cashPct}
                onChangeText={setCashPct}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="50"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Pago mínimo (% del saldo)">
              <TextInput
                value={minPct}
                onChangeText={setMinPct}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="2.5"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Pago mínimo base (₡, opcional)">
              <TextInput
                value={minFloor}
                onChangeText={setMinFloor}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="5000"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Límite de crédito (opcional)">
              <TextInput
                value={creditLimit}
                onChangeText={setCreditLimit}
                keyboardType="decimal-pad"
                style={styles.input}
                placeholder="2000000"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Día de corte (1–31, opcional)">
              <TextInput
                value={statementDay}
                onChangeText={setStatementDay}
                keyboardType="number-pad"
                style={styles.input}
                placeholder="20"
                placeholderTextColor={Colors.textMuted}
                maxLength={2}
              />
            </Field>
            <Field label="Día límite de pago (1–31)">
              <TextInput
                value={dueDay}
                onChangeText={setDueDay}
                keyboardType="number-pad"
                style={styles.input}
                placeholder="10"
                placeholderTextColor={Colors.textMuted}
                maxLength={2}
              />
            </Field>
            <Field label="Sobre (reserva el pago mínimo)">
              <Pressable
                onPress={() => setPickerVisible(true)}
                style={({ pressed }) => [
                  styles.input,
                  styles.selectRow,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Text
                  style={[
                    styles.selectText,
                    selectedEnvelope == null && styles.selectPlaceholder,
                  ]}
                >
                  {selectedEnvelope?.name ?? "Sin sobre"}
                </Text>
                <Feather name="chevron-right" size={18} color={Colors.textMuted} />
              </Pressable>
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
                <Text style={styles.btnSaveText}>Guardar</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      <EnvelopePickerModal
        visible={pickerVisible}
        currentEnvelopeId={envelopeId}
        onClose={() => setPickerVisible(false)}
        onSelect={(id) => {
          setEnvelopeId(id);
          setPickerVisible(false);
        }}
      />
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
  selectRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  selectText: { fontSize: FontSize.md, color: Colors.textPrimary },
  selectPlaceholder: { color: Colors.textMuted },
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
