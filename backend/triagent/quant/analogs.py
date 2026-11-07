"""Historical analog search: how did situations resembling today's setup play out?

Point-in-time: only analog dates whose full forward window ended BEFORE the
decision bar are used, and feature standardisation uses only past data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ANALOG_FEATURES = ["roc_20", "roc_63", "rsi_14", "dist_sma50_atr", "adx", "hv_20"]


def _feature_frame(f: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=f.index)
    x["roc_20"] = f["roc_20"]
    x["roc_63"] = f["roc_63"]
    x["rsi_14"] = f["rsi_14"]
    x["dist_sma50_atr"] = (f["close"] - f["sma_50"]) / f["atr_14"]
    x["adx"] = f["adx"]
    x["hv_20"] = f["hv_20"]
    return x


def find_analogs(f: pd.DataFrame, t_pos: int, hold: int, k: int = 50, stop_atr: float = 2.0,
                 target_r: float = 2.0) -> dict:
    x = _feature_frame(f)
    if t_pos < 300:
        return {"available": False, "reason": "Insufficient history for analog search (<300 bars)."}
    cur = x.iloc[t_pos]
    if cur.isna().any():
        return {"available": False, "reason": "Current features incomplete."}
    last_usable = t_pos - hold - 1          # analog outcome must be fully known at t
    past = x.iloc[:last_usable + 1].dropna()
    if len(past) < k * 2:
        return {"available": False, "reason": "Too few completed historical analogs."}
    mu, sd = past.mean(), past.std(ddof=0).replace(0, 1)
    z = (past - mu) / sd
    zc = (cur - mu) / sd
    dist = np.sqrt(((z - zc) ** 2).sum(axis=1))
    nearest = dist.nsmallest(k).index
    close, high, low, atr = f["close"], f["high"], f["low"], f["atr_14"]
    pos_of = {ts: i for i, ts in enumerate(f.index)}
    fwd, long_wins, short_wins = [], 0, 0
    for ts in nearest:
        i = pos_of[ts]
        c0, a = close.iloc[i], atr.iloc[i]
        fwd.append(close.iloc[i + hold] / c0 - 1)
        long_wins += _first_hit(high.iloc[i + 1:i + hold + 1], low.iloc[i + 1:i + hold + 1],
                                c0 + target_r * stop_atr * a, c0 - stop_atr * a, long=True)
        short_wins += _first_hit(high.iloc[i + 1:i + hold + 1], low.iloc[i + 1:i + hold + 1],
                                 c0 - target_r * stop_atr * a, c0 + stop_atr * a, long=False)
    fwd = np.array(fwd)
    return {
        "available": True,
        "n": int(len(fwd)),
        "hold_bars": hold,
        "mean_forward_return": round(float(fwd.mean()), 4),
        "median_forward_return": round(float(np.median(fwd)), 4),
        "p10": round(float(np.percentile(fwd, 10)), 4),
        "p90": round(float(np.percentile(fwd, 90)), 4),
        "pct_positive": round(float((fwd > 0).mean()), 3),
        "long_plan_target_first": round(long_wins / len(fwd), 3),
        "short_plan_target_first": round(short_wins / len(fwd), 3),
        "earliest_analog": str(min(nearest).date()),
        "latest_analog": str(max(nearest).date()),
        "note": "Analogs overlap in time and are not independent samples; treat as descriptive, not predictive.",
    }


def _first_hit(highs: pd.Series, lows: pd.Series, target: float, stop: float, long: bool) -> int:
    for hi, lo in zip(highs.to_numpy(), lows.to_numpy()):
        if long:
            if lo <= stop:
                return 0
            if hi >= target:
                return 1
        else:
            if hi >= stop:
                return 0
            if lo <= target:
                return 1
    return 0
