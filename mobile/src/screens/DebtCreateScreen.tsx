/**
 * Phase 6f debt slice (D3) — chat-initiated debt registration form.
 *
 * Reached via the chat→form handoff: the chat does light extraction
 * (Intent.CREATE_DEBT) and navigates here with a `prefill`. The user completes
 * the amortization fields, sees a live cuota preview, and submits to the
 * existing POST /debts (the only write path — "LLM extracts; rules decide").
 *
 * Interest rate is entered as a PERCENT and converted to the 0–1 fraction the
 * API stores. The hardest field for a real user is the rate, so:
 *   - "Subí el contrato (PDF)" → POST /debts/parse-document reads it for them.
 *   - If the rate is still missing, submit is blocked with the (voseo) message
 *     telling them to call their lender — an amortization schedule is
 *     meaningless without a rate, so we refuse to create a wrong debt.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation, useRoute, type RouteProp } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createDebt,
  parseDebtDocument,
  DEBT_TYPES,
  DEBT_TYPE_LABELS,
  type DebtType,
} from "../api/debts";
import { computeMonthlyPaymentPreview } from "../lib/amortization";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import type { ChatStackParamList } from "../navigation/ChatNavigator";

type Nav = NativeStackNavigationProp<ChatStackParamList, "DebtCreate">;
type Rt = RouteProp<ChatStackParamList, "DebtCreate">;

const LOW_CONFIDENCE = 0.65;

const NO_RATE_MESSAGE =
  "Por favor llamá a tu entidad financiera para confirmar la tasa de interés. " +
  "Cuando la tengás, volvé y registramos el préstamo.";

function parseNum(s: string): number {
  return parseFloat(s.replace(",", "."));
}

function isValidPositive(s: string): boolean {
  const n = parseNum(s);
  return !isNaN(n) && n > 0;
}

function formatMoney(amount: number, currency: string): string {
  if (currency === "USD") return `$${amount.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return `₡${Math.round(amount).toLocaleString("es-CR")}`;
}

export function DebtCreateScreen() {
  const nav = useNavigation<Nav>();
  // prefill is present on the chat→form handoff; absent when opened fresh from
  // the Deudas tab ("+ Nueva deuda"). Either way the form works.
  const prefill = useRoute<Rt>().params?.prefill;
  const qc = useQueryClient();

  const [name, setName] = useState(prefill?.name ?? "");
  const [debtType, setDebtType] = useState<DebtType>("personal_loan");
  const [originalAmount, setOriginalAmount] = useState(prefill?.original_amount ?? "");
  const [currentBalance, setCurrentBalance] = useState(
    prefill?.current_balance ?? prefill?.original_amount ?? "",
  );
  const [ratePct, setRatePct] = useState(prefill?.interest_rate_pct ?? "");
  const [termMonths, setTermMonths] = useState(
    prefill?.term_months != null ? String(prefill.term_months) : "",
  );
  const [minimumPayment, setMinimumPayment] = useState("");
  const [paymentDueDay, setPaymentDueDay] = useState("");
  const [lender, setLender] = useState(prefill?.lender ?? "");
  const [currency, setCurrency] = useState<"CRC" | "USD">(
    prefill?.currency === "USD" ? "USD" : "CRC",
  );
  const [includesInsurance, setIncludesInsurance] = useState(false);
  const [insuranceMonthly, setInsuranceMonthly] = useState("");
  const [lowConfidenceNote, setLowConfidenceNote] = useState(false);

  // ── live cuota preview ──────────────────────────────────────────────────
  const balanceNum = parseNum(currentBalance || originalAmount);
  const rateNum = parseNum(ratePct);
  const termNum = parseInt(termMonths, 10);
  const insNum = parseNum(insuranceMonthly) || 0;
  const rateProvided = ratePct.trim() !== "" && !isNaN(rateNum) && rateNum >= 0;

  const cuotaPreview =
    !isNaN(balanceNum) && balanceNum > 0 && rateProvided && !isNaN(termNum) && termNum > 0
      ? computeMonthlyPaymentPreview({
          balance: balanceNum,
          annualRate: rateNum / 100,
          termMonths: termNum,
          includesInsurance,
          insuranceMonthly: insNum,
        })
      : null;

  const parseMutation = useMutation({
    mutationFn: (uri: string) => parseDebtDocument(uri),
    onSuccess: (terms) => {
      if (terms.original_amount != null) {
        setOriginalAmount(String(terms.original_amount));
        if (!currentBalance.trim()) setCurrentBalance(String(terms.original_amount));
      }
      if (terms.interest_rate != null) {
        setRatePct(String(Math.round(terms.interest_rate * 10000) / 100));
      }
      if (terms.term_months != null) setTermMonths(String(terms.term_months));
      if (terms.minimum_payment != null) setMinimumPayment(String(terms.minimum_payment));
      if (terms.lender) setLender(terms.lender);
      if (terms.currency === "USD" || terms.currency === "CRC") setCurrency(terms.currency);
      if (terms.includes_insurance != null) setIncludesInsurance(terms.includes_insurance);
      if (terms.insurance_monthly != null) setInsuranceMonthly(String(terms.insurance_monthly));
      setLowConfidenceNote(terms.confidence < LOW_CONFIDENCE);
    },
    onError: () => {
      Alert.alert(
        "No pude leer el PDF",
        "Probá con otro archivo o ingresá los datos a mano.",
      );
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createDebt({
        name: name.trim() || lender.trim() || DEBT_TYPE_LABELS[debtType],
        debt_type: debtType,
        original_amount: parseNum(originalAmount),
        current_balance: currentBalance.trim()
          ? parseNum(currentBalance)
          : parseNum(originalAmount),
        interest_rate: rateNum / 100,
        minimum_payment: parseNum(minimumPayment),
        payment_due_day: parseInt(paymentDueDay, 10),
        term_months: termMonths.trim() ? parseInt(termMonths, 10) : undefined,
        lender: lender.trim() || undefined,
        currency,
        rate_type: "fixed",
        includes_insurance: includesInsurance,
        insurance_monthly: includesInsurance && insuranceMonthly.trim()
          ? parseNum(insuranceMonthly)
          : undefined,
        payments_made: 0,
        prepayment_penalty_pct: 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["debts"] });
      qc.invalidateQueries({ queryKey: ["debtOverview"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      Alert.alert("Listo", "Préstamo registrado.");
      nav.goBack();
    },
    onError: () => {
      Alert.alert("Error", "No se pudo registrar el préstamo. Revisá los datos.");
    },
  });

  const dueDayNum = parseInt(paymentDueDay, 10);

  // ── sanity validations (a debt with impossible numbers is worse than none) ──
  const originalNum = parseNum(originalAmount);
  const minPayNum = parseNum(minimumPayment);
  // Monthly interest on the balance — only meaningful once the rate is known.
  const monthlyInterest =
    rateProvided && !isNaN(balanceNum) && balanceNum > 0
      ? balanceNum * (rateNum / 100 / 12)
      : null;

  const balanceError =
    currentBalance.trim() !== "" &&
    !isNaN(originalNum) &&
    !isNaN(balanceNum) &&
    balanceNum > originalNum
      ? "El saldo actual no puede ser mayor que el monto original."
      : null;

  let paymentError: string | null = null;
  if (isValidPositive(minimumPayment)) {
    if (!isNaN(balanceNum) && balanceNum > 0 && minPayNum >= balanceNum) {
      // The user's case: a cuota ≥ the whole debt isn't a loan.
      paymentError = "La cuota mensual no puede ser mayor o igual al saldo de la deuda.";
    } else if (monthlyInterest != null && minPayNum <= monthlyInterest) {
      // Negative amortization: the payment doesn't even cover the interest.
      paymentError = `La cuota no cubre ni el interés del mes (~${formatMoney(
        monthlyInterest,
        currency,
      )}). Con ese monto el saldo nunca bajaría.`;
    }
  }

  // Backend stores interest_rate as a 0–1 fraction (≤ 100% annual).
  const rateError =
    ratePct.trim() !== "" && !isNaN(rateNum) && rateNum > 100
      ? "La tasa anual no puede superar 100%."
      : null;

  const canSubmit =
    isValidPositive(originalAmount) &&
    rateProvided &&
    isValidPositive(minimumPayment) &&
    !isNaN(dueDayNum) &&
    dueDayNum >= 1 &&
    dueDayNum <= 31 &&
    !balanceError &&
    !paymentError &&
    !rateError &&
    !createMutation.isPending &&
    !parseMutation.isPending;

  const pickPdf = async () => {
    if (parseMutation.isPending || createMutation.isPending) return;
    const result = await DocumentPicker.getDocumentAsync({
      type: "application/pdf",
      copyToCacheDirectory: true,
    });
    if (result.canceled || result.assets.length === 0) return;
    parseMutation.mutate(result.assets[0].uri);
  };

  const useCuotaAsPayment = () => {
    if (cuotaPreview != null) setMinimumPayment(String(Math.round(cuotaPreview)));
  };

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ── PDF upload (rate helper) ─────────────────────────────────────── */}
        <View style={styles.pdfCard}>
          <Text style={styles.pdfTitle}>¿No sabés la tasa de interés?</Text>
          <Text style={styles.pdfBody}>Subí el contrato (PDF) y leo la tasa por vos.</Text>
          <Pressable
            onPress={pickPdf}
            disabled={parseMutation.isPending}
            style={({ pressed }) => [styles.pdfBtn, pressed && { opacity: 0.85 }]}
          >
            {parseMutation.isPending ? (
              <ActivityIndicator color={Colors.accent} />
            ) : (
              <>
                <Feather name="upload" size={16} color={Colors.accent} />
                <Text style={styles.pdfBtnLabel}>Subí el contrato (PDF)</Text>
              </>
            )}
          </Pressable>
          {lowConfidenceNote && (
            <Text style={styles.lowConf}>
              Leí el documento pero no estoy seguro de todos los datos. Revisalos
              antes de guardar.
            </Text>
          )}
        </View>

        {/* ── Name ─────────────────────────────────────────────────────────── */}
        <Field label="Nombre del préstamo">
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="Ej: Préstamo del carro, Hipoteca"
            placeholderTextColor={Colors.textMuted}
            maxLength={120}
          />
        </Field>

        {/* ── Debt type ────────────────────────────────────────────────────── */}
        <Field label="Tipo">
          <View style={styles.segmented}>
            {DEBT_TYPES.map((t) => (
              <Pressable
                key={t}
                onPress={() => setDebtType(t)}
                style={({ pressed }) => [
                  styles.segmentItem,
                  debtType === t && styles.segmentItemActive,
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text
                  style={[styles.segmentLabel, debtType === t && styles.segmentLabelActive]}
                >
                  {DEBT_TYPE_LABELS[t]}
                </Text>
              </Pressable>
            ))}
          </View>
        </Field>

        {/* ── Lender ───────────────────────────────────────────────────────── */}
        <Field label="Entidad (opcional)">
          <TextInput
            style={styles.input}
            value={lender}
            onChangeText={setLender}
            placeholder="Ej: BAC, Banco Nacional"
            placeholderTextColor={Colors.textMuted}
            maxLength={100}
          />
        </Field>

        {/* ── Currency ─────────────────────────────────────────────────────── */}
        <Field label="Moneda">
          <View style={styles.currencyRow}>
            {(["CRC", "USD"] as const).map((c) => (
              <Pressable
                key={c}
                onPress={() => setCurrency(c)}
                style={({ pressed }) => [
                  styles.currencyBtn,
                  currency === c && styles.currencyBtnActive,
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text
                  style={[styles.currencyLabel, currency === c && styles.currencyLabelActive]}
                >
                  {c === "CRC" ? "₡ Colones" : "$ Dólares"}
                </Text>
              </Pressable>
            ))}
          </View>
        </Field>

        {/* ── Amounts ──────────────────────────────────────────────────────── */}
        <Field label="Monto original">
          <TextInput
            style={styles.input}
            value={originalAmount}
            onChangeText={setOriginalAmount}
            placeholder="5000000"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>

        <Field label="Saldo actual" hint="Para un préstamo nuevo, igual al monto original.">
          <TextInput
            style={[styles.input, balanceError != null && styles.inputError]}
            value={currentBalance}
            onChangeText={setCurrentBalance}
            placeholder="5000000"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {balanceError != null && <Text style={styles.fieldError}>{balanceError}</Text>}

        {/* ── Rate (percent) ───────────────────────────────────────────────── */}
        <Field label="Tasa de interés anual (%)">
          <TextInput
            style={[styles.input, rateError != null && styles.inputError]}
            value={ratePct}
            onChangeText={setRatePct}
            placeholder="18"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {rateError != null && <Text style={styles.fieldError}>{rateError}</Text>}

        {!rateProvided && (
          <View style={styles.noRateCard}>
            <Feather name="phone" size={16} color={Colors.warning} />
            <Text style={styles.noRateText}>{NO_RATE_MESSAGE}</Text>
          </View>
        )}

        {/* ── Term ─────────────────────────────────────────────────────────── */}
        <Field label="Plazo (meses, opcional)">
          <TextInput
            style={styles.input}
            value={termMonths}
            onChangeText={setTermMonths}
            placeholder="60"
            placeholderTextColor={Colors.textMuted}
            keyboardType="number-pad"
          />
        </Field>

        {/* ── Cuota preview ────────────────────────────────────────────────── */}
        {cuotaPreview != null && (
          <View style={styles.cuotaCard}>
            <Text style={styles.cuotaLabel}>Cuota estimada</Text>
            <Text style={styles.cuotaValue}>{formatMoney(cuotaPreview, currency)}/mes</Text>
            <Pressable onPress={useCuotaAsPayment} style={styles.cuotaUseBtn}>
              <Text style={styles.cuotaUseLabel}>Usar como cuota mensual</Text>
            </Pressable>
          </View>
        )}

        {/* ── Minimum payment ──────────────────────────────────────────────── */}
        <Field label="Cuota mensual">
          <TextInput
            style={[styles.input, paymentError != null && styles.inputError]}
            value={minimumPayment}
            onChangeText={setMinimumPayment}
            placeholder="126000"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {paymentError != null && <Text style={styles.fieldError}>{paymentError}</Text>}

        {/* ── Payment due day ──────────────────────────────────────────────── */}
        <Field label="Día de pago (1–31)">
          <TextInput
            style={styles.input}
            value={paymentDueDay}
            onChangeText={setPaymentDueDay}
            placeholder="15"
            placeholderTextColor={Colors.textMuted}
            keyboardType="number-pad"
            maxLength={2}
          />
        </Field>

        {/* ── Insurance ────────────────────────────────────────────────────── */}
        <View style={styles.switchRow}>
          <Text style={styles.label}>Incluye seguro</Text>
          <Switch
            value={includesInsurance}
            onValueChange={setIncludesInsurance}
            trackColor={{ false: Colors.border, true: Colors.accentSoft }}
            thumbColor={includesInsurance ? Colors.accent : Colors.bgCard}
          />
        </View>
        {includesInsurance && (
          <Field label="Seguro mensual">
            <TextInput
              style={styles.input}
              value={insuranceMonthly}
              onChangeText={setInsuranceMonthly}
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
              keyboardType="decimal-pad"
            />
          </Field>
        )}

        {/* ── Ley 7472 note ────────────────────────────────────────────────── */}
        <View style={styles.leyCard}>
          <Text style={styles.leyText}>
            Ley 7472: tras 2 cuotas pagadas podés cancelar anticipadamente sin
            penalización. Calculá ahorros desde la pantalla de la deuda.
          </Text>
        </View>

        {/* ── Submit ───────────────────────────────────────────────────────── */}
        <Pressable
          onPress={() => canSubmit && createMutation.mutate()}
          disabled={!canSubmit}
          style={({ pressed }) => [
            styles.submitBtn,
            !canSubmit && styles.submitDisabled,
            pressed && canSubmit && { opacity: 0.85 },
          ]}
        >
          {createMutation.isPending ? (
            <ActivityIndicator color={Colors.textOnDark} />
          ) : (
            <Text style={styles.submitLabel}>Registrar préstamo</Text>
          )}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.fieldGroup}>
      <Text style={styles.label}>{label}</Text>
      {children}
      {hint && <Text style={styles.hint}>{hint}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  scroll: { flex: 1 },
  content: { padding: Spacing.md, gap: Spacing.lg, paddingBottom: Spacing.xl },

  fieldGroup: { gap: Spacing.xs },
  label: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textSecondary,
    letterSpacing: 0.3,
  },
  hint: { fontSize: FontSize.xs, color: Colors.textMuted, lineHeight: 17, marginTop: 2 },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  inputError: {
    borderColor: Colors.expense,
  },
  fieldError: {
    fontSize: FontSize.xs,
    color: Colors.expense,
    marginTop: -Spacing.xs,
    marginBottom: Spacing.sm,
    lineHeight: 17,
  },

  // ── PDF card ──────────────────────────────────────────────────────────────
  pdfCard: {
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  pdfTitle: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  pdfBody: { fontSize: FontSize.sm, color: Colors.textSecondary },
  pdfBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    backgroundColor: Colors.bgCard,
  },
  pdfBtnLabel: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.accent },
  lowConf: { fontSize: FontSize.xs, color: Colors.warning, lineHeight: 17 },

  // ── segmented ───────────────────────────────────────────────────────────────
  segmented: { flexDirection: "row", flexWrap: "wrap", gap: Spacing.sm },
  segmentItem: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.bgCard,
  },
  segmentItemActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  segmentLabel: { fontSize: FontSize.sm, color: Colors.textSecondary, fontWeight: "500" },
  segmentLabelActive: { color: Colors.accent, fontWeight: "600" },

  // ── currency ──────────────────────────────────────────────────────────────
  currencyRow: { flexDirection: "row", gap: Spacing.sm },
  currencyBtn: {
    flex: 1,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    alignItems: "center",
    backgroundColor: Colors.bgCard,
  },
  currencyBtnActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  currencyLabel: { fontSize: FontSize.sm, color: Colors.textSecondary, fontWeight: "500" },
  currencyLabelActive: { color: Colors.accent, fontWeight: "600" },

  // ── no-rate fallback ────────────────────────────────────────────────────────
  noRateCard: {
    flexDirection: "row",
    gap: Spacing.sm,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.md,
    padding: Spacing.md,
    alignItems: "flex-start",
  },
  noRateText: { flex: 1, fontSize: FontSize.sm, color: Colors.warning, lineHeight: 19 },

  // ── cuota preview ───────────────────────────────────────────────────────────
  cuotaCard: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.xs,
  },
  cuotaLabel: { fontSize: FontSize.xs, color: Colors.textMuted, fontWeight: "600" },
  cuotaValue: { fontSize: FontSize.lg, color: Colors.textPrimary, fontWeight: "700" },
  cuotaUseBtn: { marginTop: Spacing.xs },
  cuotaUseLabel: { fontSize: FontSize.sm, color: Colors.accent, fontWeight: "600" },

  // ── insurance switch ────────────────────────────────────────────────────────
  switchRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },

  // ── Ley 7472 ──────────────────────────────────────────────────────────────
  leyCard: {
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.md,
    padding: Spacing.md,
  },
  leyText: { fontSize: FontSize.xs, color: Colors.textSecondary, lineHeight: 18 },

  // ── submit ──────────────────────────────────────────────────────────────────
  submitBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  submitDisabled: { backgroundColor: Colors.border },
  submitLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },
});
