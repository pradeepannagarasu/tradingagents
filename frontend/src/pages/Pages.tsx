import { useEffect, useState } from "react";
import { api, decisionClass, fmt, inr, pct } from "../api";
import { DataBadge } from "../components/DataBadge";

function useLoad<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const reload = () => api<T>(path).then(d => { setData(d); setErr(""); }).catch(e => setErr(e.message));
  useEffect(() => { reload(); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err, reload };
}

export function Dashboard({ open, tick }: { open: (id: string) => void; tick: number }) {
  const ov = useLoad<any>("/api/market/overview", [tick]);
  const dec = useLoad<any[]>("/api/decisions?limit=12", [tick]);
  const n = ov.data?.items?.["NIFTY 50"];
  const vix = ov.data?.items?.["INDIA VIX"];
  return (
    <div className="grid">
      {n?.simulated && <div className="notice">The loaded NIFTY 50 series is SIMULATED. Load real data on the Data page before drawing any conclusion.</div>}
      <div className="grid cols-4">
        <div className="card"><h2>NIFTY 50 {n && <DataBadge label={n.data_label} />}</h2>
          {n ? (<><div className="stat">{fmt(n.last)}</div>
            <div className={n.change >= 0 ? "up" : "down"}>{n.change >= 0 ? "+" : ""}{fmt(n.change)} ({pct(n.change_pct, 2)})</div>
            <div className="small muted">as of {n.as_of} · {n.source}</div></>) : <p className="muted">Not loaded</p>}
        </div>
        <div className="card"><h2>India VIX</h2>{vix ? <div className="stat">{fmt(vix.last)}</div> : <p className="muted small">Not loaded — volatility gate uses realised volatility only.</p>}</div>
        <div className="card"><h2>Market regime</h2>{n ? (<><div className="stat">{n.regime}</div><div className="small muted">volatility: {n.vol_regime} (model estimate)</div></>) : "—"}</div>
        <div className="card"><h2>Active signals</h2><div className="stat">{dec.data?.filter(d => d.final_decision !== "NO TRADE").length ?? 0}</div><div className="small muted">of last {dec.data?.length ?? 0} decisions</div></div>
      </div>
      <div className="grid cols-2">
        <div className="card"><h2>Recent decisions</h2>
          <table><thead><tr><th>Time</th><th>Instrument</th><th>Horizon</th><th>Decision</th></tr></thead>
            <tbody>{dec.data?.map(d => (
              <tr key={d.id} style={{ cursor: "pointer" }} onClick={() => open(d.id)}>
                <td className="small">{String(d.created_at).slice(0, 16)}</td><td>{d.symbol}</td><td>{d.horizon}</td>
                <td className={`decision-cell ${decisionClass(d.final_decision)}`}><b className={decisionClass(d.final_decision) === "buy" ? "up" : decisionClass(d.final_decision) === "sell" ? "down" : "muted"}>{d.final_decision}</b>{d.simulated ? " · sim" : ""}</td></tr>))}</tbody></table>
        </div>
        <div className="card"><h2>Not connected (shown, never faked)</h2>
          <ul className="tight">{ov.data?.unavailable?.map((u: string) => <li key={u}>{u}</li>)}</ul>
          <h2 style={{ marginTop: 14 }}>Upcoming events</h2>
          {ov.data?.events?.length ? <ul className="tight">{ov.data.events.map((e: any) => <li key={e.id}>{e.date} — {e.name}</li>)}</ul> : <p className="small muted">No event calendar loaded (config/events). Scheduled events can be monitored; unexpected events cannot be known in advance.</p>}
          <h2 style={{ marginTop: 14 }}>Latest verified news</h2>
          {ov.data?.news?.length ? <ul className="tight small">{ov.data.news.filter((x: any) => x.verified).slice(0, 8).map((x: any) => <li key={x.id}><a href={x.url} target="_blank" rel="noreferrer">{x.title}</a> <span className="muted">— {x.source}, {String(x.published_at).slice(0, 16)}</span></li>)}</ul> : <p className="small muted">No news fetched. Use “Refresh official news” on the Data page.</p>}
        </div>
      </div>
    </div>
  );
}

export function History({ open }: { open: (id: string) => void }) {
  const dec = useLoad<any[]>("/api/decisions?limit=200");
  const sc = useLoad<any>("/api/scorecard");
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [msg, setMsg] = useState("");
  async function evaluate() {
    const r = await api(`/api/outcomes/evaluate/${encodeURIComponent(symbol)}`, {});
    setMsg(`Evaluated ${r.evaluated} decisions against subsequent prices.`); sc.reload();
  }
  return (
    <div className="grid">
      <div className="card"><h2>Agent scorecard</h2>
        <div className="row" style={{ marginBottom: 10 }}><input value={symbol} onChange={e => setSymbol(e.target.value)} />
          <button className="btn secondary" onClick={evaluate}>Evaluate outcomes</button><span className="small muted">{msg}</span></div>
        {sc.data && Object.keys(sc.data).length ? (<>
          <table><thead><tr><th>Agent</th><th className="num">Plans resolved</th><th className="num">Win rate (95% CI)</th><th className="num">Expectancy (R)</th><th className="num">Avg loss (R)</th><th className="num">Max DD (R)</th><th></th></tr></thead>
            <tbody>{["BULL", "BEAR", "JUDGE"].map(a => { const s = sc.data[a]; return s && (
              <tr key={a}><td>{a}</td><td className="num">{s.n}</td><td className="num">{pct(s.win_rate, 0)} ({pct(s.win_rate_95ci[0], 0)}–{pct(s.win_rate_95ci[1], 0)})</td>
                <td className="num">{fmt(s.avg_r, 3)}</td><td className="num">{fmt(s.avg_loss_r, 2)}</td><td className="num">{fmt(s.max_drawdown_r, 2)}</td>
                <td>{s.sample_warning && <span className="pill warn">n &lt; 30</span>}</td></tr>); })}</tbody></table>
          <p className="small">NO TRADE decisions: {sc.data.NO_TRADE.n} — avoided loss {sc.data.NO_TRADE.avoided_loss}, missed opportunity {sc.data.NO_TRADE.missed_opportunity}, neutral {sc.data.NO_TRADE.neutral}.</p>
          <p className="small muted">The judge weights agents by sample-size-shrunk expectancy, not raw win rate.</p></>) : <p className="muted small">No evaluated outcomes yet.</p>}
      </div>
      <div className="card"><h2>Signal history ({dec.data?.length ?? 0})</h2>
        <table><thead><tr><th>Created</th><th>Instrument</th><th>Horizon</th><th>Decision</th><th>Data</th></tr></thead>
          <tbody>{dec.data?.map(d => <tr key={d.id} style={{ cursor: "pointer" }} onClick={() => open(d.id)}>
            <td className="small">{String(d.created_at).slice(0, 19)}</td><td>{d.symbol}</td><td>{d.horizon}</td><td>{d.final_decision}</td><td>{d.simulated ? <span className="pill warn">simulated</span> : "real"}</td></tr>)}</tbody></table>
      </div>
    </div>
  );
}

export function Backtest() {
  const [form, setForm] = useState({ symbol: "NIFTY 50", strategy: "trend_pullback", stop_atr: 2, target_r: 2, max_hold_bars: 15, walk_forward: true });
  const [res, setRes] = useState<any>(null);
  const [cal, setCal] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  async function run() { setBusy("bt"); setErr(""); try { setRes(await api("/api/backtest", form)); } catch (e: any) { setErr(e.message); } setBusy(""); }
  async function calibrate() { setBusy("cal"); setErr(""); try { setCal(await api("/api/calibration", { symbol: form.symbol })); } catch (e: any) { setErr(e.message); } setBusy(""); }
  const m = res?.metrics;
  return (
    <div className="grid">
      <div className="card row">
        <input value={form.symbol} onChange={e => setForm({ ...form, symbol: e.target.value })} />
        <select value={form.strategy} onChange={e => setForm({ ...form, strategy: e.target.value })}>
          {["trend_pullback", "volume_breakout", "macd_trend", "bollinger_reversion"].map(s => <option key={s}>{s}</option>)}</select>
        <label>stop ATR <input type="number" step="0.5" style={{ width: 70 }} value={form.stop_atr} onChange={e => setForm({ ...form, stop_atr: +e.target.value })} /></label>
        <label>target R <input type="number" step="0.5" style={{ width: 70 }} value={form.target_r} onChange={e => setForm({ ...form, target_r: +e.target.value })} /></label>
        <label><input type="checkbox" checked={form.walk_forward} onChange={e => setForm({ ...form, walk_forward: e.target.checked })} /> walk-forward</label>
        <button className="btn" onClick={run} disabled={!!busy}>{busy === "bt" ? "Running…" : "Run backtest"}</button>
        <button className="btn secondary" onClick={calibrate} disabled={!!busy}>{busy === "cal" ? "Replaying agents…" : "Calibrate judge (OOS)"}</button>
      </div>
      {err && <div className="error">{err}</div>}
      {res && <DataBadge label={res.data_label} />}
      {res?.simulated_data && <div className="notice">Backtest on SIMULATED data — validates the engine, says nothing about real NIFTY performance.</div>}
      {m && (<div className="grid cols-2">
        <div className="card"><h2>Metrics (after costs & slippage)</h2>
          <dl className="kv">
            <dt>Total return</dt><dd>{pct(m.total_return)}</dd><dt>CAGR</dt><dd>{pct(m.cagr)}</dd>
            <dt>Volatility</dt><dd>{pct(m.annualised_volatility)}</dd><dt>Sharpe / Sortino</dt><dd>{m.sharpe} / {m.sortino}</dd>
            <dt>Max drawdown</dt><dd>{pct(m.max_drawdown)}</dd><dt>Profit factor</dt><dd>{fmt(m.profit_factor)}</dd>
            <dt>Win rate (95% CI)</dt><dd>{pct(m.win_rate)} ({pct(m.win_rate_95ci[0], 0)}–{pct(m.win_rate_95ci[1], 0)})</dd>
            <dt>Avg win / loss</dt><dd>{inr(m.avg_win)} / {inr(m.avg_loss)}</dd><dt>Expectancy / trade</dt><dd>{inr(m.expectancy_per_trade)} ({m.avg_r_multiple} R)</dd>
            <dt>Trades</dt><dd>{m.n_trades}</dd><dt>Max consecutive losses</dt><dd>{m.max_consecutive_losses}</dd>
            <dt>Recovery factor</dt><dd>{fmt(m.recovery_factor)}</dd><dt>Turnover</dt><dd>{inr(m.turnover)}</dd>
            <dt>Brokerage & taxes</dt><dd>{inr(m.total_costs)}</dd><dt>Slippage</dt><dd>{inr(m.total_slippage)}</dd>
            <dt>Net profit after costs</dt><dd>{inr(m.net_profit_after_costs)}</dd>
          </dl>
          <h3 style={{ marginTop: 10 }}>Benchmark</h3><p className="small">{res.benchmark.name}: return {pct(res.benchmark.total_return)}, CAGR {pct(res.benchmark.cagr)}, max DD {pct(res.benchmark.max_drawdown)}</p>
          {res.warnings.map((w: string, i: number) => <div key={i} className="notice">{w}</div>)}
        </div>
        <div className="card"><h2>By regime</h2>
          <table><thead><tr><th>Regime</th><th className="num">Trades</th><th className="num">Win</th><th className="num">Avg R</th></tr></thead>
            <tbody>{Object.entries(res.by_regime).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{v.n}</td><td className="num">{pct(v.win_rate, 0)}</td><td className="num">{v.avg_r}</td></tr>)}</tbody></table>
          <h2 style={{ marginTop: 14 }}>Crash windows</h2>
          <table><tbody>{Object.entries(res.crash_periods).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{pct(v.strategy_return)}</td><td className="num">{v.n_trades} trades</td></tr>)}</tbody></table>
          {res.walk_forward && (<><h2 style={{ marginTop: 14 }}>Walk-forward (out of sample)</h2>
            <p className="small">{res.walk_forward.oos_trades} OOS trades · avg R {res.walk_forward.oos_avg_r} · win {pct(res.walk_forward.oos_win_rate, 0)} · net {inr(res.walk_forward.oos_net_pnl)}</p>
            <table><thead><tr><th>Test window</th><th>Params</th><th className="num">Trades</th><th className="num">Avg R</th></tr></thead>
              <tbody>{res.walk_forward.folds.map((f: any, i: number) => <tr key={i}><td className="small">{f.test.join(" → ")}</td><td className="mono">{JSON.stringify(f.chosen_params)}</td><td className="num">{f.test_metrics.n_trades}</td><td className="num">{f.test_metrics.avg_r_multiple}</td></tr>)}</tbody></table></>)}
        </div>
      </div>)}
      {cal && (<div className="card"><h2>Judge calibration & weight comparison (train {cal.train.join("→")}, test {cal.test.join("→")})</h2>
        <table><thead><tr><th>Weights</th><th className="num">Train trades</th><th className="num">Train avg R</th><th className="num">Test trades</th><th className="num">Test avg R</th><th className="num">Test hit</th><th className="num">Brier (test)</th><th className="num">Brier baseline</th></tr></thead>
          <tbody>{Object.entries(cal.weight_sets).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{v.train_trades}</td><td className="num">{fmt(v.train_avg_r, 3)}</td><td className="num">{v.test_trades}</td><td className="num">{fmt(v.test_avg_r, 3)}</td><td className="num">{pct(v.test_hit_rate, 0)}</td><td className="num">{fmt(v.brier_test, 4)}</td><td className="num">{fmt(v.brier_baseline_constant, 4)}</td></tr>)}</tbody></table>
        {cal.weight_sets.configured?.reliability_test && (<><h3 style={{ marginTop: 10 }}>Reliability (test period, configured weights)</h3>
          <table><thead><tr><th>Bin</th><th className="num">n</th><th className="num">Mean predicted</th><th className="num">Observed</th></tr></thead>
            <tbody>{cal.weight_sets.configured.reliability_test.map((r: any) => <tr key={r.bin}><td>{r.bin}</td><td className="num">{r.n}</td><td className="num">{pct(r.mean_predicted, 0)}</td><td className="num">{pct(r.observed_rate, 0)}</td></tr>)}</tbody></table></>)}
        <p className="small muted">{cal.note}</p>
      </div>)}
    </div>
  );
}

export function Data() {
  const inv = useLoad<any>("/api/data/inventory");
  const [msg, setMsg] = useState("");
  const [path, setPath] = useState("");
  const [sym, setSym] = useState("NIFTY 50");
  async function ingest(source: string) {
    setMsg("Loading…");
    try { const r = await api("/api/data/ingest", { source, symbol: sym, path: path || null }); setMsg(`Loaded ${r.dataset.n_bars} bars (${r.dataset.first_bar} → ${r.dataset.last_bar}); quality ${r.quality.status}`); inv.reload(); }
    catch (e: any) { setMsg(e.message); }
  }
  async function news() { try { const r = await api("/api/news/refresh", {}); setMsg(`News: ${r.fetched} fetched, ${r.stored_new} new. Errors: ${r.errors.join("; ") || "none"}`); } catch (e: any) { setMsg(e.message); } }
  return (
    <div className="grid">
      <div className="card"><h2>Load data</h2>
        <div className="row"><input value={sym} onChange={e => setSym(e.target.value)} />
          <button className="btn secondary" onClick={() => ingest("yahoo")}>Yahoo (unofficial, personal use)</button>
          <input placeholder="file in data/import/ e.g. nifty50.csv" value={path} onChange={e => setPath(e.target.value)} />
          <button className="btn secondary" onClick={() => ingest("csv")}>Import CSV</button>
          <button className="btn secondary" onClick={() => ingest("synthetic")}>Load SIMULATED demo series</button>
          <button className="btn secondary" onClick={news}>Refresh official news (RBI/SEBI/PIB)</button></div>
        {msg && <p className="small">{msg}</p>}
      </div>
      <div className="card"><h2>Loaded datasets</h2>
        {inv.data?.loaded?.map((d: any) => (<details key={d.symbol + d.timeframe} open>
          <summary><b>{d.symbol}</b> {d.timeframe} · {d.first_bar} → {d.last_bar} · {d.n_bars} bars · <span className={`pill ${d.quality_status === "PASS" ? "good" : d.quality_status === "FAIL" ? "bad" : "warn"}`}>{d.quality_status}</span> <DataBadge label={d.data_label} /></summary>
          <dl className="kv small" style={{ marginTop: 6 }}><dt>Source</dt><dd>{d.source}</dd><dt>Licence</dt><dd>{d.licence}</dd><dt>Retrieved</dt><dd>{d.retrieved_at}</dd><dt>Last success</dt><dd>{d.last_success}</dd><dt>Adjustment</dt><dd>{d.adjustment_note}</dd></dl>
          <ul className="tight small">{d.quality_json?.issues?.map((i: string, k: number) => <li key={k}>{i}</li>)}</ul>
          {d.quality_json?.gaps?.length > 0 && <table><thead><tr><th>Gap from</th><th>to</th><th className="num">days</th></tr></thead><tbody>{d.quality_json.gaps.map((g: any, k: number) => <tr key={k}><td>{g.from}</td><td>{g.to}</td><td className="num">{g.missing_days}</td></tr>)}</tbody></table>}
        </details>)) ?? <p className="muted">None</p>}
      </div>
      <div className="card"><h2>Source inventory</h2>
        <table><thead><tr><th>Dataset</th><th>Provider</th><th>Claimed coverage</th><th>Licence</th><th>Status</th></tr></thead>
          <tbody>{inv.data?.sources?.map((s: any) => <tr key={s.id}><td>{s.dataset}</td><td>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.provider}</a> : s.provider}{s.official && <span className="tag" style={{ marginLeft: 4 }}>official</span>}</td><td className="small">{s.claimed_coverage}</td><td className="small">{s.licence}</td><td><span className={`pill ${s.status === "implemented" ? "good" : s.status === "manual_import" ? "" : "warn"}`}>{s.status}</span></td></tr>)}</tbody></table>
      </div>
    </div>
  );
}

export function Orders({ tick }: { tick: number }) {
  const o = useLoad<any>("/api/orders", [tick]);
  const a = useLoad<any>("/api/audit?limit=100", [tick]);
  const r = useLoad<any[]>("/api/risk/events?limit=50", [tick]);
  return (
    <div className="grid">
      <div className="card"><h2>Order tickets</h2>
        <table><thead><tr><th>Created</th><th>Mode</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th>Status</th><th>Broker id</th></tr></thead>
          <tbody>{o.data?.tickets?.map((t: any) => <tr key={t.id}><td className="small">{String(t.created_at).slice(0, 19)}</td><td>{t.mode}</td><td>{t.ticket_json.instrument}</td><td>{t.ticket_json.side}</td><td className="num">{t.ticket_json.quantity}</td><td>{t.status}</td><td className="mono">{t.broker_order_id ?? "—"}</td></tr>)}</tbody></table>
      </div>
      <div className="grid cols-2">
        <div className="card"><h2>Paper positions</h2>
          <table><tbody>{o.data?.paper_positions?.map((p: any) => <tr key={p.symbol}><td>{p.symbol}</td><td className="num">{p.qty}</td><td className="num">@ {fmt(p.avg_price)}</td></tr>)}</tbody></table>
          <h2 style={{ marginTop: 12 }}>Realised (paper)</h2>
          <table><tbody>{o.data?.paper_realised?.map((p: any, i: number) => <tr key={i}><td>{p.symbol}</td><td className="num">{p.qty}</td><td className="num">{fmt(p.entry)} → {fmt(p.exit)}</td><td className={`num ${p.pnl >= 0 ? "up" : "down"}`}>{inr(p.pnl)}</td></tr>)}</tbody></table>
        </div>
        <div className="card"><h2>Risk rejections</h2>
          <table><tbody>{r.data?.filter(x => !x.approved).slice(0, 20).map(x => <tr key={x.id}><td className="small">{String(x.ts).slice(0, 19)}</td><td className="small">{x.checks.filter((c: any) => !c.passed).map((c: any) => c.name).join(", ")}</td></tr>)}</tbody></table>
        </div>
      </div>
      <div className="card"><h2>Audit log {a.data && <span className={`pill ${a.data.chain_valid ? "good" : "bad"}`}>hash chain {a.data.chain_valid ? "valid" : "BROKEN"} ({a.data.entries_checked})</span>}</h2>
        <table><tbody>{a.data?.entries?.map((e: any) => <tr key={e.id}><td className="small">{String(e.ts).slice(0, 19)}</td><td>{e.actor}</td><td>{e.event}</td><td className="mono">{JSON.stringify(e.payload).slice(0, 140)}</td></tr>)}</tbody></table>
      </div>
    </div>
  );
}
