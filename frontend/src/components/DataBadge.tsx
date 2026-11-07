const TEXT: Record<string, string> = {
  REAL: "REAL · end-of-day, official/attested source",
  DELAYED: "DELAYED · unofficial end-of-day feed",
  SIMULATED: "SIMULATED · not market prices",
};

export function DataBadge({ label }: { label?: string | null }) {
  const l = (label || "UNKNOWN").toUpperCase();
  const cls = l === "REAL" ? "good" : l === "DELAYED" ? "warn" : "bad";
  return <span className={`pill ${cls}`} title={TEXT[l] ?? "provenance unknown"}>DATA: {l}</span>;
}
