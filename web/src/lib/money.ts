export type Currency = "CRC" | "USD";

function groupIntegerPart(value: string, separator: string) {
  return value.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
}

export function parseMoneyInput(value: string, currency: Currency): number | null {
  const cleaned = value.trim().replace(/[^\d.,-]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === "." || cleaned === ",") {
    return null;
  }

  let normalized: string;
  if (currency === "CRC") {
    normalized = cleaned.replace(/\./g, "").replace(",", ".");
  } else {
    normalized = cleaned.replace(/,/g, "");
  }

  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatMoneyInput(value: string, currency: Currency) {
  const parsed = parseMoneyInput(value, currency);
  if (parsed === null) return value;

  const negative = parsed < 0;
  const absolute = Math.abs(parsed);
  const fixed = absolute.toFixed(currency === "USD" ? 2 : 2);
  const [integer, fraction] = fixed.split(".");
  const grouped = groupIntegerPart(integer, currency === "CRC" ? "." : ",");

  if (currency === "CRC") {
    const suffix = fraction === "00" ? "" : `,${fraction}`;
    return `${negative ? "-" : ""}${grouped}${suffix}`;
  }

  return `${negative ? "-" : ""}${grouped}.${fraction}`;
}

export function moneyToApiString(value: string, currency: Currency) {
  const parsed = parseMoneyInput(value, currency);
  if (parsed === null) {
    return null;
  }
  return parsed.toFixed(2);
}

export function displayMoney(value: string | number | null | undefined, currency: Currency | string) {
  if (value === null || value === undefined) return "Sin monto";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return formatMoneyInput(String(numeric), currency === "USD" ? "USD" : "CRC");
}
