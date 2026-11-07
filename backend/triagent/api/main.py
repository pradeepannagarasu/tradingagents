"""FastAPI application. OpenAPI docs at /docs."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from triagent.api.security import SecurityMiddleware
from triagent.config import get_settings
from triagent.core.models import HORIZON_SPEC, Horizon, InstrumentType
from triagent.data.connectors.base import data_label
from triagent.data.connectors import ConnectorUnavailable, CsvConnector, SyntheticConnector, YahooChartConnector
from triagent.data.inventory import DATA_SOURCES
from triagent.execution.broker_base import LiveTradingDisabled
from triagent.execution.orders import CONFIRM_PHRASE
from triagent.news.service import NewsService
from triagent.pipeline import Services, calibrate_and_validate
from triagent.quant.backtest import BacktestConfig, run_backtest, walk_forward
from triagent.quant.strategies import STRATEGIES, StrategySpec


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)


hub = Hub()
svc: Services | None = None


def services() -> Services:
    if svc is None:
        raise HTTPException(503, "services not initialised")
    return svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    global svc, _loop
    _loop = asyncio.get_running_loop()
    svc = Services.create(get_settings())
    if os.environ.get("TRIAGENT_DEMO", "").lower() in ("1", "true") and not svc.store.list_datasets():
        svc.ingest(SyntheticConnector().fetch("NIFTY 50"))
    yield


app = FastAPI(title="Tri-Agent Market Intelligence API", version="0.1.0", lifespan=lifespan,
              description="Research & decision-support API. Not investment advice. LIVE execution disabled by default.")
# Explicit origins only. In the Cloudflare deployment the dashboard calls /api same-origin through the Pages
# gateway, so CORS is not needed there; CORS_ORIGINS lists the exact dashboard origin(s) as a backstop.
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
                                  if o.strip() and o.strip() != "*"],
                   allow_methods=["GET", "POST"], allow_headers=["content-type"], allow_credentials=False)
app.add_middleware(SecurityMiddleware)  # outermost: auth + rate limit + headers run before CORS/routing


_loop: asyncio.AbstractEventLoop | None = None


def _emit(msg: dict):
    """Broadcast from sync endpoints (which run in a worker thread) onto the server's event loop."""
    if _loop is not None and hub.clients:
        asyncio.run_coroutine_threadsafe(hub.broadcast(msg), _loop)


# ---------------------------------------------------------------------------- system
@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/system")
def system():
    s = services()
    st = s.settings
    return {
        "mode": st.mode, "live_trading_enabled": st.live_trading_enabled,
        "live_execution_disabled_flag": s.controls.live_disabled(), "trading_halted": s.controls.trading_halted(),
        "market": st.section("system").get("market"), "timezone": st.section("system").get("timezone"),
        "llm_provider": st.section("llm").get("provider"), "weights": st.weights, "thresholds": st.thresholds,
        "risk_limits": st.section("risk"), "credentials_present": st.secrets_present,
        "paper_only_lock": st.paper_only,
        "calibrated": s.calibrator is not None,
    }


# ---------------------------------------------------------------------------- data
@app.get("/api/data/inventory")
def inventory():
    s = services()
    loaded = [{**d, "data_label": data_label(d["source"], d["simulated"])} for d in s.store.list_datasets()]
    return {"sources": DATA_SOURCES, "loaded": loaded}


class IngestRequest(BaseModel):
    source: str = Field(description="synthetic | yahoo | csv")
    symbol: str = "NIFTY 50"
    path: str | None = None
    start: str | None = "2006-01-01"
    instrument_type: InstrumentType = InstrumentType.INDEX
    adjusted: bool = False


@app.post("/api/data/ingest")
def ingest(req: IngestRequest):
    s = services()
    try:
        if req.source == "synthetic":
            ps = SyntheticConnector().fetch(req.symbol)
        elif req.source == "yahoo":
            ps = YahooChartConnector().fetch(req.symbol, start=req.start)
        elif req.source == "csv":
            if not req.path:
                raise HTTPException(400, "path required for csv")
            data_dir = Path(os.environ.get("TRIAGENT_IMPORT_DIR", "data/import")).resolve()
            p = (data_dir / req.path).resolve()
            if data_dir not in p.parents:
                raise HTTPException(400, f"CSV must be inside {data_dir}")
            ps = CsvConnector(p, adjusted=req.adjusted).fetch(req.symbol)
        else:
            raise HTTPException(400, "unknown source")
    except ConnectorUnavailable as e:
        raise HTTPException(502, f"Source unavailable (no data substituted): {e}") from e
    q = s.ingest(ps, req.instrument_type)
    return {"dataset": ps.meta(), "quality": q}


@app.get("/api/data/quality/{symbol}")
def quality(symbol: str):
    for d in services().store.list_datasets():
        if d["symbol"] == symbol:
            return d["quality_json"]
    raise HTTPException(404, "no dataset")


# ---------------------------------------------------------------------------- market
@app.get("/api/market/bars/{symbol}")
def bars(symbol: str, limit: int = 400):
    try:
        fc = services().features(symbol)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    f = fc.features.tail(limit)
    cols = ["open", "high", "low", "close", "volume", "sma_50", "sma_200", "ema_20", "rsi_14", "bb_upper", "bb_lower",
            "atr_14", "adx", "macd", "signal"]
    out = f[cols].astype(float).round(4).astype(object)
    out = out.where(pd.notna(out), None)  # warm-up NaNs → null (NaN is not valid JSON)
    return {"symbol": symbol, "simulated": fc.series.simulated, "source": fc.series.source,
            "data_label": data_label(fc.series.source, fc.series.simulated),
            "bars": [{"time": str(i.date()), **r} for i, r in zip(out.index, out.to_dict("records"))],
            "regime": fc.regime.iloc[-1], "vol_regime": fc.vol_regime.iloc[-1]}


@app.get("/api/market/overview")
def overview():
    s = services()
    out = {"as_of": None, "items": {}, "unavailable": []}
    for sym in ("NIFTY 50", "INDIA VIX"):
        try:
            fc = s.features(sym)
        except KeyError:
            out["unavailable"].append(sym)
            continue
        c = fc.features["close"]
        out["items"][sym] = {"last": round(float(c.iloc[-1]), 2), "change": round(float(c.iloc[-1] - c.iloc[-2]), 2),
                             "change_pct": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4),
                             "as_of": str(c.index[-1].date()), "regime": fc.regime.iloc[-1],
                             "vol_regime": fc.vol_regime.iloc[-1], "simulated": fc.series.simulated,
                             "data_label": data_label(fc.series.source, fc.series.simulated),
                             "source": fc.series.source}
        out["as_of"] = str(c.index[-1].date())
    out["unavailable"] += ["Market breadth (A/D)", "Sector performance", "FII/DII flows",
                           "Option chain / PCR"]  # not connected yet — shown explicitly, never faked
    out["events"] = s.events
    out["news"] = s.store.list_news(20)
    return out


# ---------------------------------------------------------------------------- research
class ResearchRequest(BaseModel):
    symbol: str = "NIFTY 50"
    horizon: Horizon = Horizon.SWING
    as_of: str | None = None


@app.post("/api/research/run")
def research(req: ResearchRequest):
    s = services()
    try:
        rec = s.run_research(req.symbol, req.horizon, req.as_of)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    data = rec.model_dump(mode="json")
    _emit({"type": "decision", "id": rec.id, "decision": rec.final_decision.value, "symbol": req.symbol})
    return data


@app.get("/api/decisions")
def decisions(limit: int = 100):
    return services().store.list_decisions(limit)


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: str):
    d = services().store.get_decision(decision_id)
    if not d:
        raise HTTPException(404, "not found")
    return d


@app.get("/api/horizons")
def horizons():
    return {h.value: {k: v for k, v in spec.items()} for h, spec in HORIZON_SPEC.items()}


# ---------------------------------------------------------------------------- evaluation
@app.post("/api/outcomes/evaluate/{symbol}")
def evaluate(symbol: str):
    s = services()
    outs = s.evaluate_outcomes(symbol)
    for o in outs:
        s.store.save_outcome(o["decision_id"], o)
    return {"evaluated": len(outs), "scorecard": s.agent_scorecard()}


@app.get("/api/scorecard")
def get_scorecard():
    return services().agent_scorecard()


class BacktestRequest(BaseModel):
    symbol: str = "NIFTY 50"
    strategy: str = "trend_pullback"
    params: dict = Field(default_factory=dict)
    stop_atr: float = 2.0
    target_r: float = 2.0
    max_hold_bars: int = 15
    start: str | None = None
    end: str | None = None
    walk_forward: bool = False


@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    s = services()
    if req.strategy not in STRATEGIES and req.strategy != "combination":
        raise HTTPException(400, f"unknown strategy; available: {sorted(STRATEGIES) + ['combination']}")
    fc = s.features(req.symbol)
    _, inst = s.resolve_instrument(req.symbol)
    spec = StrategySpec(req.strategy, req.params, req.stop_atr, req.target_r, req.max_hold_bars)
    rk = s.settings.section("risk")
    cfg = BacktestConfig(initial_capital=rk["capital"], risk_per_trade_pct=rk["max_risk_per_trade_pct"],
                         slippage_bps=rk["default_slippage_bps"])
    res = run_backtest(fc.features, spec, inst, Horizon.SWING, s.profile, cfg, req.start, req.end).to_dict()
    res["simulated_data"] = fc.series.simulated
    res["data_label"] = data_label(fc.series.source, fc.series.simulated)
    if req.walk_forward:
        res["walk_forward"] = walk_forward(fc.features, spec, {"stop_atr": [1.5, 2.0, 3.0], "target_r": [1.5, 2.0, 3.0]},
                                           inst, Horizon.SWING, s.profile, cfg)
    return res


class CalibrationRequest(BaseModel):
    symbol: str = "NIFTY 50"
    horizon: Horizon = Horizon.SWING
    start: str = "2008-01-01"
    split: str = "2018-01-01"
    end: str = "2026-06-30"
    step: int = 10


@app.post("/api/calibration")
def calibration(req: CalibrationRequest):
    s = services()
    alt = {"configured": s.settings.weights,
           "equal": {k: 1 / 7 for k in s.settings.weights},
           "technical_heavy": {**s.settings.weights, "technical": 0.40, "fundamental": 0.05}}
    return calibrate_and_validate(s, req.symbol, req.horizon, req.split, req.start, req.end, req.step, alt)


# ---------------------------------------------------------------------------- orders
class TicketRequest(BaseModel):
    decision_id: str


@app.post("/api/orders/tickets")
def create_ticket(req: TicketRequest):
    s = services()
    rec = s.store.get_decision(req.decision_id)
    if not rec:
        raise HTTPException(404, "decision not found")
    try:
        t = s.orders.create_ticket(rec, s.settings.mode)
    except (ValueError, LiveTradingDisabled) as e:
        raise HTTPException(400, str(e)) from e
    t = dict(t)
    t["ticket_json"] = {k: v for k, v in t["ticket_json"].items() if k != "risk_token"}
    t["confirm_phrase"] = CONFIRM_PHRASE
    return t


class ConfirmRequest(BaseModel):
    confirmation: str
    ticket_hash: str


@app.post("/api/orders/tickets/{ticket_id}/confirm")
def confirm(ticket_id: str, req: ConfirmRequest):
    s = services()
    try:
        t = s.orders.confirm_and_submit(ticket_id, req.confirmation, req.ticket_hash)
    except (PermissionError, LiveTradingDisabled, ValueError) as e:
        raise HTTPException(403, str(e)) from e
    _emit({"type": "order", "ticket_id": ticket_id, "status": t["status"]})
    return _public_ticket(t)


@app.post("/api/orders/tickets/{ticket_id}/reject")
def reject(ticket_id: str):
    return _public_ticket(services().orders.reject_ticket(ticket_id))


@app.post("/api/orders/tickets/{ticket_id}/reconcile")
def reconcile(ticket_id: str):
    return _public_ticket(services().orders.reconcile(ticket_id))


@app.get("/api/orders")
def orders():
    s = services()
    return {"tickets": [_public_ticket(t) for t in s.store.list_tickets()],
            "paper_positions": s.paper.positions(), "paper_realised": s.paper.realised()}


def _public_ticket(t: dict | None):
    if t is None:
        raise HTTPException(404, "ticket not found")
    t = dict(t)
    t["ticket_json"] = {k: v for k, v in (t.get("ticket_json") or {}).items() if k != "risk_token"}
    return t


# ---------------------------------------------------------------------------- emergency
class Reason(BaseModel):
    reason: str = "manual"


@app.post("/api/emergency/stop-all")
def stop_all(r: Reason):
    s = services()
    s.controls.stop_all_trading("user", r.reason)
    res = s.orders.cancel_all("user")
    _emit({"type": "emergency", "action": "STOP_ALL"})
    return {"trading_halted": True, **res}


@app.post("/api/emergency/cancel-all")
def cancel_all(r: Reason):
    return services().orders.cancel_all("user")


@app.post("/api/emergency/disable-live")
def disable_live(r: Reason):
    services().controls.disable_live("user", r.reason)
    return {"live_execution_disabled_flag": True}


@app.post("/api/emergency/resume")
def resume(r: Reason):
    services().controls.resume_trading("user", r.reason)
    return {"trading_halted": False}


# ---------------------------------------------------------------------------- logs
@app.get("/api/audit")
def audit(limit: int = 200):
    s = services()
    ok, n = s.store.verify_audit_chain()
    return {"chain_valid": ok, "entries_checked": n, "entries": s.store.audit_entries(limit)}


@app.get("/api/risk/events")
def risk_events(limit: int = 100):
    return services().store.risk_log(limit)


@app.get("/api/notifications")
def notifications():
    return services().notifier.in_app[::-1]


@app.post("/api/news/refresh")
def news_refresh():
    s = services()
    items, errors = NewsService().fetch_official()
    s.news = items
    n = s.store.save_news([i.to_row() for i in items])
    return {"fetched": len(items), "stored_new": n, "errors": errors}


@app.websocket("/ws/updates")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(websocket)
