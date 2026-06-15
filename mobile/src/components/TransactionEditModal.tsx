/**
 * Phase 6f — Transaction edit modal (parity with the SPA's TransactionEditModal).
 *
 * Edits amount / merchant / description / category / date on a confirmed,
 * non-archived, non-transfer transaction via PATCH /transactions/{id}. The
 * backend rejects shadow rows, transfer legs, and archived rows with 409; we
 * surface that detail in an Alert. The amount field edits magnitude only and
 * preserves the original sign (expense stays expense) — switching income↔
 * expense is rare and out of scope here. Category is chosen from the Categorías
 * screen (user_categories) via a picker — see CategoryPickerModal. We send both
 * `category` (name, what the list/detail screens display) and `category_id`
 * (FK) so the displayed string and the FK stay in sync. A transaction captured
 * via chat carries a free-text `category` with no `category_id`; it shows as the
 * label until the user picks a real category here.
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

import { fetchAccounts } from "../api/accounts";
import { fetchEnvelopes } from "../api/envelopes";
import {
  type TransactionResponse,
  type TransactionUpdate,
  updateTransaction,
} from "../api/transactions";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AccountPickerModal } from "./AccountPickerModal";
import { CategoryPickerModal } from "./CategoryPickerModal";
import { EnvelopePickerModal } from "./EnvelopePickerModal";
import { AmountInput } from "./fields/AmountInput";
import { DateField } from "./fields/DateField";

function currencyName(currency: string): string {
  if (currency === "CRC") return "colones";
  if (currency === "USD") return "dólares";
  return currency;
}

interface Props {
  visible: boolean;
  tx: TransactionResponse;
  onClose: () => void;
  onSaved: (updated: TransactionResponse) => void;
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

export function TransactionEditModal({ visible, tx, onClose, onSaved }: Props) {
  const isExpense = tx.amount < 0;
  const [amount, setAmount] = useState(String(Math.abs(tx.amount)));
  const [merchant, setMerchant] = useState(tx.merchant ?? "");
  const [description, setDescription] = useState(tx.description ?? "");
  // Category is now picked from the Categorías list; we keep the name (for
  // display + the legacy `category` string) and the FK id side by side.
  const [categoryName, setCategoryName] = useState(tx.category ?? "");
  const [categoryId, setCategoryId] = useState<string | null>(tx.category_id);
  const [date, setDate] = useState(tx.transaction_date);
  const [envelopeId, setEnvelopeId] = useState<string | null>(tx.envelope_id);
  const [accountId, setAccountId] = useState<string | null>(tx.account_id);
  const [pickerVisible, setPickerVisible] = useState(false);
  const [categoryPickerVisible, setCategoryPickerVisible] = useState(false);
  const [accountPickerVisible, setAccountPickerVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seed the form whenever a different row is opened.
  useEffect(() => {
    setAmount(String(Math.abs(tx.amount)));
    setMerchant(tx.merchant ?? "");
    setDescription(tx.description ?? "");
    setCategoryName(tx.category ?? "");
    setCategoryId(tx.category_id);
    setDate(tx.transaction_date);
    setEnvelopeId(tx.envelope_id);
    setAccountId(tx.account_id);
    setError(null);
  }, [tx, visible]);

  // Resolve the selected account's name + currency for the "Cuenta" field and
  // the cross-currency conversion hint. All currencies are offered; the backend
  // converts when the destination currency differs from the row's.
  const { data: accounts } = useQuery({
    queryKey: ["accounts", "active"],
    queryFn: () => fetchAccounts(false),
    enabled: visible,
  });
  const selectedAccount = accounts?.find((a) => a.id === accountId) ?? null;
  const crossCurrency =
    selectedAccount != null && selectedAccount.currency !== tx.currency;

  // Only expenses can be assigned to a spending-cap envelope.
  const { data: envelopes } = useQuery({
    queryKey: ["envelopes", "active"],
    queryFn: () => fetchEnvelopes(false),
    enabled: visible && isExpense,
  });
  const selectedEnvelope = envelopes?.find((e) => e.id === envelopeId) ?? null;

  const mutation = useMutation({
    mutationFn: (payload: TransactionUpdate) => updateTransaction(tx.id, payload),
    onSuccess: (updated) => onSaved(updated),
  });

  const save = () => {
    const parsed = Number(amount.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      mutation.reset();
      setError("Ingresá un monto mayor que cero.");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      setError("Usá el formato de fecha AAAA-MM-DD.");
      return;
    }
    setError(null);
    mutation.mutate({
      amount: isExpense ? -Math.abs(parsed) : Math.abs(parsed),
      merchant: merchant.trim() || null,
      description: description.trim() || null,
      // Set both: the name keeps the displayed string right, the id links the
      // user_categories row (so the Categorías screen counts it).
      category: categoryName.trim() || null,
      category_id: categoryId,
      // Reassign the movement; backend converts amount + currency if the
      // destination account uses a different currency.
      account_id: accountId,
      transaction_date: date,
      // Only send envelope_id for expenses (income/transfers never carry one).
      ...(isExpense ? { envelope_id: envelopeId } : {}),
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
          <Text style={styles.title}>Editar movimiento</Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <Field label={`Monto (${tx.currency})`}>
              <AmountInput
                value={amount}
                onChangeValue={setAmount}
                style={styles.input}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Comercio">
              <TextInput
                value={merchant}
                onChangeText={setMerchant}
                style={styles.input}
                placeholder="Auto Mercado…"
                placeholderTextColor={Colors.textMuted}
              />
            </Field>
            <Field label="Detalle">
              <TextInput
                value={description}
                onChangeText={setDescription}
                style={styles.input}
                placeholder="Opcional"
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
                <Text
                  style={[
                    styles.selectText,
                    !categoryName.trim() && styles.selectPlaceholder,
                  ]}
                >
                  {categoryName.trim() || "Sin categoría"}
                </Text>
                <Feather name="chevron-right" size={18} color={Colors.textMuted} />
              </Pressable>
            </Field>
            <Field label="Cuenta">
              <Pressable
                onPress={() => setAccountPickerVisible(true)}
                style={({ pressed }) => [
                  styles.input,
                  styles.selectRow,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Text
                  style={[
                    styles.selectText,
                    selectedAccount == null && styles.selectPlaceholder,
                  ]}
                >
                  {selectedAccount?.name ?? "Sin cuenta"}
                </Text>
                <Feather name="chevron-right" size={18} color={Colors.textMuted} />
              </Pressable>
              {crossCurrency && selectedAccount != null && (
                <Text style={styles.hint}>
                  El monto se convertirá a {currencyName(selectedAccount.currency)}.
                </Text>
              )}
            </Field>
            <Field label="Fecha">
              <DateField value={date} onChange={setDate} style={styles.input} />
            </Field>
            {isExpense && (
              <Field label="Sobre">
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
            )}
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

      <CategoryPickerModal
        visible={categoryPickerVisible}
        kind={isExpense ? "expense" : "income"}
        currentCategoryId={categoryId}
        onClose={() => setCategoryPickerVisible(false)}
        onSelect={(cat) => {
          setCategoryId(cat?.id ?? null);
          setCategoryName(cat?.name ?? "");
          setCategoryPickerVisible(false);
        }}
      />

      <EnvelopePickerModal
        visible={pickerVisible}
        currentEnvelopeId={envelopeId}
        onClose={() => setPickerVisible(false)}
        onSelect={(id) => {
          setEnvelopeId(id);
          setPickerVisible(false);
        }}
      />

      <AccountPickerModal
        visible={accountPickerVisible}
        currentAccountId={accountId}
        onClose={() => setAccountPickerVisible(false)}
        onSelect={(account) => {
          setAccountId(account?.id ?? null);
          setAccountPickerVisible(false);
        }}
      />
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
  selectRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  selectText: { fontSize: FontSize.md, color: Colors.textPrimary },
  selectPlaceholder: { color: Colors.textMuted },
  hint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 6,
  },
  error: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    marginTop: -Spacing.sm + 2,
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
