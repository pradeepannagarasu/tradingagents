from datetime import date

import numpy as np
import pandas as pd
import pytest

from triagent.core.models import Direction, Horizon, Instrument, InstrumentType
from triagent.quant import metrics
from triagent.quant.backtest import BacktestConfig, run_backtest, walk_forward
from triagent.quant.indicators import compute_all
from triagent.quant.strategies import StrategySpec

EQ = Instrument(symbol="ABC", instrument_type=InstrumentType.EQUITY)
FUT = Instrument(symbol="NIFTY-FUT", instrument_type=InstrumentType.FUTURE, lot_size=50)


def test_delivery_cost_hand_calculation(profile):
    cb = profile.round_trip_costs(EQ, Horizon.SWING, 1000, 1100, 100, date(2025, 1, 15))
    turnover = 210_000
    assert cb.brokerage == 0
    assert cb.transaction_tax == pytest.approx(turnover * 0.001)
    assert cb.exchange_charges == pytest.approx(turnover * 0.0000297)
    assert cb.stamp_duty == pytest.approx(100_000 * 0.00015)
    assert cb.regulator_fee == pytest.approx(turnover * 1e-6)
    assert cb.gst == pytest.approx((cb.exchange_charges + cb.regulator_fee) * 0.18)
    assert cb.other == pytest.approx(15.93)


def test_futures_stt_is_effective_dated(profile):
    def stt(d):
        return profile.round_trip_costs(FUT, Horizon.SWING, 20000, 20000, 50, d).transaction_tax
    sell_val = 20000 * 50
    assert stt(date(2024, 9, 30)) == pytest.approx(sell_val * 0.000125)
    assert stt(date(2024, 10, 1)) == pytest.approx(sell_val * 0.0002)
    assert stt(date(2026, 4, 1)) == pytest.approx(sell_val * 0.0005)


def test_short_cash_equity_overnight_not_allowed(profile):
    ok, _ = profile.can_hold_short(EQ, Horizon.SWING)
    assert not ok
    assert profile.can_hold_short(EQ, Horizon.INTRADAY)[0]
    assert profile.can_hold_short(FUT, Horizon.SWING)[0]


def _frame(rows):
    idx = pd.bdate_range("2024-01-01", periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1_000_000
    f = compute_all(df)
    f["atr_14"] = 10.0
    return f


class _OneSignal(StrategySpec):
    def __init__(self, at, direction=1, **kw):
        super().__init__("trend_pullback", **kw)
        self.at, self.dir = at, direction

    def signals(self, f):
        s = pd.Series(0, index=f.index)
        s.iloc[self.at] = self.dir
        return s


def test_entry_at_next_open_and_stop_has_priority(profile):
    rows = [[100, 101, 99, 100]] * 5 + [[105, 140, 70, 100]] + [[100, 101, 99, 100]] * 4
    f = _frame(rows)
    spec = _OneSignal(at=4, stop_atr=2.0, target_r=1.0, max_hold_bars=5)
    res = run_backtest(f, spec, EQ, Horizon.SWING, profile, BacktestConfig(slippage_bps=0))
    t = res.trades[0]
    assert t["entry"] == pytest.approx(105.0)          # next bar's open, not signal bar close
    assert t["entry_date"] == str(f.index[5].date())
    assert t["exit_reason"] == "stop"                   # both touched in one bar → stop assumed
    assert t["exit"] == pytest.approx(85.0)


def test_gap_through_stop_fills_at_open(profile):
    rows = [[100, 101, 99, 100]] * 5 + [[100, 101, 99, 100], [70, 72, 65, 68]] + [[68, 69, 67, 68]] * 3
    f = _frame(rows)
    res = run_backtest(f, _OneSignal(at=4, stop_atr=2.0, target_r=3.0), EQ, Horizon.SWING, profile,
                       BacktestConfig(slippage_bps=0))
    t = res.trades[0]
    assert t["exit_reason"] == "stop_gap" and t["exit"] == pytest.approx(70.0)
    assert t["net_pnl"] < t["gross_pnl"]  # costs charged


def test_costs_and_slippage_reduce_pnl(profile):
    rows = [[100, 101, 99, 100]] * 5 + [[100, 125, 99, 120]] + [[120, 121, 119, 120]] * 4
    f = _frame(rows)
    spec = _OneSignal(at=4, stop_atr=2.0, target_r=1.0)
    no = run_backtest(f, spec, EQ, Horizon.SWING, profile, BacktestConfig(slippage_bps=0)).trades[0]
    yes = run_backtest(f, spec, EQ, Horizon.SWING, profile, BacktestConfig(slippage_bps=20)).trades[0]
    assert yes["entry"] > no["entry"] and yes["net_pnl"] < no["net_pnl"]


def test_short_signals_ignored_when_not_permitted(profile):
    rows = [[100, 101, 99, 100]] * 10
    res = run_backtest(_frame(rows), _OneSignal(at=4, direction=-1), EQ, Horizon.SWING, profile)
    assert res.trades == [] and any("Short signals ignored" in w for w in res.warnings)


def test_position_sizing_respects_risk_and_lots(profile):
    rows = [[100, 101, 99, 100]] * 5 + [[100, 101, 99, 100]] * 5
    f = _frame(rows)
    res = run_backtest(f, _OneSignal(at=4, stop_atr=2.0), FUT, Horizon.SWING, profile,
                       BacktestConfig(initial_capital=1_000_000, risk_per_trade_pct=1.0, slippage_bps=0))
    t = res.trades[0]
    assert t["qty"] % 50 == 0
    assert t["qty"] * 20 <= 10_000 + 1e-6   # risk at stop ≤ 1% of capital


def test_metrics_basics():
    eq = pd.Series([100, 110, 99, 120.0])
    assert metrics.max_drawdown(eq) == pytest.approx(99 / 110 - 1)
    assert metrics.max_consecutive_losses([1, -1, -2, 3, -1]) == 2
    lo, hi = metrics.wilson_interval(5, 10)
    assert lo < 0.5 < hi
    assert metrics.sharpe(pd.Series([0.01] * 10)) == 0.0  # zero variance → undefined → 0


def test_walk_forward_uses_each_test_window_once(profile, series_factory):
    f = compute_all(series_factory(n=1800).bars)
    wf = walk_forward(f, StrategySpec("trend_pullback"), {"stop_atr": [1.5, 2.5]}, EQ, Horizon.SWING, profile,
                      train_years=2, test_years=1)
    tests = [tuple(x["test"]) for x in wf["folds"]]
    assert len(tests) == len(set(tests)) >= 2
    for fold in wf["folds"]:
        assert fold["train"][1] < fold["test"][0]
