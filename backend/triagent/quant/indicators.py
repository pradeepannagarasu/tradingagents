"""Deterministic technical indicators.

All functions are *causal*: the value at bar t depends only on bars <= t.
This is enforced by tests (appending future bars must not change past values).
Inputs are pandas Series/DataFrames indexed by timestamp with columns
open, high, low, close, volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA seeded with the SMA of the first n values (standard convention)."""
    s = s.astype(float)
    out = pd.Series(np.nan, index=s.index)
    values = s.to_numpy()
    valid = np.where(~np.isnan(values))[0]
    if len(valid) < n:
        return out
    start = valid[0]
    seed_end = start + n
    if np.isnan(values[start:seed_end]).any():
        return s.ewm(span=n, adjust=False, min_periods=n).mean()
    alpha = 2.0 / (n + 1)
    res = np.full(len(values), np.nan)
    res[seed_end - 1] = values[start:seed_end].mean()
    for i in range(seed_end, len(values)):
        v = values[i]
        res[i] = res[i - 1] if np.isnan(v) else alpha * v + (1 - alpha) * res[i - 1]
    return pd.Series(res, index=s.index)


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA) seeded with the simple mean of the first n values."""
    values = s.astype(float).to_numpy()
    res = np.full(len(values), np.nan)
    valid = np.where(~np.isnan(values))[0]
    if len(valid) < n:
        return pd.Series(res, index=s.index)
    start = valid[0]
    seed_end = start + n
    res[seed_end - 1] = np.nanmean(values[start:seed_end])
    for i in range(seed_end, len(values)):
        v = values[i]
        res[i] = res[i - 1] if np.isnan(v) else (res[i - 1] * (n - 1) + v) / n
    return pd.Series(res, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = wilder(gain.iloc[1:], n).reindex(close.index)
    avg_loss = wilder(loss.iloc[1:], n).reindex(close.index)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df)
    atr_n = wilder(tr.iloc[1:], n).reindex(df.index)
    plus_di = 100 * wilder(plus_dm.iloc[1:], n).reindex(df.index) / atr_n
    minus_di = 100 * wilder(minus_dm.iloc[1:], n).reindex(df.index) / atr_n
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    adx_line = wilder(dx, n)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_line})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pct_b": pct_b})


def vwap(df: pd.DataFrame, session_key: pd.Series | None = None) -> pd.Series:
    """Session VWAP (resets each session). For daily bars, pass session_key=None to get a rolling-20 VWAP proxy."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    if session_key is None:
        return pv.rolling(20, min_periods=20).sum() / df["volume"].rolling(20, min_periods=20).sum()
    return pv.groupby(session_key).cumsum() / df["volume"].groupby(session_key).cumsum()


def volume_ma(volume: pd.Series, n: int = 20) -> pd.Series:
    return sma(volume.astype(float), n)


def roc(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def relative_strength(close: pd.Series, benchmark: pd.Series, n: int = 63) -> pd.Series:
    """Excess n-bar return of the instrument over its benchmark (aligned on dates)."""
    b = benchmark.reindex(close.index).ffill()
    return roc(close, n) - roc(b, n)


def historical_volatility(close: pd.Series, n: int = 20, periods_per_year: int = 252) -> pd.Series:
    r = np.log(close / close.shift(1))
    return r.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(periods_per_year)


def gaps(df: pd.DataFrame) -> pd.Series:
    """Opening gap as fraction of previous close."""
    return df["open"] / df["close"].shift(1) - 1


def volume_breakout(df: pd.DataFrame, n: int = 20, mult: float = 1.5, lookback: int = 20) -> pd.Series:
    """True where close breaks the prior `lookback`-bar high on volume >= mult × average volume."""
    prior_high = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    vol_ok = df["volume"] >= mult * volume_ma(df["volume"], n).shift(1)
    return (df["close"] > prior_high) & vol_ok


def volume_breakdown(df: pd.DataFrame, n: int = 20, mult: float = 1.5, lookback: int = 20) -> pd.Series:
    prior_low = df["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    vol_ok = df["volume"] >= mult * volume_ma(df["volume"], n).shift(1)
    return (df["close"] < prior_low) & vol_ok


def compute_all(df: pd.DataFrame, benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Compute the standard feature set used by agents, backtests and the judge."""
    out = df.copy()
    c = df["close"]
    for n in (20, 50, 200):
        out[f"sma_{n}"] = sma(c, n)
    for n in (9, 20, 50):
        out[f"ema_{n}"] = ema(c, n)
    out["rsi_14"] = rsi(c, 14)
    out = out.join(macd(c))
    out["atr_14"] = atr(df, 14)
    out = out.join(adx(df, 14))
    out = out.join(bollinger(c))
    out["vwap_20"] = vwap(df)
    out["vol_ma_20"] = volume_ma(df["volume"], 20)
    out["roc_20"] = roc(c, 20)
    out["roc_63"] = roc(c, 63)
    out["roc_126"] = roc(c, 126)
    out["hv_20"] = historical_volatility(c, 20)
    out["gap"] = gaps(df)
    out["vol_breakout"] = volume_breakout(df)
    out["vol_breakdown"] = volume_breakdown(df)
    out["high_52w"] = df["high"].rolling(252, min_periods=200).max()
    out["low_52w"] = df["low"].rolling(252, min_periods=200).min()
    if benchmark is not None:
        out["rs_63"] = relative_strength(c, benchmark, 63)
    return out
