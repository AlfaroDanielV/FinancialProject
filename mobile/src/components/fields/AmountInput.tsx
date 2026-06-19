/**
 * Phase 7f — shared money input with live thousands grouping.
 *
 * Displays `1 000 000` (space-grouped, operator-chosen convention) while the
 * form state keeps a plain numeric string ("1000000" / "1000000,5") so every
 * existing `Number(value.replace(",", "."))` parse keeps working. Masking is
 * display-only — the backend payload never sees the spaces.
 *
 * One decimal separator ("," or ".") is preserved as typed; everything else
 * non-numeric is dropped.
 */
import { TextInput, type TextInputProps } from "react-native";

export function sanitizeAmountInput(text: string): string {
  let out = "";
  let sepSeen = false;
  for (const ch of text.replace(/\s+/g, "")) {
    if (ch >= "0" && ch <= "9") {
      out += ch;
    } else if ((ch === "." || ch === ",") && !sepSeen) {
      out += ch;
      sepSeen = true;
    }
  }
  return out;
}

export function formatAmountDisplay(raw: string): string {
  if (!raw) return "";
  const sepIndex = raw.search(/[.,]/);
  const intPart = sepIndex >= 0 ? raw.slice(0, sepIndex) : raw;
  const tail = sepIndex >= 0 ? raw.slice(sepIndex) : "";
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return grouped + tail;
}

interface Props
  extends Omit<TextInputProps, "value" | "onChangeText" | "keyboardType"> {
  /** Raw numeric string: digits + at most one "." or ",". */
  value: string;
  onChangeValue: (raw: string) => void;
}

export function AmountInput({ value, onChangeValue, ...rest }: Props) {
  return (
    <TextInput
      {...rest}
      value={formatAmountDisplay(value)}
      onChangeText={(t) => onChangeValue(sanitizeAmountInput(t))}
      keyboardType="decimal-pad"
      inputMode="decimal"
    />
  );
}
