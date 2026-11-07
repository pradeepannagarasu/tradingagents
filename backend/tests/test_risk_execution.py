import time

import pytest

from triagent.core.models import Decision, Direction, Horizon, Instrument, InstrumentType, QualityStatus, TradePlan
from triagent.execution.broker_base import BrokerAdapter, LiveTradingDisabled, OrderRequest, OrderStatus, OrderUpdate
from triagent.execution.orders import CONFIRM_PHRASE, LiveGuard, OrderManager
from triagent.execution.paper_broker import PaperBroker
from triagent.risk import engine as risk_mod
from triagent.risk.engine import PortfolioState, RiskEngine, RiskLimits, TradeContext
from triagent.risk.kill_switch import EmergencyControls

LIMITS = dict(capital=1_000_000, max_capital_per_trade_pct=20, max_risk_per_trade_pct=1, max_daily_loss=20_000,
              max_weekly_loss=50_000, max_open_positions=3, max_sector_exposure_pct=40, max_leverage=1,
              max_drawdown_pct=10, min_avg_daily_value_multiple=50, max_spread_bps=15, min_stop_distance_atr=0.5,
              max_stop_distance_atr=4, max_india_vix=30, block_expiry_day_options=True, default_slippage_bps=5)
EQ = Instrument(symbol="ABC", instrument_type=InstrumentType.EQUITY, sector="IT")


def plan(entry=1000.0, stop=960.0, t1=1100.0, direction=Direction.LONG, inst=EQ):
    return TradePlan(instrument=inst, direction=direction, entry_low=entry - 2, entry_high=entry + 2, stop_loss=stop,
                     target_1=t1, target_2=t1 + 50, risk_reward=round(abs(t1 - entry) / abs(entry - stop), 2) if stop else None,
                     invalidation=["close below stop"])


def ctx(p, **kw):
    base = dict(decision_id="d1", decision=Decision.BUY, plan=p, horizon=Horizon.SWING, atr=20.0,
                avg_daily_value=5e9, data_quality=QualityStatus.PASS, data_stale=False, simulated=False, mode="PAPER")
    base.update(kw)
    return TradeContext(**base)


def pf(**kw):
    return PortfolioState(**{"equity": 1_000_000, "peak_equity": 1_000_000, **kw})


@pytest.fixture
def engine(profile):
    state = {"halt": False}
    eng = RiskEngine(RiskLimits(**LIMITS), profile, b"k" * 32, kill_switch=lambda: state["halt"])
    eng._state = state
    return eng


def failed(ra):
    return {c.name for c in ra.checks if not c.passed}


def test_sizing_is_risk_based_and_capped(engine):
    ra = engine.assess(ctx(plan()), pf())
    assert ra.approved, failed(ra)
    # 1% of 10L = 10,000 / 40 per share = 250 shares; capital cap 20% = 2L / 1000 = 200 → 200
    assert ra.quantity == 200
    assert ra.max_loss_at_stop <= 10_000
    assert ra.approval_token


@pytest.mark.parametrize("p,ctx_kw,pf_kw,expected", [
    (plan(stop=None), {}, {}, "invalidation_defined"),
    (plan(stop=1040), {}, {}, "stop_on_correct_side"),
    (plan(stop=995, t1=1010), {}, {}, "stop_distance"),
    (plan(t1=1020), {}, {}, "risk_reward"),
    (plan(), {"data_stale": True}, {}, "data_quality"),
    (plan(), {"mode": "RESEARCH"}, {}, "mode_permits"),
    (plan(), {"mode": "LIVE", "simulated": True}, {}, "no_simulated_data_live"),
    (plan(), {"avg_daily_value": None}, {}, "liquidity"),
    (plan(), {"india_vix": 35.0}, {}, "volatility_vix"),
    (plan(), {}, {"realised_pnl_today": -25_000}, "daily_loss_limit"),
    (plan(), {}, {"realised_pnl_week": -60_000}, "weekly_loss_limit"),
    (plan(), {}, {"equity": 880_000, "peak_equity": 1_000_000}, "drawdown_threshold"),
    (plan(), {}, {"open_positions": [{"symbol": "ABC", "sector": "IT", "value": 1000}]}, "no_averaging_or_pyramiding"),
    (plan(), {}, {"open_positions": [{"symbol": f"S{i}", "sector": "X", "value": 1} for i in range(3)]}, "open_positions"),
    (plan(direction=Direction.SHORT, stop=1040, t1=900), {}, {}, "short_permitted"),
    (plan(), {"mode": "LIVE"}, {}, "bid_ask_spread"),
])
def test_rejections(engine, p, ctx_kw, pf_kw, expected):
    ra = engine.assess(ctx(p, **ctx_kw), pf(**pf_kw))
    assert not ra.approved and expected in failed(ra)
    assert ra.approval_token is None


def test_kill_switch_blocks_everything(engine):
    engine._state["halt"] = True
    ra = engine.assess(ctx(plan()), pf())
    assert not ra.approved and "kill_switch_off" in failed(ra)


def test_unlimited_or_excessive_limits_rejected():
    for bad in ({"max_risk_per_trade_pct": 0}, {"max_risk_per_trade_pct": 10}, {"capital": float("inf")},
                {"max_leverage": 50}, {"max_capital_per_trade_pct": 1000}):
        with pytest.raises(ValueError):
            RiskLimits(**{**LIMITS, **bad}).validate()


def test_futures_lot_too_large_for_budget_is_rejected(engine):
    fut = Instrument(symbol="NIFTY-FUT", instrument_type=InstrumentType.FUTURE, lot_size=65)
    ra = engine.assess(ctx(plan(entry=25000, stop=24600, t1=25900, inst=fut), atr=250), pf())
    assert "position_size_positive" in failed(ra)  # 400 pts × 65 = 26,000 > 10,000 budget


def test_token_binding_and_expiry(engine, monkeypatch):
    ra = engine.assess(ctx(plan()), pf())
    good = dict(decision_id="d1", symbol="ABC", direction="LONG", qty=ra.quantity, stop=960.0)
    assert engine.verify_token(ra.approval_token, **good)[0]
    assert not engine.verify_token(ra.approval_token, **{**good, "qty": ra.quantity * 10})[0]
    body, sig = ra.approval_token.rsplit(".", 1)
    assert not engine.verify_token(body + "." + "0" * 64, **good)[0]
    monkeypatch.setattr(risk_mod.time, "time", lambda: time.monotonic() + 10**10)
    assert not engine.verify_token(ra.approval_token, **good)[0]


# ----------------------------------------------------------------------------- execution
class SpyLiveBroker(BrokerAdapter):
    name = "spy-live"
    is_live = True

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
        self.orders = {}

    def place_order(self, req):
        self.calls.append(req)
        if self.fail:
            self.orders[req.idempotency_key] = OrderUpdate("LIVE-1", OrderStatus.OPEN)
            raise ConnectionError("network dropped after send")
        return OrderUpdate("LIVE-1", OrderStatus.OPEN)

    def order_status(self, oid):
        return OrderUpdate(oid, OrderStatus.OPEN)

    def find_by_idempotency_key(self, key):
        return self.orders.get(key)

    def cancel_order(self, oid):
        return OrderUpdate(oid, OrderStatus.CANCELLED)

    def modify_order(self, oid, price=None, quantity=None):
        return OrderUpdate(oid, OrderStatus.OPEN)

    def open_orders(self):
        return []

    def positions(self):
        return []


class _Settings:
    def __init__(self, live):
        self.live_trading_enabled = live


def make_record(engine, mode="PAPER", simulated=False):
    p = plan()
    ra = engine.assess(ctx(p, decision_id="dec1", mode=mode, bid=999.9, ask=1000.1), pf())
    assert ra.approved
    from datetime import datetime, timezone
    return {"id": "dec1", "final_decision": "BUY / LONG", "market_data_as_of": datetime.now(timezone.utc).isoformat(), "horizon": "SWING", "simulated_data": simulated,
            "judge": {"plan": p.model_dump(mode="json"), "reasons": ["test"]}, "risk": ra.model_dump(mode="json")}


def manager(store, engine, live=None, live_enabled=False):
    controls = EmergencyControls(store)
    guard = LiveGuard(live, _Settings(live_enabled), controls) if live else None
    paper = PaperBroker(store, price_source=lambda s: 1000.0, slippage_bps=0)
    return OrderManager(store, engine, controls, _Settings(live_enabled), paper, guard), controls


def test_paper_flow_requires_explicit_confirmation_and_is_idempotent(store, engine):
    om, _ = manager(store, engine)
    t = om.create_ticket(make_record(engine), "PAPER")
    assert t["status"] == "PENDING_APPROVAL"
    with pytest.raises(PermissionError):
        om.confirm_and_submit(t["id"], "yes", t["ticket_hash"])
    with pytest.raises(PermissionError):
        om.confirm_and_submit(t["id"], CONFIRM_PHRASE, "tampered-hash")
    done = om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert done["status"] == "FILLED"
    again = om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert again["broker_order_id"] == done["broker_order_id"]
    assert len(om.paper.state["orders"]) == 1
    dup = om.create_ticket(make_record(engine), "PAPER")       # same decision → same idempotency key
    assert dup["id"] == t["id"]
    assert store.verify_audit_chain()[0]


def test_no_live_order_possible_while_live_disabled(store, engine):
    spy = SpyLiveBroker()
    om, controls = manager(store, engine, live=spy, live_enabled=False)
    t = om.create_ticket(make_record(engine, mode="LIVE"), "LIVE")
    with pytest.raises(LiveTradingDisabled):
        om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert spy.calls == []
    # enabled by config/env but emergency flag still disabled (default) → still blocked
    om2, _ = manager(store, engine, live=spy, live_enabled=True)
    rec = make_record(engine, mode="LIVE")
    rec["id"] = "dec2"
    rec["risk"] = engine.assess(ctx(plan(), decision_id="dec2", mode="LIVE", bid=999.9, ask=1000.1), pf()).model_dump(mode="json")
    t2 = om2.create_ticket(rec, "LIVE")
    with pytest.raises(LiveTradingDisabled):
        om2.confirm_and_submit(t2["id"], CONFIRM_PHRASE, t2["ticket_hash"])
    assert spy.calls == []


def test_live_orders_blocked_on_simulated_data(store, engine):
    spy = SpyLiveBroker()
    om, controls = manager(store, engine, live=spy, live_enabled=True)
    controls.enable_live("test", "test")
    t = om.create_ticket(make_record(engine, mode="LIVE", simulated=True), "LIVE")
    with pytest.raises(PermissionError):
        om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert spy.calls == []


def test_unknown_outcome_reconciles_instead_of_resubmitting(store, engine):
    spy = SpyLiveBroker(fail=True)
    om, controls = manager(store, engine, live=spy, live_enabled=True)
    controls.enable_live("test", "test")
    t = om.create_ticket(make_record(engine, mode="LIVE"), "LIVE")
    out = om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert len(spy.calls) == 1
    assert out["status"] == "OPEN" and out["broker_order_id"] == "LIVE-1"
    om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    assert len(spy.calls) == 1


def test_kill_switch_blocks_confirmation_and_cancel_all(store, engine):
    om, controls = manager(store, engine)
    t = om.create_ticket(make_record(engine), "PAPER")
    controls.stop_all_trading("test", "drill")
    with pytest.raises(LiveTradingDisabled):
        om.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"])
    res = om.cancel_all()
    assert store.get_ticket(t["id"])["status"] == "CANCELLED"
    assert res["errors"] == []


def test_paper_broker_partial_fill_and_idempotency(store):
    pb = PaperBroker(store, price_source=lambda s: 100.0, slippage_bps=0, partial_fill_ratio=0.5)
    req = OrderRequest("k1", "ABC", "NSE", "BUY", 10, "MARKET")
    u = pb.place_order(req)
    assert u.status == OrderStatus.PARTIALLY_FILLED and u.filled_qty == 5
    assert pb.place_order(req).broker_order_id == u.broker_order_id
    assert pb.cancel_order(u.broker_order_id).status == OrderStatus.CANCELLED
    rej = pb.place_order(OrderRequest("k2", "ABC", "NSE", "BUY", 0))
    assert rej.status == OrderStatus.REJECTED


def test_kite_adapter_param_building_and_auth_handling():
    import httpx

    from triagent.execution.brokers.zerodha import KiteAdapter
    from triagent.execution.broker_base import AuthExpired
    p = KiteAdapter.build_params(OrderRequest("abcdefghijklmnopqrstuvwxyz", "INFY", "NSE", "BUY", 1, "LIMIT", 1500.0,
                                              product="CNC"))
    assert p["tag"] == "abcdefghijklmnopqrst" and p["price"] == 1500.0
    client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})))
    with pytest.raises(AuthExpired):
        KiteAdapter("k", "t", client).positions()
    with pytest.raises(AuthExpired):
        KiteAdapter("", "", client).positions()


def test_stale_decision_cannot_become_order(store, engine):
    om, _ = manager(store, engine)
    rec = make_record(engine)
    rec["market_data_as_of"] = "2019-03-15T10:00:00+00:00"
    with pytest.raises(ValueError, match="stale"):
        om.create_ticket(rec, "PAPER")
