/**
 * Phase 7b B1 — transfer between own accounts (incl. credit-card payment).
 *
 * Bottom-sheet modal with from/to account pickers, amount, and date. When the
 * destination is a credit account the title and copy switch to card-payment
 * wording. Cross-currency shows an fx_rate field (the chat path rejects those;
 * this modal is the supported surface). Keyboard scaffold copied from
 * TransactionEditModal — KeyboardAvoidingView + ScrollView with
 * keyboardShouldPersistTaps, otherwise the sheet renders behind the keyboard.
 */
import { useEffect, useMemo, useState } from "react";
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchAccounts, type AccountResponse } from "../api/accounts";
import { createTransfer } from "../api/transfers";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";
import { DateField } from "./fields/DateField";

interface Props {
  visible: boolean;
  onClose: () => void;
  /** Prefill the destination (e.g. "Registrar pago" on a credit account). */
  initialToAccountId?: string | null;
}

function todayIso(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

function AccountPicker({
  label,
  accounts,
  selectedId,
  excludeId,
  onSelect,
}: {
  label: string;
  accounts: AccountResponse[];
  selectedId: string | null;
  excludeId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = accounts.find((a) => a.id === selectedId) ?? null;
  const options = accounts.filter((a) => a.id !== excludeId);

  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Pressable
        onPress={() => setOpen((v) => !v)}
        style={({ pressed }) => [
          styles.input,
          styles.selectRow,
          pressed && { opacity: 0.7 },
        ]}
      >
        <Text
          style={[
            styles.selectText,
            selected == null && styles.selectPlaceholder,
          ]}
        >
          {selected
            ? `${selected.name} (${selected.currency})`
            : "Elegí una cuenta"}
        </Text>
        <Feather
          name={open ? "chevron-up" : "chevron-down"}
          size={18}
          color={Colors.textMuted}
        />
      </Pressable>
      {open && (
        <View style={styles.optionList}>
          {options.map((a) => (
            <Pressable
              key={a.id}
              onPress={() => {
                onSelect(a.id);
                setOpen(false);
              }}
              style={({ pressed }) => [
                styles.optionRow,
                a.id === selectedId && styles.optionRowActive,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Text style={styles.optionText}>
                {a.name} ({a.currency})
              </Text>
              {a.account_type === "credit" && (
                <Text style={styles.optionPill}>Crédito</Text>
              )}
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

export function TransferModal({ visible, onClose, initialToAccountId }: Props) {
  const qc = useQueryClient();
  const [fromId, setFromId] = useState<string | null>(null);
  const [toId, setToId] = useState<string | null>(initialToAccountId ?? null);
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(todayIso());
  const [fxRate, setFxRate] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setFromId(null);
      setToId(initialToAccountId ?? null);
      setAmount("");
      setDate(todayIso());
      setFxRate("");
      setError(null);
    }
  }, [visible, initialToAccountId]);

  const { data: accounts } = useQuery({
    queryKey: ["accounts", { archived: false }],
    queryFn: () => fetchAccounts(false),
    enabled: visible,
  });
  const active = useMemo(
    () => (accounts ?? []).filter((a) => !a.archived),
    [accounts],
  );

  const fromAccount = active.find((a) => a.id === fromId) ?? null;
  const toAccount = active.find((a) => a.id === toId) ?? null;
  const isCardPayment = toAccount?.account_type === "credit";
  const crossCurrency =
    fromAccount != null &&
    toAccount != null &&
    fromAccount.currency !== toAccount.currency;

  const mutation = useMutation({
    mutationFn: createTransfer,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account"] });
      qc.invalidateQueries({ queryKey: ["accountTransactions"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
  });

  const save = () => {
    if (!fromAccount || !toAccount) {
      setError("Elegí la cuenta origen y la cuenta destino.");
      return;
    }
    const parsed = Number(amount.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("Ingresá un monto mayor que cero.");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      setError("Usá el formato de fecha AAAA-MM-DD.");
      return;
    }
    let fx: string | null = null;
    if (crossCurrency) {
      const parsedFx = Number(fxRate.replace(",", "."));
      if (!Number.isFinite(parsedFx) || parsedFx <= 0) {
        setError("Ingresá el tipo de cambio para cuentas en monedas distintas.");
        return;
      }
      fx = String(parsedFx);
    }
    setError(null);
    mutation.mutate({
      from_account_id: fromAccount.id,
      to_account_id: toAccount.id,
      amount: String(parsed),
      currency: fromAccount.currency,
      fx_rate: fx,
      occurred_at: `${date}T12:00:00Z`,
    });
  };

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo registrar. Intentá de nuevo." : null);

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
            {isCardPayment ? "Registrar pago de tarjeta" : "Transferir entre cuentas"}
          </Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <AccountPicker
              label="Desde"
              accounts={active}
              selectedId={fromId}
              excludeId={toId}
              onSelect={setFromId}
            />
            <AccountPicker
              label="Hacia"
              accounts={active}
              selectedId={toId}
              excludeId={fromId}
              onSelect={setToId}
            />
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>
                Monto{fromAccount ? ` (${fromAccount.currency})` : ""}
              </Text>
              <AmountInput
                value={amount}
                onChangeValue={setAmount}
                style={styles.input}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Fecha</Text>
              <DateField value={date} onChange={setDate} style={styles.input} />
            </View>
            {crossCurrency && (
              <View style={styles.field}>
                <Text style={styles.fieldLabel}>
                  Tipo de cambio ({fromAccount?.currency} → {toAccount?.currency})
                </Text>
                <TextInput
                  value={fxRate}
                  onChangeText={setFxRate}
                  keyboardType="decimal-pad"
                  style={styles.input}
                  placeholder="500"
                  placeholderTextColor={Colors.textMuted}
                />
              </View>
            )}
            {isCardPayment && (
              <Text style={styles.cardNote}>
                El pago baja la deuda de la tarjeta y no cuenta como un gasto
                nuevo (tus compras ya contaron cuando las hiciste).
              </Text>
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
                <Text style={styles.btnSaveText}>
                  {isCardPayment ? "Registrar pago" : "Transferir"}
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
  optionList: {
    marginTop: Spacing.xs,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    backgroundColor: Colors.bgCard,
    overflow: "hidden",
  },
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  optionRowActive: { backgroundColor: Colors.accentBg },
  optionText: { fontSize: FontSize.md, color: Colors.textPrimary },
  optionPill: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.xs,
    paddingVertical: 1,
  },
  cardNote: {
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
