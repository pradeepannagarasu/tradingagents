export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail ? String(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data as T;
}

export const fmt = (v: unknown, d = 2) =>
  v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v)) ? "—"
    : typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: d }) : String(v);

export const pct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`;

export const inr = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

export function decisionClass(d: string) {
  return d?.startsWith("BUY") ? "buy" : d?.startsWith("SELL") ? "sell" : "none";
}
