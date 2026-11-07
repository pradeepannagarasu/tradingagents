"""Event-driven daily-bar backtester with realistic execution assumptions.

Bias controls
-------------
* Signals are evaluated on bar t close and filled at bar t+1 OPEN (never the signal bar's close).
* Fills are penalised by configurable slippage in the adverse direction.
* Gaps through the stop fill at the open (worse than the stop), not at the stop price.
* If stop and target are both touched within one bar, the STOP is assumed to fill first.
* Market-specific transaction costs (taxes, fees, stamp duty, GST) are charged on every round trip,
  using the rate schedule in force on the exit date.
* Shorts are only taken where the market profile allows holding them for the horizon.
* Position size is risk-based and capped by capital; it never increases after losses (no martingale).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import product

import numpy as np
import pandas as pd

from triagent.core.models import Direction, Horizon, Instrument
from triagent.markets.base import MarketProfile
from triagent.quant.metrics import summarize
from triagent.quant.regime import market_regime
from triagent.quant.strategies import StrategySpec

# Well-known Indian market stress windows (approximate dates, for slicing reports).
CRASH_PERIODS = {
    "GFC_2008": ("2008-01-08", "2009-03-09"),
    "EURO_DEBT_2011": ("2010-11-05", "2011-12-20"),
    "CHINA_DEVAL_2015_16": ("2015-03-04", "2016-02-29"),
    "COVID_2020": ("2020-01-17", "2020-03-24"),
    "CORRECTION_2024_25": ("2024-09-27", "2025-03-04"),
}


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 1.0
    max_capital_per_trade_pct: float = 100.0
    slippage_bps: float = 5.0
    allow_short: bool = True


@dataclass
class BacktestResult:
    strategy: StrategySpec
    trades: list[dict]
    equity: pd.Series
    metrics: dict
    benchmark: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    crash_periods: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _py({
            "strategy": {"name": self.strategy.name, "params": self.strategy.params, "stop_atr": self.strategy.stop_atr,
                         "target_r": self.strategy.target_r, "max_hold_bars": self.strategy.max_hold_bars},
            "metrics": self.metrics,
            "benchmark": self.benchmark,
            "by_regime": self.by_regime,
            "crash_periods": self.crash_periods,
            "warnings": self.warnings,
            "n_trades": len(self.trades),
            "trades": self.trades[-200:],
            "equity_curve": [[str(k.date()) if hasattr(k, "date") else str(k), round(float(v), 2)]
                             for k, v in self.equity.iloc[:: max(1, len(self.equity) // 400)].items()],
        })


def _py(o):
    """Convert numpy scalars/containers to plain Python so results are JSON-serialisable."""
    if isinstance(o, dict):
        return {str(k): _py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_py(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


def run_backtest(features: pd.DataFrame, spec: StrategySpec, instrument: Instrument, horizon: Horizon,
                 profile: MarketProfile, cfg: BacktestConfig | None = None,
                 start: str | None = None, end: str | None = None) -> BacktestResult:
    cfg = cfg or BacktestConfig()
    f = features
    signals = spec.signals(f)
    regimes = market_regime(f)
    if start:
        mask_start = f.index >= pd.Timestamp(start)
    else:
        mask_start = np.ones(len(f), dtype=bool)
    if end:
        mask_end = f.index <= pd.Timestamp(end)
    else:
        mask_end = np.ones(len(f), dtype=bool)
    window = np.where(mask_start & mask_end)[0]
    if len(window) < 3:
        return BacktestResult(spec, [], pd.Series([cfg.initial_capital]), summarize(
            pd.Series([cfg.initial_capital]), [], cfg.initial_capital), warnings=["Window too short"])

    short_ok, short_reason = profile.can_hold_short(instrument, horizon)
    warnings = []
    if not short_ok and cfg.allow_short:
        warnings.append(f"Short signals ignored: {short_reason}")
    allow_short = cfg.allow_short and short_ok

    o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr_v = f["atr_14"].to_numpy()
    sig = signals.to_numpy()
    idx = f.index
    lot = max(1, instrument.lot_size)
    slip = cfg.slippage_bps / 10_000

    cash = cfg.initial_capital
    pos = None
    trades: list[dict] = []
    equity_vals = []
    i0, i1 = int(window[0]), int(window[-1])

    for i in range(i0, i1 + 1):
        # 1) manage open position on bar i
        if pos is not None and i >= pos["entry_i"]:
            d = 1 if pos["dir"] == Direction.LONG else -1
            stop, target = pos["stop"], pos["target"]
            exit_px, reason = None, None
            if d == 1:
                pos["mfe"] = max(pos["mfe"], h[i] - pos["entry"])
                pos["mae"] = min(pos["mae"], l[i] - pos["entry"])
                if o[i] <= stop:
                    exit_px, reason = o[i], "stop_gap"
                elif l[i] <= stop:
                    exit_px, reason = stop, "stop"
                elif o[i] >= target:
                    exit_px, reason = o[i], "target_gap"
                elif h[i] >= target:
                    exit_px, reason = target, "target"
            else:
                pos["mfe"] = max(pos["mfe"], pos["entry"] - l[i])
                pos["mae"] = min(pos["mae"], pos["entry"] - h[i])
                if o[i] >= stop:
                    exit_px, reason = o[i], "stop_gap"
                elif h[i] >= stop:
                    exit_px, reason = stop, "stop"
                elif o[i] <= target:
                    exit_px, reason = o[i], "target_gap"
                elif l[i] <= target:
                    exit_px, reason = target, "target"
            if exit_px is None and (i - pos["entry_i"] + 1 >= spec.max_hold_bars or i == i1):
                exit_px, reason = c[i], "time" if i != i1 else "end_of_window"
            if exit_px is not None:
                fill = exit_px * (1 - slip * d)
                qty = pos["qty"]
                gross = (fill - pos["entry"]) * qty * d
                costs = profile.round_trip_costs(instrument, horizon, pos["entry"], fill, qty, idx[i].date(),
                                                 pos["dir"]).total
                net = gross - costs
                cash += pos["margin"] + net
                trades.append({
                    "entry_date": str(idx[pos["entry_i"]].date()), "exit_date": str(idx[i].date()),
                    "direction": pos["dir"].value, "entry": round(pos["entry"], 2), "exit": round(fill, 2),
                    "qty": qty, "stop": round(stop, 2), "target": round(target, 2),
                    "gross_pnl": round(gross, 2), "costs": round(costs, 2),
                    "slippage": round(pos["slip_cost"] + abs(exit_px - fill) * qty, 2),
                    "net_pnl": round(net, 2), "r_multiple": round(net / pos["risk_amt"], 3) if pos["risk_amt"] else 0.0,
                    "exit_reason": reason, "bars_held": i - pos["entry_i"] + 1,
                    "mfe": round(pos["mfe"] * qty, 2), "mae": round(pos["mae"] * qty, 2),
                    "regime": pos["regime"], "turnover": round((pos["entry"] + fill) * qty, 2),
                })
                pos = None

        # 2) new entry: signal at close of bar i-1, fill at open of bar i
        if pos is None and i > i0 and i < i1:
            s = sig[i - 1]
            if s == -1 and not allow_short:
                s = 0
            a = atr_v[i - 1]
            if s != 0 and not np.isnan(a) and a > 0:
                d = int(s)
                ref = o[i]
                entry = ref * (1 + slip * d)
                stop_dist = spec.stop_atr * a
                stop = entry - d * stop_dist
                target = entry + d * spec.target_r * stop_dist
                equity_now = cash
                risk_amt_budget = equity_now * cfg.risk_per_trade_pct / 100
                qty_risk = risk_amt_budget / stop_dist
                qty_cap = equity_now * cfg.max_capital_per_trade_pct / 100 / entry
                qty = int(math.floor(min(qty_risk, qty_cap) / lot)) * lot
                if qty > 0:
                    margin = entry * qty
                    cash -= margin
                    pos = {"dir": Direction.LONG if d == 1 else Direction.SHORT, "entry": entry, "entry_i": i,
                           "stop": stop, "target": target, "qty": qty, "margin": margin,
                           "risk_amt": stop_dist * qty, "mfe": 0.0, "mae": 0.0,
                           "slip_cost": abs(entry - ref) * qty, "regime": str(regimes.iloc[i - 1])}
                    # The entry bar itself can hit the stop/target after the at-open fill.
                    _manage_entry_bar(pos, i, h, l, slip, instrument, horizon, profile, idx, trades)
                    if pos.get("closed"):
                        cash += pos["margin"] + trades[-1]["net_pnl"]
                        pos = None

        # 3) mark to market
        mtm = cash
        if pos is not None:
            d = 1 if pos["dir"] == Direction.LONG else -1
            mtm += pos["margin"] + (c[i] - pos["entry"]) * pos["qty"] * d
        equity_vals.append(mtm)

    equity = pd.Series(equity_vals, index=idx[i0:i1 + 1])
    metrics = summarize(equity, trades, cfg.initial_capital)
    bench = _benchmark(f["close"].iloc[i0:i1 + 1], cfg.initial_capital)
    res = BacktestResult(spec, trades, equity, metrics, bench, _by_regime(trades), _crash_slices(equity, trades),
                         warnings)
    if metrics["small_sample_warning"]:
        res.warnings.append("Fewer than 30 trades — statistics are not reliable.")
    return res


def _manage_entry_bar(pos, i, h, l, slip, instrument, horizon, profile, idx, trades):
    """Check the entry bar for an intrabar stop/target after an at-open fill (stop has priority)."""
    d = 1 if pos["dir"] == Direction.LONG else -1
    stop, target = pos["stop"], pos["target"]
    hit_stop = (l[i] <= stop) if d == 1 else (h[i] >= stop)
    hit_target = (h[i] >= target) if d == 1 else (l[i] <= target)
    pos["mfe"] = max(0.0, (h[i] - pos["entry"]) if d == 1 else (pos["entry"] - l[i]))
    pos["mae"] = min(0.0, (l[i] - pos["entry"]) if d == 1 else (pos["entry"] - h[i]))
    if not (hit_stop or hit_target):
        return
    exit_px, reason = (stop, "stop") if hit_stop else (target, "target")
    fill = exit_px * (1 - slip * d)
    qty = pos["qty"]
    gross = (fill - pos["entry"]) * qty * d
    costs = profile.round_trip_costs(instrument, horizon, pos["entry"], fill, qty, idx[i].date(), pos["dir"]).total
    net = gross - costs
    trades.append({
        "entry_date": str(idx[i].date()), "exit_date": str(idx[i].date()), "direction": pos["dir"].value,
        "entry": round(pos["entry"], 2), "exit": round(fill, 2), "qty": qty, "stop": round(stop, 2),
        "target": round(target, 2), "gross_pnl": round(gross, 2), "costs": round(costs, 2),
        "slippage": round(pos["slip_cost"] + abs(exit_px - fill) * qty, 2), "net_pnl": round(net, 2),
        "r_multiple": round(net / pos["risk_amt"], 3) if pos["risk_amt"] else 0.0, "exit_reason": reason,
        "bars_held": 1, "mfe": round(pos["mfe"] * qty, 2), "mae": round(pos["mae"] * qty, 2),
        "regime": pos["regime"], "turnover": round((pos["entry"] + fill) * qty, 2),
    })
    pos["closed"] = True


def _benchmark(close: pd.Series, capital: float) -> dict:
    eq = capital * close / close.iloc[0]
    m = summarize(eq, [], capital)
    return {"name": "Buy & hold of the same series (price return; TRI preferred when available)",
            "total_return": m["total_return"], "cagr": m["cagr"], "max_drawdown": m["max_drawdown"],
            "sharpe": m["sharpe"]}


def _by_regime(trades: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for t in trades:
        r = out.setdefault(t["regime"], {"n": 0, "wins": 0, "net_pnl": 0.0, "sum_r": 0.0})
        r["n"] += 1
        r["wins"] += t["net_pnl"] > 0
        r["net_pnl"] += t["net_pnl"]
        r["sum_r"] += t["r_multiple"]
    for r in out.values():
        r["win_rate"] = round(r["wins"] / r["n"], 3)
        r["avg_r"] = round(r.pop("sum_r") / r["n"], 3)
        r["net_pnl"] = round(r["net_pnl"], 2)
    return out


def _crash_slices(equity: pd.Series, trades: list[dict]) -> dict:
    out = {}
    for name, (a, b) in CRASH_PERIODS.items():
        e = equity[(equity.index >= pd.Timestamp(a)) & (equity.index <= pd.Timestamp(b))]
        if len(e) < 2:
            continue
        tr = [t for t in trades if a <= t["entry_date"] <= b]
        out[name] = {"strategy_return": round(float(e.iloc[-1] / e.iloc[0] - 1), 4),
                     "n_trades": len(tr), "net_pnl": round(sum(t["net_pnl"] for t in tr), 2)}
    return out


# --------------------------------------------------------------------------- walk-forward
def walk_forward(features: pd.DataFrame, base: StrategySpec, grid: dict[str, list], instrument: Instrument,
                 horizon: Horizon, profile: MarketProfile, cfg: BacktestConfig | None = None,
                 train_years: int = 4, test_years: int = 1, min_train_trades: int = 10) -> dict:
    """Rolling walk-forward: optimise on train window, evaluate ONCE on the following unseen test window.

    `grid` keys may be strategy params or spec fields (stop_atr, target_r, max_hold_bars).
    Each test window is used exactly once, so there is no repeated optimisation on the same test data.
    """
    cfg = cfg or BacktestConfig()
    idx = features.index
    start = idx[0] + pd.DateOffset(years=1)  # allow indicator warm-up (200d SMA)
    folds = []
    oos_trades: list[dict] = []
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in product(*grid.values())] or [{}]
    t0 = start
    while True:
        train_end = t0 + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if test_end > idx[-1] + pd.DateOffset(days=1):
            break
        best, best_score = None, -np.inf
        for combo in combos:
            spec = _apply(base, combo)
            r = run_backtest(features, spec, instrument, horizon, profile, cfg, str(t0.date()), str(train_end.date()))
            n = r.metrics["n_trades"]
            score = r.metrics["avg_r_multiple"] * math.sqrt(n) if n >= min_train_trades else -np.inf
            if score > best_score:
                best, best_score = combo, score
        if best is None:
            best = combos[0]
        spec = _apply(base, best)
        test = run_backtest(features, spec, instrument, horizon, profile, cfg,
                            str((train_end + pd.DateOffset(days=1)).date()), str(test_end.date()))
        folds.append({"train": [str(t0.date()), str(train_end.date())],
                      "test": [str((train_end + pd.DateOffset(days=1)).date()), str(test_end.date())],
                      "chosen_params": best, "train_score": None if best_score == -np.inf else round(best_score, 3),
                      "test_metrics": {k: test.metrics[k] for k in ("total_return", "n_trades", "win_rate",
                                                                     "avg_r_multiple", "max_drawdown", "sharpe")}})
        oos_trades.extend(test.trades)
        t0 = t0 + pd.DateOffset(years=test_years)
    n = len(oos_trades)
    avg_r = float(np.mean([t["r_multiple"] for t in oos_trades])) if n else 0.0
    return {
        "folds": folds,
        "oos_trades": n,
        "oos_avg_r": round(avg_r, 3),
        "oos_win_rate": round(sum(t["net_pnl"] > 0 for t in oos_trades) / n, 3) if n else 0.0,
        "oos_net_pnl": round(sum(t["net_pnl"] for t in oos_trades), 2),
        "note": "Out-of-sample results concatenate unseen test windows; each window was evaluated once.",
    }


def _apply(base: StrategySpec, combo: dict) -> StrategySpec:
    spec_fields = {"stop_atr", "target_r", "max_hold_bars"}
    params = dict(base.params)
    kw = {"stop_atr": base.stop_atr, "target_r": base.target_r, "max_hold_bars": base.max_hold_bars}
    for k, v in combo.items():
        if k in spec_fields:
            kw[k] = v
        else:
            params[k] = v
    return StrategySpec(base.name, params, **kw)
