"""Immutable, point-in-time market snapshot shared by BULL, BEAR and JUDGE."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from triagent.core.models import (HORIZON_SPEC, DataQualityReport, Horizon, Instrument, InstrumentType, SourceRef,
                                  stable_hash)
from triagent.markets.base import MarketProfile
from triagent.news.service import NewsItem
from triagent.quant.analogs import find_analogs
from triagent.quant.regime import Level, market_regime, support_resistance, trend_label, volatility_regime


@dataclass
class ContextBlock:
    """Optional externally-sourced data (fundamentals, macro, derivatives, breadth) with provenance."""
    values: dict
    source: SourceRef
    as_of: datetime


@dataclass
class MarketSnapshot:
    underlying: Instrument            # the analysed series (may be a non-tradable index)
    instrument: Instrument            # the tradable instrument the plan refers to
    horizon: Horizon
    as_of: datetime
    features: pd.DataFrame
    latest: dict
    levels: list[Level]
    trend: str
    vol_regime: str
    regime: str
    analogs: dict
    quality: DataQualityReport
    price_source: SourceRef
    fundamentals: ContextBlock | None = None
    macro: ContextBlock | None = None
    derivatives: ContextBlock | None = None
    breadth: ContextBlock | None = None
    news: list[NewsItem] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    plan_price_note: str = ""
    short_allowed: bool = False
    short_reason: str = ""

    @property
    def hold_bars(self) -> int:
        return HORIZON_SPEC[self.horizon]["hold_bars"]

    def hashable(self) -> dict:
        tail = self.features.tail(5)[["open", "high", "low", "close", "volume"]]
        return {
            "underlying": self.underlying.model_dump(), "instrument": self.instrument.model_dump(),
            "horizon": self.horizon.value, "as_of": self.as_of.isoformat(),
            "latest": self.latest, "levels": [vars(lv) for lv in self.levels], "trend": self.trend,
            "vol_regime": self.vol_regime, "regime": self.regime, "analogs": self.analogs,
            "last_bars": {str(k): v for k, v in tail.to_dict("index").items()},
            "n_bars": len(self.features),
            "fundamentals": self.fundamentals.values if self.fundamentals else None,
            "macro": self.macro.values if self.macro else None,
            "derivatives": self.derivatives.values if self.derivatives else None,
            "breadth": self.breadth.values if self.breadth else None,
            "news": [n.id for n in self.news], "events": self.events,
            "quality": self.quality.status.value,
        }

    def hash(self) -> str:
        return stable_hash(self.hashable())


LATEST_FIELDS = ["open", "high", "low", "close", "volume", "sma_20", "sma_50", "sma_200", "ema_9", "ema_20",
                 "ema_50", "rsi_14", "macd", "signal", "hist", "atr_14", "plus_di", "minus_di", "adx", "bb_upper",
                 "bb_lower", "bb_mid", "bb_pct_b", "bb_width", "vwap_20", "vol_ma_20", "roc_20", "roc_63", "roc_126",
                 "hv_20", "gap", "high_52w", "low_52w", "rs_63"]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (bool,)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) else round(f, 6)


def build_snapshot(features_full: pd.DataFrame, underlying: Instrument, instrument: Instrument, horizon: Horizon,
                   profile: MarketProfile, quality: DataQualityReport, price_source: SourceRef,
                   as_of: pd.Timestamp | None = None, news: list[NewsItem] | None = None,
                   events: list[dict] | None = None, regimes: tuple[pd.Series, pd.Series] | None = None,
                   **context) -> MarketSnapshot:
    """Slice features to `as_of` (inclusive) and derive everything from that slice only."""
    f = features_full if as_of is None else features_full.loc[:as_of]
    t_pos = len(f) - 1
    row = f.iloc[-1]
    latest = {k: _clean(row.get(k)) for k in LATEST_FIELDS if k in f.columns}
    latest["vol_breakout_recent"] = bool(f["vol_breakout"].tail(3).any())
    latest["vol_breakdown_recent"] = bool(f["vol_breakdown"].tail(3).any())
    # 20-bar prior high/low (exclude today) for failed-breakout / divergence tests
    latest["prior_high_20"] = _clean(f["high"].iloc[-21:-1].max()) if len(f) > 21 else None
    latest["prior_low_20"] = _clean(f["low"].iloc[-21:-1].min()) if len(f) > 21 else None
    latest["max_high_5"] = _clean(f["high"].tail(5).max())
    latest["min_low_5"] = _clean(f["low"].tail(5).min())
    if len(f) > 40:
        latest["rsi_max_prev20"] = _clean(f["rsi_14"].iloc[-40:-20].max())
        latest["rsi_min_prev20"] = _clean(f["rsi_14"].iloc[-40:-20].min())
        latest["close_max_prev20"] = _clean(f["close"].iloc[-40:-20].max())
        latest["close_min_prev20"] = _clean(f["close"].iloc[-40:-20].min())
        latest["rsi_max_last20"] = _clean(f["rsi_14"].tail(20).max())
        latest["rsi_min_last20"] = _clean(f["rsi_14"].tail(20).min())
        latest["close_max_last20"] = _clean(f["close"].tail(20).max())
        latest["close_min_last20"] = _clean(f["close"].tail(20).min())
    latest["avg_daily_value_20"] = _clean((f["close"] * f["volume"]).tail(20).mean())

    if regimes is not None:
        vol_reg, mkt_reg = regimes[0].loc[f.index[-1]], regimes[1].loc[f.index[-1]]
    else:
        vol_reg, mkt_reg = volatility_regime(f).iloc[-1], market_regime(f).iloc[-1]

    short_ok, short_reason = profile.can_hold_short(instrument, horizon)
    note = ""
    if underlying.instrument_type == InstrumentType.INDEX and instrument.symbol != underlying.symbol:
        note = (f"Price levels are {underlying.symbol} index levels. {instrument.symbol} trades at a basis to the "
                f"index (basis data not loaded) — translate levels using the live basis before ordering.")
    last_ts = f.index[-1]
    if last_ts.hour == 0 and last_ts.minute == 0:  # daily bar → information is as of the session close
        sess = profile.session(last_ts.date())
        as_of_ts = (sess[1] if sess else last_ts.tz_localize(profile.timezone).to_pydatetime()).astimezone(timezone.utc)
    else:
        as_of_ts = last_ts.tz_localize(profile.timezone).to_pydatetime().astimezone(timezone.utc)
    return MarketSnapshot(
        underlying=underlying, instrument=instrument, horizon=horizon, as_of=as_of_ts, features=f, latest=latest,
        levels=support_resistance(f), trend=trend_label(row), vol_regime=str(vol_reg), regime=str(mkt_reg),
        analogs=find_analogs(f, t_pos, HORIZON_SPEC[horizon]["hold_bars"]), quality=quality,
        price_source=price_source, news=[n for n in (news or []) if n.published_at <= as_of_ts],
        events=events or [], plan_price_note=note, short_allowed=short_ok, short_reason=short_reason,
        fundamentals=context.get("fundamentals"), macro=context.get("macro"),
        derivatives=context.get("derivatives"), breadth=context.get("breadth"),
    )
