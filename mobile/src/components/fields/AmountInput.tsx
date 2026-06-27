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

/** Cap the fractional part to `places` digits (money = 2). A live input mask, so
 *  it truncates the in-progress value — the user simply can't type past the cap,
 *  which keeps the displayed value === what gets submitted (and a strict
 *  2-decimal backend Decimal can never 422 on a stray 3rd digit). */
export function capDecimals(raw: string, places: number): string {
  const sep = raw.search(/[.,]/);
  if (sep < 0) return raw;
  return raw.slice(0, sep + 1 + places);
}

interface Props
  extends Omit<TextInputProps, "value" | "onChangeText" | "keyboardType"> {
  /** Raw numeric string: digits + at most one "." or ",". */
  value: string;
  onChangeValue: (raw: string) => void;
  /** Cap fractional digits (e.g. 2 for a strict-cents backend field). Default: uncapped. */
  maxDecimals?: number;
}

export function AmountInput({ value, onChangeValue, maxDecimals, ...rest }: Props) {
  return (
    <TextInput
      {...rest}
      value={formatAmountDisplay(value)}
      onChangeText={(t) => {
        const raw = sanitizeAmountInput(t);
        onChangeValue(maxDecimals == null ? raw : capDecimals(raw, maxDecimals));
      }}
      keyboardType="decimal-pad"
      inputMode="decimal"
    />
  );
}
