/**
 * Registrar como pago — turn a positive movement on a credit account into a
 * card payment.
 *
 * The user picks the SOURCE (fund) account and confirms the amount; the backend
 * creates a transfer from that account → the card and deletes the original row
 * (so the money actually leaves the source, the card is credited once, and the
 * row stops inflating income). Cross-currency asks for an fx_rate (units of the
 * source currency per 1 unit of the card currency), mirroring TransferModal.
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

import type { AccountResponse } from "../api/accounts";
import { registerAsPayment } from "../api/transactions";
import type { TransactionResponse } from "../api/transactions";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AccountPickerModal } from "./AccountPickerModal";
import { AmountInput } from "./fields/AmountInput";

interface Props {
  visible: boolean;
  /** The positive movement on a credit account being converted. */
  tx: TransactionResponse;
  onClose: () => void;
  /** Called after a successful conversion (the row is deleted — caller goes back). */
  onDone: () => void;
}

function currencyLabel(currency: string): string {
  if (currency === "CRC") return "₡";
  if (currency === "USD") return "$";
  return currency;
}

export function RegisterPaymentModal({ visible, tx, onClose, onDone }: Props) {
  const qc = useQueryClient();
  const [source, setSource] = useState<AccountResponse | null>(null);
  const [amount, setAmount] = useState(String(Math.abs(tx.amount)));
  const [fxRate, setFxRate] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setSource(null);
      setAmount(String(Math.abs(tx.amount)));
      setFxRate("");
      setPickerOpen(false);
      setError(null);
    }
  }, [visible, tx]);

  const crossCurrency = source != null && source.currency !== tx.currency;

  const mutation = useMutation({
    mutationFn: (payload: {
      source_account_id: string;
      amount: number;
      occurred_at: string;
      fx_rate: number | null;
    }) => registerAsPayment(tx.id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account"] });
      qc.invalidateQueries({ queryKey: ["accountTransactions"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onDone();
    },
  });

  const save = () => {
    if (source == null) {
      setError("Elegí la cuenta de donde salió el pago.");
      return;
    }
    const parsed = Number(amount.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("Ingresá un monto mayor que cero.");
      return;
    }
    let fx: number | null = null;
    if (crossCurrency) {
      const parsedFx = Number(fxRate.replace(",", "."));
      if (!Number.isFinite(parsedFx) || parsedFx <= 0) {
        setError("Ingresá el tipo de cambio (cuentas en monedas distintas).");
        return;
      }
      fx = parsedFx;
    }
    setError(null);
    mutation.mutate({
      source_account_id: source.id,
      amount: parsed,
      occurred_at: `${tx.transaction_date}T12:00:00Z`,
      fx_rate: fx,
    });
  };

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo registrar el pago. Intentá de nuevo." : null);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Registrar como pago de tarjeta</Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Cuenta de origen</Text>
              <Pressable
                onPress={() => setPickerOpen(true)}
                style={({ pressed }) => [
                  styles.input,
                  styles.selectRow,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Text
                  style={[
                    styles.selectText,
                    source == null && styles.selectPlaceholder,
                  ]}
                >
                  {source
                    ? `${source.name} (${currencyLabel(source.currency)})`
                    : "Elegí una cuenta"}
                </Text>
                <Feather name="chevron-down" size={18} color={Colors.textMuted} />
              </Pressable>
            </View>

            <View style={styles.field}>
              <Text style={styles.fieldLabel}>
                Monto ({currencyLabel(tx.currency)})
              </Text>
              <AmountInput
                value={amount}
                onChangeValue={setAmount}
                style={styles.input}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
              />
            </View>

            {crossCurrency && source != null && (
              <View style={styles.field}>
                <Text style={styles.fieldLabel}>
                  Tipo de cambio (1 {tx.currency} = ? {source.currency})
                </Text>
                <TextInput
                  value={fxRate}
                  onChangeText={setFxRate}
                  keyboardType="decimal-pad"
                  style={styles.input}
                  placeholder="520"
                  placeholderTextColor={Colors.textMuted}
                />
              </View>
            )}

            <Text style={styles.note}>
              Se crea una transferencia desde la cuenta de origen hacia la
              tarjeta y se elimina este ingreso. El pago baja la deuda y no
              cuenta como ingreso.
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
                <Text style={styles.btnSaveText}>Registrar pago</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      <AccountPickerModal
        visible={pickerOpen}
        currentAccountId={source?.id ?? null}
        allowClear={false}
        excludeAccountTypes={["credit"]}
        onClose={() => setPickerOpen(false)}
        onSelect={(acc) => {
          setSource(acc);
          setPickerOpen(false);
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
  note: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    padding: Spacing.sm,
    marginBottom: Spacing.md,
    lineHeight: 19,
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
  btnCancelText: {
    color: Colors.textSecondary,
    fontSize: FontSize.md,
    fontWeight: "500",
  },
  btnSave: { backgroundColor: Colors.accent },
  btnSaveText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
