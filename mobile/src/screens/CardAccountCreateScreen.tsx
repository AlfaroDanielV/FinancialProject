/**
 * Phase 7b B3 — credit-card account creation (mirrors DebtCreateScreen).
 *
 * Reached from the chat handoff (Intent.CREATE_CARD → open_screen
 * "card_create" with a prefill) or from AccountCreateScreen when the user
 * picks the "credit" type. The user can upload the CARD CONTRACT (PDF) —
 * POST /accounts/parse-card-document reads the rates/fechas/mínimo for them
 * (a statement works too, but the contract is where the terms live).
 *
 * Dual currency: most CR cards run in ₡ AND $. "₡ + $ Ambas" creates TWO
 * credit accounts ("<nombre> ₡" and "<nombre> $"), each with its own terms —
 * each currency keeps its own live balance, minimum, analysis, and payment,
 * exactly like the bank's statement separates them. The PDF extraction
 * auto-switches to Ambas when the contract lists separate dollar terms.
 *
 * Submit = deterministic writes only: POST /accounts (initial_balance =
 * NEGATIVE owed balance — we handle the sign) then PUT
 * /accounts/{id}/card-terms per currency. Rates are entered as PERCENT and
 * converted to the 0–1 fraction the API stores. Without the purchase rate +
 * minimum formula the analysis is meaningless, so submit is blocked with the
 * "llamá a tu banco" fallback.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation, useRoute } from "@react-navigation/native";
import * as DocumentPicker from "expo-document-picker";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { createAccount } from "../api/accounts";
import { parseCardDocument, upsertCardTerms } from "../api/cardTerms";
import type { CardPrefill } from "../api/chat";
import { Colors, FontSize, Radius, Spacing } from "../theme";

const LOW_CONFIDENCE = 0.65;

const NO_RATE_MESSAGE =
  "Por favor llamá a tu banco y pedí la tasa anual de compras y el " +
  "porcentaje de pago mínimo. Cuando los tengás, volvé y registramos la " +
  "tarjeta.";

type CurrencyMode = "CRC" | "USD" | "BOTH";

function parseNum(s: string): number {
  return parseFloat(s.replace(",", "."));
}

function formatMoney(amount: number, currency: string): string {
  if (currency === "USD")
    return `$${amount.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return `₡${Math.round(amount).toLocaleString("es-CR")}`;
}

export function CardAccountCreateScreen() {
  const nav = useNavigation();
  const prefill = (
    useRoute().params as { prefill?: CardPrefill } | undefined
  )?.prefill;
  const qc = useQueryClient();

  const [name, setName] = useState(prefill?.name ?? "");
  const [issuer, setIssuer] = useState(prefill?.issuer ?? "");
  const [mode, setMode] = useState<CurrencyMode>(
    prefill?.currency === "USD" ? "USD" : "CRC",
  );
  // Per-currency figures. The CRC fields double as the single-currency
  // fields when mode === "USD" (relabeled in the UI).
  const [balanceOwed, setBalanceOwed] = useState("");
  const [balanceOwedUsd, setBalanceOwedUsd] = useState("");
  const [creditLimit, setCreditLimit] = useState(prefill?.credit_limit ?? "");
  const [creditLimitUsd, setCreditLimitUsd] = useState("");
  const [ratePct, setRatePct] = useState("");
  const [ratePctUsd, setRatePctUsd] = useState("");
  const [minFloor, setMinFloor] = useState("");
  const [minFloorUsd, setMinFloorUsd] = useState("");
  // Shared terms (same plastic): minimum %, corte, fecha límite.
  const [minPct, setMinPct] = useState("");
  const [statementDay, setStatementDay] = useState("");
  const [dueDay, setDueDay] = useState("");
  const [lowConfidenceNote, setLowConfidenceNote] = useState(false);

  const dual = mode === "BOTH";
  const rateNum = parseNum(ratePct);
  const rateUsdNum = ratePctUsd.trim() ? parseNum(ratePctUsd) : rateNum;
  const minPctNum = parseNum(minPct);
  const balanceNum = parseNum(balanceOwed) || 0;
  const balanceUsdNum = parseNum(balanceOwedUsd) || 0;
  const floorNum = parseNum(minFloor) || 0;
  const floorUsdNum = parseNum(minFloorUsd) || 0;
  const dueDayNum = parseInt(dueDay, 10);
  const stmtDayNum = parseInt(statementDay, 10);

  const rateProvided = ratePct.trim() !== "" && !isNaN(rateNum) && rateNum >= 0;
  const minPctProvided =
    minPct.trim() !== "" && !isNaN(minPctNum) && minPctNum > 0;

  // Live preview: mínimo ≈ max(pct × saldo, piso), per currency.
  const preview = (balance: number, floor: number): number | null =>
    minPctProvided && balance > 0
      ? Math.min(balance, Math.max(balance * (minPctNum / 100), floor))
      : null;
  const minimumPreview = preview(balanceNum, floorNum);
  const minimumPreviewUsd = dual ? preview(balanceUsdNum, floorUsdNum) : null;

  const rateError =
    ratePct.trim() !== "" && !isNaN(rateNum) && rateNum > 100
      ? "La tasa anual no puede superar 100%."
      : null;
  const rateUsdError =
    ratePctUsd.trim() !== "" && (isNaN(parseNum(ratePctUsd)) || parseNum(ratePctUsd) > 100)
      ? "La tasa anual no puede superar 100%."
      : null;
  const minPctError =
    minPct.trim() !== "" && !isNaN(minPctNum) && (minPctNum <= 0 || minPctNum > 100)
      ? "El pago mínimo va entre 0 y 100%."
      : null;

  const parseMutation = useMutation({
    mutationFn: (uri: string) => parseCardDocument(uri),
    onSuccess: (terms) => {
      if (terms.issuer && !issuer.trim()) setIssuer(terms.issuer);
      if (terms.credit_limit != null) setCreditLimit(String(terms.credit_limit));
      if (terms.statement_balance != null)
        setBalanceOwed(String(terms.statement_balance));
      if (terms.annual_interest_rate != null)
        setRatePct(String(Math.round(terms.annual_interest_rate * 10000) / 100));
      if (terms.minimum_payment_pct != null)
        setMinPct(String(Math.round(terms.minimum_payment_pct * 10000) / 100));
      if (terms.minimum_payment_amount != null && !minFloor.trim())
        setMinFloor(String(terms.minimum_payment_amount));
      if (terms.statement_day != null) setStatementDay(String(terms.statement_day));
      if (terms.payment_due_day != null) setDueDay(String(terms.payment_due_day));
      // Separate dollar terms in the contract ⟹ the card runs in both
      // currencies — switch to Ambas and pre-fill the $ side.
      const hasUsd =
        terms.annual_interest_rate_usd != null ||
        terms.credit_limit_usd != null ||
        terms.statement_balance_usd != null;
      if (hasUsd) {
        setMode("BOTH");
        if (terms.annual_interest_rate_usd != null)
          setRatePctUsd(
            String(Math.round(terms.annual_interest_rate_usd * 10000) / 100),
          );
        if (terms.credit_limit_usd != null)
          setCreditLimitUsd(String(terms.credit_limit_usd));
        if (terms.statement_balance_usd != null)
          setBalanceOwedUsd(String(terms.statement_balance_usd));
      } else if (terms.currency === "USD" || terms.currency === "CRC") {
        setMode(terms.currency);
      }
      setLowConfidenceNote(terms.confidence < LOW_CONFIDENCE);
    },
    onError: () => {
      Alert.alert(
        "No pude leer el PDF",
        "Probá con otro archivo o ingresá los datos a mano.",
      );
    },
  });

  const baseName = () =>
    name.trim() || (issuer.trim() ? `Tarjeta ${issuer.trim()}` : "Tarjeta");

  const createOne = async (opts: {
    accountName: string;
    currency: "CRC" | "USD";
    owed: number;
    ratePctValue: number;
    floor: string;
    limit: string;
  }) => {
    const account = await createAccount({
      name: opts.accountName,
      account_type: "credit",
      currency: opts.currency,
      // Sign handled for the user: owing ₡200.000 → initial_balance -200000.
      initial_balance: String(-Math.abs(opts.owed)),
    });
    await upsertCardTerms(account.id, {
      annual_interest_rate: String(opts.ratePctValue / 100),
      minimum_payment_pct: String(minPctNum / 100),
      minimum_payment_floor: opts.floor.trim()
        ? String(parseNum(opts.floor))
        : null,
      credit_limit: opts.limit.trim() ? String(parseNum(opts.limit)) : null,
      statement_day: statementDay.trim() ? stmtDayNum : null,
      payment_due_day: dueDayNum,
    });
    return account;
  };

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!dual) {
        const currency = mode as "CRC" | "USD";
        try {
          return await createOne({
            accountName: baseName(),
            currency,
            owed: balanceNum,
            ratePctValue: rateNum,
            floor: minFloor,
            limit: creditLimit,
          });
        } catch (err) {
          // The account may exist with the terms write failing — keep it.
          Alert.alert(
            "Revisá la tarjeta",
            "Si la cuenta se creó sin términos, podés agregarlos desde el detalle.",
          );
          throw err;
        }
      }
      // Dual currency: one account per moneda, shared plastic. Each keeps
      // its own live balance / minimum / analysis / payment — like the
      // bank's statement separates them.
      const crc = await createOne({
        accountName: `${baseName()} ₡`,
        currency: "CRC",
        owed: balanceNum,
        ratePctValue: rateNum,
        floor: minFloor,
        limit: creditLimit,
      });
      try {
        await createOne({
          accountName: `${baseName()} $`,
          currency: "USD",
          owed: balanceUsdNum,
          ratePctValue: rateUsdNum,
          floor: minFloorUsd,
          limit: creditLimitUsd,
        });
      } catch {
        Alert.alert(
          "Falta la parte en dólares",
          "Se creó la tarjeta en colones, pero la parte en dólares falló. " +
            "Creala desde Cuentas cuando querás.",
        );
      }
      return crc;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      Alert.alert(
        "Listo",
        dual ? "Tarjeta registrada en colones y dólares." : "Tarjeta registrada.",
      );
      nav.goBack();
    },
    onError: () => {
      Alert.alert("Error", "No se pudo registrar la tarjeta. Revisá los datos.");
    },
  });

  const canSubmit =
    rateProvided &&
    minPctProvided &&
    !isNaN(dueDayNum) &&
    dueDayNum >= 1 &&
    dueDayNum <= 31 &&
    (statementDay.trim() === "" || (stmtDayNum >= 1 && stmtDayNum <= 31)) &&
    !rateError &&
    !rateUsdError &&
    !minPctError &&
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

  const singleCurrency = mode === "USD" ? "USD" : "CRC";
  const singleSymbol = mode === "USD" ? "$" : "₡";

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ── PDF upload (contract-first) ──────────────────────────────────── */}
        <View style={styles.pdfCard}>
          <Text style={styles.pdfTitle}>¿Tenés el contrato de la tarjeta?</Text>
          <Text style={styles.pdfBody}>
            Subilo en PDF y leo la tasa, el pago mínimo y las fechas por vos.
            Ahí es donde vienen los términos reales (un estado de cuenta
            también sirve).
          </Text>
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
              Leí el documento pero no estoy seguro de todos los datos.
              Revisalos antes de guardar.
            </Text>
          )}
        </View>

        <Field label="Nombre de la tarjeta">
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="Ej: Visa BAC, Mastercard Promerica"
            placeholderTextColor={Colors.textMuted}
            maxLength={80}
          />
        </Field>

        <Field label="Banco emisor (opcional)">
          <TextInput
            style={styles.input}
            value={issuer}
            onChangeText={setIssuer}
            placeholder="Ej: BAC, Promerica"
            placeholderTextColor={Colors.textMuted}
            maxLength={100}
          />
        </Field>

        <Field
          label="Moneda"
          hint={
            dual
              ? "Se crean dos cuentas (una ₡ y una $) que comparten los términos — cada moneda lleva su saldo y su pago mínimo por aparte, igual que en el estado del banco."
              : undefined
          }
        >
          <View style={styles.currencyRow}>
            {(
              [
                ["CRC", "₡ Colones"],
                ["USD", "$ Dólares"],
                ["BOTH", "₡ + $ Ambas"],
              ] as const
            ).map(([value, label]) => (
              <Pressable
                key={value}
                onPress={() => setMode(value)}
                style={({ pressed }) => [
                  styles.currencyBtn,
                  mode === value && styles.currencyBtnActive,
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text
                  style={[
                    styles.currencyLabel,
                    mode === value && styles.currencyLabelActive,
                  ]}
                >
                  {label}
                </Text>
              </Pressable>
            ))}
          </View>
        </Field>

        {/* ── balances ─────────────────────────────────────────────────────── */}
        <Field
          label={dual ? "¿Cuánto debés hoy en colones (₡)?" : "¿Cuánto debés hoy?"}
          hint="Si está en cero, dejalo en 0 — nosotros nos encargamos del signo."
        >
          <TextInput
            style={styles.input}
            value={balanceOwed}
            onChangeText={setBalanceOwed}
            placeholder="0"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {dual && (
          <Field label="¿Cuánto debés hoy en dólares ($)?">
            <TextInput
              style={styles.input}
              value={balanceOwedUsd}
              onChangeText={setBalanceOwedUsd}
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
              keyboardType="decimal-pad"
            />
          </Field>
        )}

        {/* ── limits ───────────────────────────────────────────────────────── */}
        <Field
          label={
            dual
              ? "Límite de crédito en colones (₡, opcional)"
              : `Límite de crédito (${singleSymbol}, opcional)`
          }
        >
          <TextInput
            style={styles.input}
            value={creditLimit}
            onChangeText={setCreditLimit}
            placeholder={mode === "USD" ? "4000" : "2000000"}
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {dual && (
          <Field label="Límite de crédito en dólares ($, opcional)">
            <TextInput
              style={styles.input}
              value={creditLimitUsd}
              onChangeText={setCreditLimitUsd}
              placeholder="4000"
              placeholderTextColor={Colors.textMuted}
              keyboardType="decimal-pad"
            />
          </Field>
        )}

        {/* ── rates ────────────────────────────────────────────────────────── */}
        <Field
          label={
            dual
              ? "Tasa anual de compras en colones (%)"
              : "Tasa anual de compras (%)"
          }
        >
          <TextInput
            style={[styles.input, rateError != null && styles.inputError]}
            value={ratePct}
            onChangeText={setRatePct}
            placeholder="45"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {rateError != null && <Text style={styles.fieldError}>{rateError}</Text>}
        {dual && (
          <>
            <Field
              label="Tasa anual de compras en dólares (%)"
              hint="Suele ser más baja que la de colones. Si la dejás vacía, uso la misma de colones."
            >
              <TextInput
                style={[styles.input, rateUsdError != null && styles.inputError]}
                value={ratePctUsd}
                onChangeText={setRatePctUsd}
                placeholder="39"
                placeholderTextColor={Colors.textMuted}
                keyboardType="decimal-pad"
              />
            </Field>
            {rateUsdError != null && (
              <Text style={styles.fieldError}>{rateUsdError}</Text>
            )}
          </>
        )}

        <Field
          label="Pago mínimo (% del saldo)"
          hint="En Costa Rica suele ser entre 2% y 5% del saldo. Aplica a ambas monedas."
        >
          <TextInput
            style={[styles.input, minPctError != null && styles.inputError]}
            value={minPct}
            onChangeText={setMinPct}
            placeholder="2.5"
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {minPctError != null && <Text style={styles.fieldError}>{minPctError}</Text>}

        <Field
          label={
            dual
              ? "Pago mínimo base en colones (₡, opcional)"
              : `Pago mínimo base (${singleSymbol}, opcional)`
          }
        >
          <TextInput
            style={styles.input}
            value={minFloor}
            onChangeText={setMinFloor}
            placeholder={mode === "USD" ? "10" : "5000"}
            placeholderTextColor={Colors.textMuted}
            keyboardType="decimal-pad"
          />
        </Field>
        {dual && (
          <Field label="Pago mínimo base en dólares ($, opcional)">
            <TextInput
              style={styles.input}
              value={minFloorUsd}
              onChangeText={setMinFloorUsd}
              placeholder="10"
              placeholderTextColor={Colors.textMuted}
              keyboardType="decimal-pad"
            />
          </Field>
        )}

        {(!rateProvided || !minPctProvided) && (
          <View style={styles.noRateCard}>
            <Feather name="phone" size={16} color={Colors.warning} />
            <Text style={styles.noRateText}>{NO_RATE_MESSAGE}</Text>
          </View>
        )}

        {(minimumPreview != null || minimumPreviewUsd != null) && (
          <View style={styles.previewCard}>
            <Text style={styles.previewLabel}>Pago mínimo estimado hoy</Text>
            {minimumPreview != null && (
              <Text style={styles.previewValue}>
                {formatMoney(minimumPreview, dual ? "CRC" : singleCurrency)}
              </Text>
            )}
            {minimumPreviewUsd != null && (
              <Text style={styles.previewValue}>
                {formatMoney(minimumPreviewUsd, "USD")}
              </Text>
            )}
          </View>
        )}

        <Field label="Día de corte (1–31, opcional)">
          <TextInput
            style={styles.input}
            value={statementDay}
            onChangeText={setStatementDay}
            placeholder="20"
            placeholderTextColor={Colors.textMuted}
            keyboardType="number-pad"
            maxLength={2}
          />
        </Field>

        <Field label="Día límite de pago (1–31)">
          <TextInput
            style={styles.input}
            value={dueDay}
            onChangeText={setDueDay}
            placeholder="10"
            placeholderTextColor={Colors.textMuted}
            keyboardType="number-pad"
            maxLength={2}
          />
        </Field>

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
            <Text style={styles.submitLabel}>
              {dual ? "Registrar tarjeta (₡ y $)" : "Registrar tarjeta"}
            </Text>
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
  inputError: { borderColor: Colors.expense },
  fieldError: {
    fontSize: FontSize.xs,
    color: Colors.expense,
    marginTop: -Spacing.xs,
    marginBottom: Spacing.sm,
    lineHeight: 17,
  },

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

  noRateCard: {
    flexDirection: "row",
    gap: Spacing.sm,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.md,
    padding: Spacing.md,
    alignItems: "flex-start",
  },
  noRateText: { flex: 1, fontSize: FontSize.sm, color: Colors.warning, lineHeight: 19 },

  previewCard: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.xs,
  },
  previewLabel: { fontSize: FontSize.xs, color: Colors.textMuted, fontWeight: "600" },
  previewValue: { fontSize: FontSize.lg, color: Colors.textPrimary, fontWeight: "700" },

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
