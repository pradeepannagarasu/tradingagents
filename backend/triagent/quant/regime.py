"""Trend, volatility regime, and support/resistance detection (deterministic, causal)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def trend_label(row: pd.Series) -> str:
    """Classify trend from moving-average stack and ADX for a single feature row."""
    c, s50, s200, adx_v = row.get("close"), row.get("sma_50"), row.get("sma_200"), row.get("adx")
    if any(pd.isna(x) for x in (c, s50, s200)):
        return "UNKNOWN"
    strong = adx_v is not None and not pd.isna(adx_v) and adx_v >= 25
    if c > s50 > s200:
        return "STRONG_UPTREND" if strong else "UPTREND"
    if c < s50 < s200:
        return "STRONG_DOWNTREND" if strong else "DOWNTREND"
    return "SIDEWAYS"


def trend_series(features: pd.DataFrame) -> pd.Series:
    return features.apply(trend_label, axis=1)


def volatility_regime(features: pd.DataFrame, window: int = 756) -> pd.Series:
    """LOW / NORMAL / HIGH by percentile of 20d HV within its own trailing ~3y window (causal)."""
    hv = features["hv_20"]
    pct = hv.rolling(window, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else np.nan,
                                                    raw=True)
    out = pd.Series("UNKNOWN", index=features.index, dtype=object)
    out[pct < 0.25] = "LOW_VOL"
    out[(pct >= 0.25) & (pct <= 0.75)] = "NORMAL_VOL"
    out[pct > 0.75] = "HIGH_VOL"
    return out


def market_regime(features: pd.DataFrame) -> pd.Series:
    """Combined regime used for slicing backtests: BULL / BEAR / SIDEWAYS (+ CRASH override)."""
    trend = trend_series(features)
    regime = pd.Series("SIDEWAYS", index=features.index, dtype=object)
    regime[trend.str.contains("UPTREND")] = "BULL"
    regime[trend.str.contains("DOWNTREND")] = "BEAR"
    regime[trend == "UNKNOWN"] = "UNKNOWN"
    # crash: drawdown from 252d high worse than -20% (causal)
    roll_max = features["close"].rolling(252, min_periods=20).max()
    dd = features["close"] / roll_max - 1
    regime[dd <= -0.20] = "CRASH"
    return regime


@dataclass
class Level:
    price: float
    kind: str          # SUPPORT | RESISTANCE
    touches: int
    last_touch: str


def support_resistance(df: pd.DataFrame, lookback: int = 250, window: int = 5, tolerance: float = 0.015,
                       max_levels: int = 4) -> list[Level]:
    """Swing-pivot clustering on the last `lookback` bars.

    A pivot high/low at i requires bars i-window..i+window, so the most recent
    `window` bars cannot form pivots — this keeps the detector causal when called
    with data up to the decision time.
    """
    d = df.tail(lookback)
    if len(d) < 2 * window + 1:
        return []
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()
    idx = d.index
    pivots: list[tuple[float, str]] = []
    for i in range(window, len(d) - window):
        if highs[i] == highs[i - window:i + window + 1].max():
            pivots.append((highs[i], str(idx[i])))
        if lows[i] == lows[i - window:i + window + 1].min():
            pivots.append((lows[i], str(idx[i])))
    pivots.sort()
    clusters: list[list[tuple[float, str]]] = []
    for p in pivots:
        if clusters and abs(p[0] / np.mean([x[0] for x in clusters[-1]]) - 1) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(d["close"].iloc[-1])
    levels = []
    for cl in clusters:
        price = float(np.mean([x[0] for x in cl]))
        kind = "SUPPORT" if price < last else "RESISTANCE"
        levels.append(Level(price=round(price, 2), kind=kind, touches=len(cl), last_touch=max(x[1] for x in cl)))
    supports = sorted([lv for lv in levels if lv.kind == "SUPPORT"], key=lambda lv: -lv.price)[:max_levels]
    resist = sorted([lv for lv in levels if lv.kind == "RESISTANCE"], key=lambda lv: lv.price)[:max_levels]
    return supports + resist
