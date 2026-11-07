import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { AgentPanel, JudgePanel } from "../components/Agents";
import { DecisionCard } from "../components/DecisionCard";
import { PriceChart } from "../components/PriceChart";
import { DataBadge } from "../components/DataBadge";

export function Research({ initialId }: { initialId?: string }) {
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [horizon, setHorizon] = useState("SWING");
  const [asOf, setAsOf] = useState("");
  const [rec, setRec] = useState<any>(null);
  const [bars, setBars] = useState<any[]>([]);
  const [label, setLabel] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { if (initialId) api(`/api/decisions/${initialId}`).then(setRec).catch(e => setErr(e.message)); }, [initialId]);
  useEffect(() => {
    api(`/api/market/bars/${encodeURIComponent(symbol)}?limit=6000`).then(d => { setBars(d.bars); setLabel(d.data_label); }).catch(() => setBars([]));
  }, [symbol]);

  async function run() {
    setBusy(true); setErr("");
    try { setRec(await api("/api/research/run", { symbol, horizon, as_of: asOf || null })); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  const plan = rec?.judge?.plan ?? rec?.bull?.plan;
  const levels = useMemo(() => plan ? [
    { price: plan.stop_loss, label: "stop", color: "#c23b3b" },
    { price: plan.target_1, label: "T1", color: "#11845b" },
    { price: plan.target_2, label: "T2", color: "#11845b" },
  ] : [], [plan]);
  const shownBars = useMemo(() => (asOf ? bars.filter(b => b.time <= asOf) : bars).slice(-500), [bars, asOf]);

  return (
    <div className="grid">
      <div className="card row">
        <label>Symbol <input value={symbol} onChange={e => setSymbol(e.target.value)} /></label>
        <label>Horizon <select value={horizon} onChange={e => setHorizon(e.target.value)}>
          <option value="SHORT">1–3 days</option><option value="SWING">Swing (1–4 wk)</option>
          <option value="POSITIONAL">Positional (1–6 mo)</option><option value="INTRADAY">Intraday (needs intraday feed)</option>
        </select></label>
        <label>As of (optional, historical) <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} /></label>
        <button className="btn" onClick={run} disabled={busy}>{busy ? "Running agents…" : "Run BULL / BEAR / JUDGE"}</button>
      </div>
      {err && <div className="error">{err}</div>}
      {shownBars.length > 0 && <div className="card"><h2>{symbol} — daily <DataBadge label={label} /> <span className="muted small">last bar {shownBars[shownBars.length - 1]?.time}</span></h2><PriceChart bars={shownBars} levels={levels} /></div>}
      {rec && (<>
        <DecisionCard rec={rec} />
        <div className="grid cols-3">
          <AgentPanel a={rec.bull} />
          <AgentPanel a={rec.bear} />
          <JudgePanel rec={rec} />
        </div>
      </>)}
    </div>
  );
}
