/**
 * Reusable CR net-salary calculator panel.
 *
 * Used in two places (one component, no duplication): the standalone
 * "Calculadora de salario neto" in the Ingresos screen, and the salary path of
 * the income create form. All math is the backend's `POST /payroll/net-salary`
 * — this panel only gathers inputs and renders the breakdown. `onResult` hands
 * the computed net + gross back to a host form so it can store them.
 */
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";

import { computeNetSalary, type NetSalaryBreakdown } from "../api/payroll";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AmountInput } from "./fields/AmountInput";

interface Props {
  currency: string;
  initialGross?: string;
  onResult?: (net: number, gross: number, breakdown: NetSalaryBreakdown) => void;
}

function parseNum(s: string): number {
  return parseFloat(s.replace(/[^\d.,]/g, "").replace(",", "."));
}

export function SalaryCalculator({ currency, initialGross, onResult }: Props) {
  const [gross, setGross] = useState(initialGross ?? "");
  const [hijos, setHijos] = useState("0");
  const [conyuge, setConyuge] = useState(false);
  const [solidaristaPct, setSolidaristaPct] = useState("");
  const [netOfCcssBase, setNetOfCcssBase] = useState(false);
  const [showOptions, setShowOptions] = useState(false);

  const [breakdown, setBreakdown] = useState<NetSalaryBreakdown | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest onResult without retriggering the debounce effect.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  const isCRC = currency === "CRC";
  const grossNum = parseNum(gross);
  const grossValid = !isNaN(grossNum) && grossNum > 0;

  useEffect(() => {
    if (!isCRC || !grossValid) {
      setBreakdown(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const handle = setTimeout(async () => {
      try {
        const result = await computeNetSalary({
          gross_monthly: Math.round(grossNum),
          hijos: Math.max(0, parseInt(hijos, 10) || 0),
          conyuge,
          solidarista_pct: Math.max(0, parseNum(solidaristaPct) || 0),
          isr_base_mode: netOfCcssBase ? "gross_minus_ccss" : "gross",
        });
        if (cancelled) return;
        setBreakdown(result);
        onResultRef.current?.(result.net_monthly, result.gross_monthly, result);
      } catch {
        if (!cancelled) setError("No se pudo calcular. Revisá el monto.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [gross, hijos, conyuge, solidaristaPct, netOfCcssBase, isCRC, grossValid, grossNum]);

  return (
    <View style={styles.container}>
      <Field label="Salario bruto mensual (antes de deducciones)">
        <AmountInput
          style={styles.input}
          value={gross}
          onChangeValue={setGross}
          placeholder="1 500 000"
          placeholderTextColor={Colors.textMuted}
        />
      </Field>

      {!isCRC && (
        <Text style={styles.note}>
          El cálculo de salario neto (CCSS + renta) aplica solo a colones.
        </Text>
      )}

      <Pressable
        style={styles.optionsToggle}
        onPress={() => setShowOptions((v) => !v)}
      >
        <Feather
          name={showOptions ? "chevron-up" : "chevron-down"}
          size={16}
          color={Colors.accent}
        />
        <Text style={styles.optionsToggleText}>Opciones (hijos, cónyuge, solidarista)</Text>
      </Pressable>

      {showOptions && (
        <View style={styles.options}>
          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Hijos (crédito fiscal)</Text>
            <TextInput
              style={styles.smallInput}
              value={hijos}
              onChangeText={setHijos}
              keyboardType="number-pad"
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
            />
          </View>
          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Cónyuge (crédito fiscal)</Text>
            <Switch
              value={conyuge}
              onValueChange={setConyuge}
              trackColor={{ false: Colors.border, true: Colors.accentSoft }}
              thumbColor={conyuge ? Colors.accent : Colors.bgCard}
            />
          </View>
          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Asociación solidarista (% del bruto)</Text>
            <TextInput
              style={styles.smallInput}
              value={solidaristaPct}
              onChangeText={setSolidaristaPct}
              keyboardType="decimal-pad"
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
            />
          </View>
          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Calcular renta sobre bruto menos CCSS</Text>
            <Switch
              value={netOfCcssBase}
              onValueChange={setNetOfCcssBase}
              trackColor={{ false: Colors.border, true: Colors.accentSoft }}
              thumbColor={netOfCcssBase ? Colors.accent : Colors.bgCard}
            />
          </View>
        </View>
      )}

      {isCRC && grossValid && (
        <View style={styles.result}>
          {loading && !breakdown ? (
            <ActivityIndicator color={Colors.accent} />
          ) : error ? (
            <Text style={styles.error}>{error}</Text>
          ) : breakdown ? (
            <>
              <Text style={styles.netLabel}>Salario neto</Text>
              <Text style={styles.netValue}>
                {formatMoney(breakdown.net_monthly, currency)}/mes
              </Text>
              <View style={styles.divider} />
              <DeductionRow label="Bruto" value={formatMoney(breakdown.gross_monthly, currency)} />
              <DeductionRow
                label="CCSS obrera (10,83%)"
                value={`− ${formatMoney(breakdown.ccss.total, currency)}`}
                color={Colors.expense}
              />
              <DeductionRow
                label="Impuesto de renta"
                value={`− ${formatMoney(breakdown.isr.tax, currency)}`}
                color={Colors.expense}
              />
              {breakdown.solidarista > 0 && (
                <DeductionRow
                  label="Solidarista"
                  value={`− ${formatMoney(breakdown.solidarista, currency)}`}
                  color={Colors.expense}
                />
              )}
              <DeductionRow
                label="Carga total"
                value={`${(breakdown.effective_rate * 100).toFixed(1)}%`}
              />
            </>
          ) : null}
        </View>
      )}
    </View>
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

function DeductionRow({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={styles.deductionRow}>
      <Text style={styles.deductionLabel}>{label}</Text>
      <Text style={[styles.deductionValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: Spacing.sm },
  field: { gap: Spacing.xs },
  fieldLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
  },
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
  smallInput: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.xs,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    minWidth: 80,
    textAlign: "right",
  },
  note: {
    fontSize: FontSize.xs,
    color: Colors.warning,
    fontStyle: "italic",
  },
  optionsToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: Spacing.xs,
  },
  optionsToggleText: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "600",
  },
  options: {
    gap: Spacing.sm,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.md,
    padding: Spacing.sm,
  },
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: Spacing.sm,
  },
  optionLabel: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  result: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: 4,
  },
  netLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "600",
  },
  netValue: {
    fontSize: FontSize.xl,
    fontWeight: "800",
    color: Colors.income,
  },
  divider: {
    height: 1,
    backgroundColor: Colors.borderLight,
    marginVertical: Spacing.xs,
  },
  deductionRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 2,
  },
  deductionLabel: { fontSize: FontSize.sm, color: Colors.textSecondary },
  deductionValue: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  error: { fontSize: FontSize.sm, color: Colors.expense },
});
