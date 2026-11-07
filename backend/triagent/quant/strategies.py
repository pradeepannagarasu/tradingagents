"""Configurable rule-based strategies.

Each strategy maps a feature frame (from ``indicators.compute_all``) to an entry
signal series: +1 (long), -1 (short), 0 (none), evaluated at the bar CLOSE.
Execution happens at the NEXT bar's open in the backtester (no look-ahead).
No single indicator is assumed to be predictive — combinations are tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


def _sig(long_cond: pd.Series, short_cond: pd.Series) -> pd.Series:
    s = pd.Series(0, index=long_cond.index, dtype=int)
    s[long_cond.fillna(False).astype(bool)] = 1
    s[short_cond.fillna(False).astype(bool)] = -1
    return s


def trend_pullback(f: pd.DataFrame, rsi_low: float = 40, rsi_high: float = 60) -> pd.Series:
    up = (f["close"] > f["sma_200"]) & (f["sma_50"] > f["sma_200"])
    dn = (f["close"] < f["sma_200"]) & (f["sma_50"] < f["sma_200"])
    return _sig(up & (f["rsi_14"] < rsi_low), dn & (f["rsi_14"] > rsi_high))


def volume_breakout(f: pd.DataFrame) -> pd.Series:
    return _sig(f["vol_breakout"] & (f["close"] > f["sma_50"]), f["vol_breakdown"] & (f["close"] < f["sma_50"]))


def macd_trend(f: pd.DataFrame, adx_min: float = 20) -> pd.Series:
    cross_up = (f["macd"] > f["signal"]) & (f["macd"].shift(1) <= f["signal"].shift(1))
    cross_dn = (f["macd"] < f["signal"]) & (f["macd"].shift(1) >= f["signal"].shift(1))
    strong = f["adx"] >= adx_min
    return _sig(cross_up & strong & (f["close"] > f["sma_50"]), cross_dn & strong & (f["close"] < f["sma_50"]))


def bollinger_reversion(f: pd.DataFrame) -> pd.Series:
    return _sig((f["close"] < f["bb_lower"]) & (f["close"] > f["sma_200"]),
                (f["close"] > f["bb_upper"]) & (f["close"] < f["sma_200"]))


STRATEGIES: dict[str, Callable[..., pd.Series]] = {
    "trend_pullback": trend_pullback,
    "volume_breakout": volume_breakout,
    "macd_trend": macd_trend,
    "bollinger_reversion": bollinger_reversion,
}


def combination(f: pd.DataFrame, members: list[str], min_agree: int = 2) -> pd.Series:
    """Signal only when at least `min_agree` member strategies fire the same direction within 3 bars."""
    sigs = pd.DataFrame({m: STRATEGIES[m](f) for m in members})
    recent_long = (sigs == 1).rolling(3, min_periods=1).max().sum(axis=1)
    recent_short = (sigs == -1).rolling(3, min_periods=1).max().sum(axis=1)
    return _sig(recent_long >= min_agree, recent_short >= min_agree)


@dataclass
class StrategySpec:
    name: str
    params: dict = field(default_factory=dict)
    stop_atr: float = 2.0
    target_r: float = 2.0
    max_hold_bars: int = 15

    def signals(self, f: pd.DataFrame) -> pd.Series:
        if self.name == "combination":
            return combination(f, **self.params)
        return STRATEGIES[self.name](f, **self.params)
