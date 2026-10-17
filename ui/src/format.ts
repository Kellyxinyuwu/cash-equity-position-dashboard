// Display only. These functions do not compute P&L, weight, or VaR.

const MISSING = "missing close";

export function money(value: string | null): string {
  if (value === null) return MISSING;
  const negative = value.startsWith("-");
  const [whole, frac = ""] = value.replace("-", "").split(".");
  const cents = `${frac}00`.slice(0, 2);
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "−" : ""}${grouped}.${cents}`;
}

/** P&L only. A gain gets a plus so the sign is visible without the green. */
export function signedMoney(value: string | null): string {
  const text = money(value);
  if (value === null || value.startsWith("-") || Number(value) === 0) return text;
  return `+${text}`;
}

export function shares(value: string): string {
  const [whole, frac] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (!frac || /^0+$/.test(frac)) return grouped;
  return `${grouped}.${frac}`;
}

/** A split factor. 1 means the share count did not change. */
export function factor(value: string): string {
  return shares(value);
}

/** Show a Python ratio as a percent. 0.696... displays as 69.6%. */
export function percent(value: string | null): string {
  if (value === null) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

export function signClass(value: string | null): string {
  if (value === null) return "missing";
  if (value.startsWith("-")) return "loss";
  if (Number(value) === 0) return "";
  return "gain";
}

export function fetchedAt(value: string | null): string {
  if (value === null) return MISSING;
  // Keep the UTC clock from the string. A Date would shift it into the local zone.
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]} UTC`;
}
