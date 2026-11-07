"""Outcome evaluation for signals and agent scorecards.

NO TRADE is evaluated properly: each agent's hypothetical plan is simulated on the
subsequent bars, so a NO TRADE that avoided a stop-out is credited ("avoided loss")
and one that skipped a winner is recorded as a "missed opportunity".
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from triagent.quant.metrics import wilson_interval


def simulate_plan(plan: dict | None, future: pd.DataFrame, max_bars: int) -> dict | None:
    """Simulate a plan dict (entry_low/high, stop_loss, target_1, direction) on bars after the decision.

    Entry assumed at the next bar's open if it lies within/through the entry range; otherwise the plan is
    'not_triggered' when price never trades into the range within 3 bars.
    """
    if not plan or plan.get("stop_loss") is None or plan.get("entry_low") is None or future.empty:
        return None
    d = 1 if plan["direction"] == "LONG" else -1
    lo, hi, stop, t1 = plan["entry_low"], plan["entry_high"], plan["stop_loss"], plan["target_1"]
    entry, start = None, None
    for i in range(min(3, len(future))):
        bar = future.iloc[i]
        if bar["low"] <= hi and bar["high"] >= lo:
            entry = float(min(max(bar["open"], lo), hi))
            start = i
            break
    if entry is None:
        return {"triggered": False, "result": "not_triggered", "r_multiple": 0.0}
    risk = abs(entry - stop)
    mfe = mae = 0.0
    window = future.iloc[start:start + max_bars]
    for _, bar in window.iterrows():
        fav = (bar["high"] - entry) * d if d == 1 else (entry - bar["low"])
        adv = (bar["low"] - entry) if d == 1 else (entry - bar["high"])
        mfe, mae = max(mfe, fav), min(mae, adv)
        hit_stop = bar["low"] <= stop if d == 1 else bar["high"] >= stop
        hit_t1 = (bar["high"] >= t1 if d == 1 else bar["low"] <= t1) if t1 is not None else False
        if hit_stop:
            ex = min(bar["open"], stop) if d == 1 else max(bar["open"], stop)
            return {"triggered": True, "result": "stop", "entry": entry, "exit": float(ex),
                    "r_multiple": round((ex - entry) * d / risk, 3), "mfe": round(mfe, 2), "mae": round(mae, 2)}
        if hit_t1:
            return {"triggered": True, "result": "target_1", "entry": entry, "exit": float(t1),
                    "r_multiple": round((t1 - entry) * d / risk, 3), "mfe": round(mfe, 2), "mae": round(mae, 2)}
    if window.empty:
        return {"triggered": True, "result": "pending", "r_multiple": 0.0}
    ex = float(window["close"].iloc[-1])
    return {"triggered": True, "result": "time_exit" if len(window) >= max_bars else "open",
            "entry": entry, "exit": ex, "r_multiple": round((ex - entry) * d / risk, 3),
            "mfe": round(mfe, 2), "mae": round(mae, 2)}


def evaluate_decision(record: dict, future: pd.DataFrame, max_bars: int) -> dict:
    final = record["final_decision"]
    judge_plan = record["judge"].get("plan")
    bull_plan = record["bull"].get("plan")
    bear_plan = record["bear"].get("plan")
    out = {"decision_id": record["id"], "final_decision": final, "regime": None, "horizon": record["horizon"],
           "symbol": record["instrument"]["symbol"],
           "judge": simulate_plan(judge_plan, future, max_bars) if judge_plan else None,
           "bull": simulate_plan(bull_plan, future, max_bars),
           "bear": simulate_plan(bear_plan, future, max_bars)}
    for k in ("bull", "bear", "judge"):
        if out[k]:
            out[k]["subsequent_return"] = round(float(future["close"].iloc[min(max_bars, len(future)) - 1]
                                                      / future["open"].iloc[0] - 1), 4) if len(future) else None
    if final == "NO TRADE":
        hypo = [x for x in (out["bull"], out["bear"]) if x and x.get("triggered")]
        if any(x["result"] == "stop" for x in hypo) and not any(x["result"] == "target_1" for x in hypo):
            out["no_trade_assessment"] = "avoided_loss"
        elif any(x["result"] == "target_1" for x in hypo):
            out["no_trade_assessment"] = "missed_opportunity"
        else:
            out["no_trade_assessment"] = "neutral"
    return out


def scorecard(outcomes: list[dict], regimes: dict[str, str] | None = None) -> dict:
    """Per-agent statistics on R multiples (expectancy, not just win rate) with sample-size information."""
    agents = {"BULL": "bull", "BEAR": "bear", "JUDGE": "judge"}
    res = {}
    for name, key in agents.items():
        rs = [o[key]["r_multiple"] for o in outcomes if o.get(key) and o[key].get("triggered")
              and o[key]["result"] in ("stop", "target_1", "time_exit")]
        n = len(rs)
        wins = sum(r > 0 for r in rs)
        lo, hi = wilson_interval(wins, n)
        cum = np.cumsum(rs) if rs else np.array([0.0])
        dd = float((cum - np.maximum.accumulate(cum)).min()) if rs else 0.0
        by_h = defaultdict(list)
        by_sym = defaultdict(list)
        for o in outcomes:
            if o.get(key) and o[key].get("triggered") and o[key]["result"] in ("stop", "target_1", "time_exit"):
                by_h[o["horizon"]].append(o[key]["r_multiple"])
                by_sym[o["symbol"]].append(o[key]["r_multiple"])
        res[name] = {
            "n": n, "wins": wins, "losses": n - wins,
            "win_rate": round(wins / n, 3) if n else None, "win_rate_95ci": [round(lo, 3), round(hi, 3)],
            "avg_r": round(float(np.mean(rs)), 3) if n else 0.0,
            "avg_loss_r": round(float(np.mean([r for r in rs if r <= 0])), 3) if any(r <= 0 for r in rs) else None,
            "max_drawdown_r": round(dd, 3),
            "by_horizon": {h: {"n": len(v), "avg_r": round(float(np.mean(v)), 3)} for h, v in by_h.items()},
            "by_instrument": {s: {"n": len(v), "avg_r": round(float(np.mean(v)), 3)} for s, v in by_sym.items()},
            "sample_warning": n < 30,
        }
    nt = [o for o in outcomes if o["final_decision"] == "NO TRADE"]
    res["NO_TRADE"] = {
        "n": len(nt),
        "avoided_loss": sum(o.get("no_trade_assessment") == "avoided_loss" for o in nt),
        "missed_opportunity": sum(o.get("no_trade_assessment") == "missed_opportunity" for o in nt),
        "neutral": sum(o.get("no_trade_assessment") == "neutral" for o in nt),
    }
    return res
