import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { Research } from "./pages/Research";
import { Backtest, Dashboard, Data, History, Orders } from "./pages/Pages";
import "./styles.css";

const PAGES = ["Dashboard", "Research", "History", "Backtest", "Data", "Orders & Audit"] as const;
type Page = typeof PAGES[number];

function App() {
  const [page, setPage] = useState<Page>("Dashboard");
  const [openId, setOpenId] = useState<string | undefined>();
  const [sys, setSys] = useState<any>(null);
  const [tick, setTick] = useState(0);
  const [theme, setTheme] = useState<string>(() => { try { return localStorage.getItem("theme") ?? "auto"; } catch { return "auto"; } });

  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);
  const loadSys = () => api("/api/system").then(setSys).catch(() => setSys(null));
  useEffect(() => { loadSys(); }, [tick]);
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws/updates`);
      ws.onmessage = () => setTick(t => t + 1);
    } catch { /* live updates optional */ }
    return () => ws?.close();
  }, []);

  const open = (id: string) => { setOpenId(id); setPage("Research"); };
  async function emergency(path: string, label: string) {
    if (!confirm(`${label}?`)) return;
    await api(`/api/emergency/${path}`, { reason: "dashboard" }); setTick(t => t + 1);
  }
  return (
    <div className="app">
      <nav className="nav">
        <h1>TRI-AGENT · NSE</h1>
        {PAGES.map(p => <button key={p} className={page === p ? "active" : ""} onClick={() => { setPage(p); if (p !== "Research") setOpenId(undefined); }}>{p}</button>)}
        <div className="spacer" />
        <select value={theme} onChange={e => setTheme(e.target.value)} aria-label="Theme"><option value="auto">Theme: system</option><option value="light">Light</option><option value="dark">Dark</option></select>
      </nav>
      <main>
        <div className="banner">
          {sys ? (<>
            <span className={`pill ${sys.mode === "LIVE" ? "bad" : "good"}`}>MODE: {sys.mode}</span>
            <span className={`pill ${sys.live_trading_enabled && !sys.live_execution_disabled_flag ? "bad" : ""}`}>LIVE execution: {sys.live_trading_enabled && !sys.live_execution_disabled_flag ? "ENABLED" : "DISABLED"}</span>
            {sys.trading_halted && <span className="pill bad">TRADING HALTED</span>}
            <span className="pill">LLM: {sys.llm_provider}</span>
            <span className="pill">{sys.calibrated ? "judge calibrated" : "judge uncalibrated"}</span>
            <span style={{ flex: 1 }} />
            <button className="btn danger" onClick={() => emergency("stop-all", "STOP ALL TRADING and cancel open orders")}>STOP ALL TRADING</button>
            <button className="btn secondary" onClick={() => emergency("cancel-all", "Cancel all open orders")}>Cancel open orders</button>
            <button className="btn secondary" onClick={() => emergency("disable-live", "Disable live execution")}>Disable live</button>
            {sys.trading_halted && <button className="btn secondary" onClick={() => emergency("resume", "Resume trading")}>Resume</button>}
          </>) : <span className="pill bad">API unreachable</span>}
        </div>
        {page === "Dashboard" && <Dashboard open={open} tick={tick} />}
        {page === "Research" && <Research key={openId ?? "new"} initialId={openId} />}
        {page === "History" && <History open={open} />}
        {page === "Backtest" && <Backtest />}
        {page === "Data" && <Data />}
        {page === "Orders & Audit" && <Orders tick={tick} />}
        <p className="small muted" style={{ marginTop: 20 }}>Research and decision-support only. Not investment advice. No outcome is guaranteed.</p>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
