export const pct = (x) => `${Math.round((x || 0) * 100)}%`;

export function fmtDay(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

export const span = (a, b) => (!a ? "" : a === b ? fmtDay(a) : `${fmtDay(a)} – ${fmtDay(b)}`);

export function fmtShort(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
