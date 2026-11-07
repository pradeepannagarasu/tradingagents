"""End-to-end orchestration: data → snapshot → BULL → BEAR → debate → JUDGE → risk → immutable record."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from triagent.agents.calibration import IsotonicCalibrator, brier, reliability_table
from triagent.agents.debate import run_debate
from triagent.agents.evidence import extract_all
from triagent.agents.judge import judge
from triagent.agents.llm import get_provider
from triagent.agents.researchers import run_bear, run_bull
from triagent.agents.snapshot import ContextBlock, build_snapshot
from triagent.config import Settings
from triagent.core.models import (HORIZON_SPEC, Decision, DecisionRecord, Horizon, Instrument, InstrumentType,
                                  SourceRef)
from triagent.data.connectors.base import PriceSeries
from triagent.data.quality import check_price_series
from triagent.data.store import Store
from triagent.execution.orders import OrderManager
from triagent.execution.paper_broker import PaperBroker
from triagent.history.outcomes import evaluate_decision, scorecard, simulate_plan
from triagent.markets import get_profile
from triagent.news.service import NewsItem
from triagent.notify.notifier import Notifier
from triagent.quant.indicators import compute_all
from triagent.quant.regime import market_regime, volatility_regime
from triagent.risk.engine import PortfolioState, RiskEngine, RiskLimits, TradeContext
from triagent.risk.kill_switch import EmergencyControls

INDEX_SYMBOLS = {"NIFTY 50", "NIFTY", "INDIA VIX", "NIFTY BANK"}


@dataclass
class FeatureCache:
    features: pd.DataFrame
    vol_regime: pd.Series
    regime: pd.Series
    series: PriceSeries


@dataclass
class Services:
    settings: Settings
    store: Store
    profile: object
    controls: EmergencyControls
    risk: RiskEngine
    paper: PaperBroker
    orders: OrderManager
    notifier: Notifier
    news: list[NewsItem] = field(default_factory=list)
    context: dict[str, dict[str, ContextBlock]] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    calibrator: IsotonicCalibrator | None = None
    _cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ factory
    @classmethod
    def create(cls, settings: Settings, store: Store | None = None) -> "Services":
        store = store or Store(settings.database_url)
        profile = get_profile(settings.section("system").get("market", "IN_NSE"))
        controls = EmergencyControls(store)
        limits = RiskLimits.from_config(settings.section("risk"), float(settings.thresholds.get("min_risk_reward", 1.5)))
        risk = RiskEngine(limits, profile, settings.risk_signing_key, kill_switch=controls.trading_halted)
        svc_holder: dict = {}

        def last_price(sym):
            s = svc_holder.get("svc")
            return s.last_price(sym) if s else None

        paper = PaperBroker(store, price_source=last_price, slippage_bps=limits.default_slippage_bps)
        live = None
        if settings.live_trading_enabled:
            from triagent.execution.brokers.zerodha import KiteAdapter
            from triagent.execution.orders import LiveGuard
            live = LiveGuard(KiteAdapter(), settings, controls)
        orders = OrderManager(store, risk, controls, settings, paper, live)
        svc = cls(settings, store, profile, controls, risk, paper, orders, Notifier(settings.section("notifications")))
        svc_holder["svc"] = svc
        return svc

    # ------------------------------------------------------------------ data
    def ingest(self, ps: PriceSeries, instrument_type: InstrumentType = InstrumentType.INDEX) -> dict:
        q = check_price_series(ps, self.profile, self.settings.section("data_quality"), instrument_type)
        self.store.save_series(ps, q.model_dump(mode="json"))
        self._cache.pop(ps.symbol, None)
        self.store.audit("system", "DATA_INGESTED", {**ps.meta(), "quality": q.status.value})
        return q.model_dump(mode="json")

    def features(self, symbol: str) -> FeatureCache:
        version = self.store.dataset_version(symbol)
        cached = self._cache.get(symbol)
        if cached is not None and cached[0] == version:
            return cached[1]
        ps = self.store.load_series(symbol)
        if ps is None:
            raise KeyError(f"No data loaded for {symbol}. Ingest data first.")
        bench = None
        if symbol not in INDEX_SYMBOLS:
            b = self.store.load_series("NIFTY 50")
            bench = b.bars["close"] if b is not None else None
        f = compute_all(ps.bars, bench)
        fc = FeatureCache(f, volatility_regime(f), market_regime(f), ps)
        self._cache[symbol] = (version, fc)  # re-computed when any process re-ingests this dataset
        return fc

    def last_price(self, symbol: str) -> float | None:
        for key in (symbol, "NIFTY 50" if symbol.startswith("NIFTY") else symbol):
            try:
                return float(self.features(key).features["close"].iloc[-1])
            except KeyError:
                continue
        return None

    def resolve_instrument(self, symbol: str) -> tuple[Instrument, Instrument]:
        if symbol.upper() in INDEX_SYMBOLS:
            underlying = Instrument(symbol=symbol.upper(), name=symbol, instrument_type=InstrumentType.INDEX)
            proxies = self.profile.tradable_proxies(symbol)
            fut = next(p for p in proxies if p.instrument_type == InstrumentType.FUTURE)
            return underlying, fut
        inst = Instrument(symbol=symbol.upper(), instrument_type=InstrumentType.EQUITY)
        return inst, inst

    # ------------------------------------------------------------------ research
    def portfolio_state(self) -> PortfolioState:
        cap = float(self.settings.section("risk")["capital"])
        closed = self.paper.realised()
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())
        realised_total = sum(c["pnl"] for c in closed)
        pnl_today = sum(c["pnl"] for c in closed if c["ts"][:10] == str(today))
        pnl_week = sum(c["pnl"] for c in closed if c["ts"][:10] >= str(week_start))
        equity = cap + realised_total
        peak = max([cap] + list(np.cumsum([c["pnl"] for c in closed]) + cap)) if closed else cap
        positions = [{"symbol": p["symbol"], "sector": "INDEX", "direction": "LONG" if p["qty"] > 0 else "SHORT",
                      "value": abs(p["qty"]) * p["avg_price"]} for p in self.paper.positions()]
        return PortfolioState(equity=equity, peak_equity=peak, realised_pnl_today=pnl_today,
                              realised_pnl_week=pnl_week, open_positions=positions)

    def run_research(self, symbol: str, horizon: Horizon, as_of: str | None = None, persist: bool = True,
                     decision_time: datetime | None = None) -> DecisionRecord:
        if horizon == Horizon.INTRADAY:
            raise ValueError("INTRADAY research requires intraday bars from a licensed feed/broker API, "
                             "which are not loaded. Daily-bar signals must not be presented as intraday signals.")
        fc = self.features(symbol)
        underlying, instrument = self.resolve_instrument(symbol)
        if as_of and decision_time is None:
            # Historical research: the decision is taken just after that session's close, not "now".
            sess = self.profile.session(pd.Timestamp(as_of).date())
            decision_time = (sess[1] if sess else pd.Timestamp(as_of).tz_localize("UTC").to_pydatetime()) \
                + timedelta(minutes=1)
        series_pit = fc.series
        if as_of:  # quality must describe the data available at the decision time, not today's file
            series_pit = PriceSeries(fc.series.symbol, fc.series.timeframe, fc.series.bars.loc[:pd.Timestamp(as_of)],
                                     fc.series.source, fc.series.licence, fc.series.retrieved_at, fc.series.adjusted,
                                     fc.series.adjustment_note, fc.series.simulated, fc.series.url)
        quality = check_price_series(series_pit, self.profile, self.settings.section("data_quality"),
                                     underlying.instrument_type, as_of=decision_time)
        src = SourceRef(source=fc.series.source, url=fc.series.url, retrieved_at=fc.series.retrieved_at,
                        licence=fc.series.licence, simulated=fc.series.simulated)
        ctx = self.context.get(symbol, {})
        snap = build_snapshot(fc.features, underlying, instrument, horizon, self.profile, quality, src,
                              as_of=pd.Timestamp(as_of) if as_of else None, news=self.news,
                              events=self._events_in_window(horizon, as_of), regimes=(fc.vol_regime, fc.regime), **ctx)
        llm_cfg = self.settings.section("llm")
        provider = get_provider(llm_cfg.get("provider", "none"))
        evidence = extract_all(snap)
        bull = run_bull(snap, evidence, provider, llm_cfg.get("bull_model", ""))
        min_rr = float(self.settings.thresholds.get("min_risk_reward", 1.5))
        bear = run_bear(snap, bull, evidence, provider, llm_cfg.get("bear_model", ""), min_rr)
        dcfg = self.settings.section("debate")
        summary = run_debate(bull, bear, int(dcfg.get("max_rounds", 2)), int(dcfg.get("max_points_per_round", 3)))
        sc = self.agent_scorecard() if persist else None
        jr = judge(snap, bull, bear, summary, self.settings.weights, self.settings.thresholds, sc, self.calibrator,
                   decision_time=decision_time)
        decision_id = uuid.uuid4().hex[:20]
        risk = None
        final = jr.decision
        if jr.decision != Decision.NO_TRADE:
            vix = (snap.macro.values.get("india_vix") if snap.macro else None)
            ctx_r = TradeContext(decision_id=decision_id, decision=jr.decision, plan=jr.plan, horizon=horizon,
                                 atr=snap.latest.get("atr_14"), avg_daily_value=snap.latest.get("avg_daily_value_20"),
                                 data_quality=quality.status, data_stale=quality.stale, simulated=quality.simulated,
                                 mode=self.settings.mode, india_vix=vix, vol_regime=snap.vol_regime,
                                 high_impact_event_in_window=bool(snap.events), trade_date=decision_time)
            risk = self.risk.assess(ctx_r, self.portfolio_state())
            if persist:
                self.store.log_risk(decision_id, risk.approved, [c.model_dump() for c in risk.checks])
            if not risk.approved:
                final = Decision.NO_TRADE
                jr.reasons.append("Risk engine REJECTED the trade: " + "; ".join(
                    f"{c.name} ({c.detail})" for c in risk.checks if not c.passed) + " → final decision NO TRADE.")
        record = DecisionRecord(
            id=decision_id, created_at=decision_time or datetime.now(timezone.utc), market_data_as_of=snap.as_of,
            instrument=instrument, horizon=horizon, mode=self.settings.mode, bull=bull, bear=bear, judge=jr, risk=risk,
            final_decision=final, data_quality=quality, snapshot_hash=snap.hash(), simulated_data=quality.simulated,
        )
        if persist:
            self.store.save_decision(record)
            self.store.audit("judge", "DECISION_RECORDED", {"decision_id": decision_id, "symbol": symbol,
                                                            "final": final.value, "snapshot_hash": record.snapshot_hash})
            try:
                self.notifier.send_decision(record.model_dump(mode="json"), "http://localhost:5173")
            except Exception as e:  # noqa: BLE001
                self.store.audit("system", "NOTIFY_FAILED", {"error": str(e)})
        return record

    def _events_in_window(self, horizon: Horizon, as_of: str | None) -> list[dict]:
        start = pd.Timestamp(as_of).date() if as_of else datetime.now(timezone.utc).date()
        days = HORIZON_SPEC[horizon]["hold_days"][1] * 7 // 5 + 1
        end = start + timedelta(days=days)
        return [e for e in self.events if start < pd.Timestamp(e["date"]).date() <= end]

    # ------------------------------------------------------------------ evaluation
    def evaluate_outcomes(self, symbol: str) -> list[dict]:
        fc = self.features(symbol)
        res = []
        for d in self.store.list_decisions(1000):
            if d["symbol"] not in (symbol, self.resolve_instrument(symbol)[1].symbol):
                continue
            rec = self.store.get_decision(d["id"])
            as_of = pd.Timestamp(rec["market_data_as_of"]).tz_convert(None).normalize()
            future = fc.features.loc[fc.features.index > as_of]
            hold = HORIZON_SPEC[Horizon(rec["horizon"])]["hold_bars"]
            out = evaluate_decision(rec, future, hold)
            res.append(out)
        return res

    def agent_scorecard(self) -> dict:
        outs = [o["outcome_json"] for o in self.store.outcomes()]
        return scorecard(outs) if outs else {}


def replay(svc: Services, symbol: str, horizon: Horizon, start: str, end: str, step: int = 5,
           weights: dict | None = None) -> list[dict]:
    """Point-in-time historical replay of the full agent pipeline (no persistence, no LLM, no news).

    Returns per-decision rows with the judge composite and the realised outcome of the plan
    the composite pointed to (long if composite ≥ 0 else short) — used for calibration and
    out-of-sample weight evaluation.
    """
    fc = svc.features(symbol)
    idx = fc.features.index
    sel = [i for i in range(len(idx)) if pd.Timestamp(start) <= idx[i] <= pd.Timestamp(end)][::step]
    hold = HORIZON_SPEC[horizon]["hold_bars"]
    underlying, instrument = svc.resolve_instrument(symbol)
    src = SourceRef(source=fc.series.source, simulated=fc.series.simulated)
    rows = []
    for i in sel:
        if i + 1 >= len(idx):
            break
        sub_series = PriceSeries(symbol, "1d", fc.series.bars.iloc[:i + 1], fc.series.source, fc.series.licence,
                                 adjusted=fc.series.adjusted, simulated=fc.series.simulated)
        decision_time = svc.profile.session(idx[i].date())
        dt = decision_time[1] if decision_time else idx[i].to_pydatetime().replace(tzinfo=timezone.utc)
        q = check_price_series(sub_series, svc.profile, svc.settings.section("data_quality"),
                               underlying.instrument_type, as_of=dt + timedelta(minutes=1))
        snap = build_snapshot(fc.features, underlying, instrument, horizon, svc.profile, q, src, as_of=idx[i],
                              regimes=(fc.vol_regime, fc.regime))
        ev = extract_all(snap)
        bull = run_bull(snap, ev)
        bear = run_bear(snap, bull, ev)
        summary = run_debate(bull, bear)
        jr = judge(snap, bull, bear, summary, weights or svc.settings.weights, svc.settings.thresholds,
                   decision_time=dt + timedelta(minutes=1))
        future = fc.features.iloc[i + 1:i + 1 + hold + 3]
        lean_plan = bull.plan if jr.composite_score >= 0 else bear.plan
        if lean_plan is None and jr.composite_score < 0:
            from triagent.agents.plans import build_plan
            from triagent.core.models import Direction
            lean_plan = build_plan(snap, Direction.SHORT)
        sim = simulate_plan(lean_plan.model_dump(mode="json") if lean_plan else None, future, hold)
        rows.append({"date": str(idx[i].date()), "composite": jr.composite_score, "decision": jr.decision.value,
                     "regime": snap.regime, "lean": "LONG" if jr.composite_score >= 0 else "SHORT",
                     "outcome": sim, "hit": int(bool(sim and sim.get("result") == "target_1")),
                     "r": sim.get("r_multiple", 0.0) if sim and sim.get("triggered") else None})
    return rows


def calibrate_and_validate(svc: Services, symbol: str, horizon: Horizon, split_date: str, start: str, end: str,
                           step: int = 5, weight_sets: dict[str, dict] | None = None) -> dict:
    """Fit calibration on [start, split) and evaluate on [split, end]; compare weight sets out of sample."""
    report = {"symbol": symbol, "horizon": horizon.value, "train": [start, split_date], "test": [split_date, end],
              "simulated_data": svc.features(symbol).series.simulated, "weight_sets": {}}
    weight_sets = weight_sets or {"configured": svc.settings.weights}
    for name, w in weight_sets.items():
        rows = replay(svc, symbol, horizon, start, end, step, w)
        train = [r for r in rows if r["date"] < split_date and r["r"] is not None]
        test = [r for r in rows if r["date"] >= split_date and r["r"] is not None]
        signed = lambda r: r["composite"] if r["lean"] == "LONG" else -r["composite"]  # noqa: E731
        cal = IsotonicCalibrator.fit([abs(signed(r)) for r in train], [r["hit"] for r in train],
                                     train_period=f"{start}..{split_date}") if len(train) >= 30 else None
        res = {"n_train": len(train), "n_test": len(test)}
        if cal:
            p = np.array([cal.predict(abs(signed(r))) for r in test])
            y = np.array([r["hit"] for r in test])
            base = np.full_like(p, np.mean([r["hit"] for r in train]))
            res.update({"brier_test": round(brier(p, y), 4), "brier_baseline_constant": round(brier(base, y), 4),
                        "reliability_test": reliability_table(p, y)})
            if name == "configured":
                svc.calibrator = cal
        thr = float(svc.settings.thresholds.get("min_composite_for_trade", 0.25))
        for part, rs in (("train", train), ("test", test)):
            traded = [r for r in rs if abs(r["composite"]) >= thr]
            res[f"{part}_trades"] = len(traded)
            res[f"{part}_avg_r"] = round(float(np.mean([r["r"] for r in traded])), 3) if traded else None
            res[f"{part}_hit_rate"] = round(float(np.mean([r["hit"] for r in traded])), 3) if traded else None
        report["weight_sets"][name] = res
    report["note"] = ("Composite thresholds and weights were NOT tuned on the test period. Analogous replays overlap "
                      "in time (step < hold), so samples are not independent; treat significance with caution.")
    return report
