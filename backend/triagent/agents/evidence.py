"""Deterministic evidence extraction from a MarketSnapshot.

Every item is a measured observation with a metric and value that the JUDGE can
recompute. Items with direction 0 are context or risk flags (not scored).
Missing datasets produce explicit UNVERIFIED items instead of guesses.
"""
from __future__ import annotations

from datetime import timedelta

from triagent.agents.snapshot import MarketSnapshot
from triagent.core.models import Category, EvidenceItem, Provenance, SourceRef

C = Category


def _item(snap: MarketSnapshot, id_: str, cat: Category, claim: str, direction: int, strength: float,
          metric: str | None, value, prov: Provenance = Provenance.CURRENT_OBSERVATION,
          source: SourceRef | None = None) -> EvidenceItem:
    return EvidenceItem(id=id_, category=cat, claim=claim, direction=direction, strength=round(min(1.0, max(0.0, strength)), 3),
                        metric=metric, value=value, provenance=prov, source=source or snap.price_source,
                        as_of=snap.as_of)


def technical(snap: MarketSnapshot) -> list[EvidenceItem]:
    L = snap.latest
    out: list[EvidenceItem] = []
    c, s50, s200, atr = L.get("close"), L.get("sma_50"), L.get("sma_200"), L.get("atr_14")
    adx, rsi = L.get("adx"), L.get("rsi_14")
    if None in (c, s50, s200, atr) or not atr:
        out.append(_item(snap, "TECH.INSUFFICIENT", C.TECHNICAL, "Insufficient history for core trend indicators",
                         0, 0.0, None, None, Provenance.UNVERIFIED))
        return out
    strong = adx is not None and adx >= 25
    if c > s50 > s200:
        out.append(_item(snap, "TECH.MA_STACK", C.TECHNICAL,
                         f"Bullish MA stack: close {c:.2f} > SMA50 {s50:.2f} > SMA200 {s200:.2f}" + (" with ADX≥25" if strong else ""),
                         1, 0.8 if strong else 0.6, "close_vs_sma50_vs_sma200", 1))
    elif c < s50 < s200:
        out.append(_item(snap, "TECH.MA_STACK", C.TECHNICAL,
                         f"Bearish MA stack: close {c:.2f} < SMA50 {s50:.2f} < SMA200 {s200:.2f}" + (" with ADX≥25" if strong else ""),
                         -1, 0.8 if strong else 0.6, "close_vs_sma50_vs_sma200", -1))
    else:
        out.append(_item(snap, "TECH.MA_STACK", C.TECHNICAL, "Mixed moving-average alignment (no clean trend)",
                         0, 0.0, "close_vs_sma50_vs_sma200", 0))

    if rsi is not None:
        if rsi >= 70:
            out.append(_item(snap, "TECH.RSI", C.TECHNICAL, f"RSI(14) {rsi:.1f} — overbought; pullback risk", -1, 0.45, "rsi_14", rsi))
        elif rsi <= 30:
            out.append(_item(snap, "TECH.RSI", C.TECHNICAL, f"RSI(14) {rsi:.1f} — oversold; bounce potential", 1, 0.35, "rsi_14", rsi))
        elif rsi >= 55:
            out.append(_item(snap, "TECH.RSI", C.TECHNICAL, f"RSI(14) {rsi:.1f} — positive momentum without overbought", 1, 0.3, "rsi_14", rsi))
        elif rsi <= 45:
            out.append(_item(snap, "TECH.RSI", C.TECHNICAL, f"RSI(14) {rsi:.1f} — weak momentum", -1, 0.3, "rsi_14", rsi))

    hist = L.get("hist")
    if hist is not None:
        d = 1 if hist > 0 else -1
        out.append(_item(snap, "TECH.MACD", C.TECHNICAL,
                         f"MACD histogram {hist:+.2f} ({'above' if d > 0 else 'below'} signal line)", d, 0.3, "hist", hist))

    pdi, mdi = L.get("plus_di"), L.get("minus_di")
    if adx is not None and pdi is not None and mdi is not None:
        if adx >= 20:
            d = 1 if pdi > mdi else -1
            out.append(_item(snap, "TECH.ADX", C.TECHNICAL,
                             f"ADX {adx:.1f} trending; +DI {pdi:.1f} vs −DI {mdi:.1f}", d, min(0.6, adx / 60), "adx", adx))
        else:
            out.append(_item(snap, "TECH.ADX", C.TECHNICAL, f"ADX {adx:.1f} < 20 — no established trend (range conditions)",
                             0, 0.0, "adx", adx))

    pb = L.get("bb_pct_b")
    if pb is not None:
        if pb > 1:
            out.append(_item(snap, "TECH.BOLL", C.TECHNICAL, f"Close above upper Bollinger band (%B {pb:.2f}) — stretched", -1, 0.3, "bb_pct_b", pb))
        elif pb < 0:
            out.append(_item(snap, "TECH.BOLL", C.TECHNICAL, f"Close below lower Bollinger band (%B {pb:.2f}) — stretched to downside", 1, 0.25, "bb_pct_b", pb))

    if L.get("vol_breakout_recent"):
        out.append(_item(snap, "TECH.VOL_BREAKOUT", C.TECHNICAL, "20-bar high breakout on ≥1.5× average volume within last 3 bars", 1, 0.5, "vol_breakout_recent", 1))
    if L.get("vol_breakdown_recent"):
        out.append(_item(snap, "TECH.VOL_BREAKDOWN", C.TECHNICAL, "20-bar low breakdown on ≥1.5× average volume within last 3 bars", -1, 0.5, "vol_breakdown_recent", 1))

    hi52, lo52 = L.get("high_52w"), L.get("low_52w")
    if hi52 and c >= 0.97 * hi52 and c > s200:
        out.append(_item(snap, "TECH.52W", C.TECHNICAL, f"Within 3% of 52-week high ({hi52:.2f}) — relative strength", 1, 0.3, "close_div_high_52w", round(c / hi52, 4)))
    elif lo52 and c <= 1.03 * lo52 and c < s200:
        out.append(_item(snap, "TECH.52W", C.TECHNICAL, f"Within 3% of 52-week low ({lo52:.2f}) — persistent weakness", -1, 0.3, "close_div_low_52w", round(c / lo52, 4)))

    for n, w in (("roc_63", 0.35), ("roc_126", 0.3)):
        v = L.get(n)
        if v is not None and abs(v) > 0.02:
            out.append(_item(snap, f"TECH.{n.upper()}", C.TECHNICAL, f"{n.split('_')[1]}-bar return {v:+.1%}", 1 if v > 0 else -1, w, n, v))

    # divergences (price makes new extreme over last 20 bars while RSI does not)
    cm_l, cm_p, rm_l, rm_p = (L.get(k) for k in ("close_max_last20", "close_max_prev20", "rsi_max_last20", "rsi_max_prev20"))
    if None not in (cm_l, cm_p, rm_l, rm_p) and cm_l > cm_p and rm_l < rm_p - 3:
        out.append(_item(snap, "TECH.BEAR_DIVERGENCE", C.TECHNICAL,
                         f"Bearish divergence: price high {cm_l:.2f} > prior {cm_p:.2f} but RSI high {rm_l:.1f} < {rm_p:.1f}", -1, 0.4, "rsi_max_last20", rm_l))
    cn_l, cn_p, rn_l, rn_p = (L.get(k) for k in ("close_min_last20", "close_min_prev20", "rsi_min_last20", "rsi_min_prev20"))
    if None not in (cn_l, cn_p, rn_l, rn_p) and cn_l < cn_p and rn_l > rn_p + 3:
        out.append(_item(snap, "TECH.BULL_DIVERGENCE", C.TECHNICAL,
                         f"Bullish divergence: price low {cn_l:.2f} < prior {cn_p:.2f} but RSI low {rn_l:.1f} > {rn_p:.1f}", 1, 0.4, "rsi_min_last20", rn_l))

    ph, mh5 = L.get("prior_high_20"), L.get("max_high_5")
    if ph and mh5 and mh5 > ph and c < ph:
        out.append(_item(snap, "TECH.FAILED_BREAKOUT", C.TECHNICAL,
                         f"Failed breakout: traded above 20-bar high {ph:.2f} in last 5 bars but closed back below", -1, 0.45, "prior_high_20", ph))
    pl, ml5 = L.get("prior_low_20"), L.get("min_low_5")
    if pl and ml5 and ml5 < pl and c > pl:
        out.append(_item(snap, "TECH.FAILED_BREAKDOWN", C.TECHNICAL,
                         f"Failed breakdown (bear trap): traded below 20-bar low {pl:.2f} but closed back above", 1, 0.4, "prior_low_20", pl))

    ema20 = L.get("ema_20")
    if ema20:
        ext = (c - ema20) / atr
        if abs(ext) >= 2.5:
            out.append(_item(snap, "TECH.EXTENSION", C.TECHNICAL,
                             f"Price {ext:+.1f} ATR from EMA20 — extended; late-entry risk for trend followers",
                             -1 if ext > 0 else 1, 0.35, "dist_ema20_atr", round(ext, 3)))

    # nearest support / resistance
    sup = [lv for lv in snap.levels if lv.kind == "SUPPORT"]
    res = [lv for lv in snap.levels if lv.kind == "RESISTANCE"]
    if res:
        r0 = res[0]
        dist = (r0.price - c) / atr
        if dist <= 1.0:
            out.append(_item(snap, "TECH.RESISTANCE_NEAR", C.TECHNICAL,
                             f"Resistance {r0.price:.2f} ({r0.touches} touches) only {dist:.1f} ATR above — limits upside",
                             -1, 0.35, "resistance_dist_atr", round(dist, 3)))
    if sup:
        s0 = sup[0]
        dist = (c - s0.price) / atr
        if dist <= 1.0:
            out.append(_item(snap, "TECH.SUPPORT_NEAR", C.TECHNICAL,
                             f"Support {s0.price:.2f} ({s0.touches} touches) {dist:.1f} ATR below — defined risk for longs",
                             1, 0.3, "support_dist_atr", round(dist, 3)))
    rs = L.get("rs_63")
    if rs is not None and abs(rs) > 0.02:
        out.append(_item(snap, "TECH.REL_STRENGTH", C.TECHNICAL, f"63-bar return vs benchmark {rs:+.1%}", 1 if rs > 0 else -1, 0.35, "rs_63", rs))
    if (L.get("volume") or 0) == 0:
        out.append(_item(snap, "TECH.NO_VOLUME", C.TECHNICAL, "Volume data unavailable — volume confirmation not assessed",
                         0, 0.0, None, None, Provenance.UNVERIFIED))
    return out


def regime(snap: MarketSnapshot) -> list[EvidenceItem]:
    out = []
    d = {"BULL": 1, "BEAR": -1, "CRASH": -1}.get(snap.regime, 0)
    s = {"BULL": 0.5, "BEAR": 0.5, "CRASH": 0.8}.get(snap.regime, 0.0)
    out.append(_item(snap, "MACRO.REGIME", C.MACRO_REGIME, f"Market regime classified as {snap.regime} (trend: {snap.trend})",
                     d, s, "regime", snap.regime, Provenance.MODEL_ESTIMATE))
    if snap.vol_regime == "HIGH_VOL":
        out.append(_item(snap, "MACRO.VOL_REGIME", C.MACRO_REGIME,
                         f"High volatility regime (20d HV {snap.latest.get('hv_20', 0):.1%} in top quartile of trailing 3y)",
                         0, 0.0, "vol_regime", snap.vol_regime, Provenance.MODEL_ESTIMATE))
    elif snap.vol_regime == "LOW_VOL":
        out.append(_item(snap, "MACRO.VOL_REGIME", C.MACRO_REGIME, "Low volatility regime (20d HV in bottom quartile)",
                         0, 0.0, "vol_regime", snap.vol_regime, Provenance.MODEL_ESTIMATE))
    m = snap.macro
    if m is None:
        out.append(_item(snap, "MACRO.UNAVAILABLE", C.MACRO_REGIME,
                         "Macro dataset (RBI policy, CPI, USD/INR, crude, FII flows, US yields) not loaded — not assessed",
                         0, 0.0, None, None, Provenance.UNVERIFIED))
        return out
    v = m.values
    rules = [
        ("india_vix", lambda x: (-1, 0.5, f"India VIX {x:.1f} elevated (>22)") if x > 22 else ((1, 0.2, f"India VIX {x:.1f} calm") if x < 14 else None)),
        ("repo_rate_change_bps", lambda x: (1, 0.4, f"RBI cut repo by {abs(x):.0f} bps") if x < 0 else ((-1, 0.4, f"RBI raised repo by {x:.0f} bps") if x > 0 else None)),
        ("fii_net_5d_cr", lambda x: (1 if x > 0 else -1, min(0.5, abs(x) / 20000), f"FII net {x:+,.0f} cr over 5 sessions") if abs(x) > 2000 else None),
        ("usd_inr_change_1m", lambda x: (-1, 0.3, f"Rupee weakened {x:+.1%} in 1 month") if x > 0.015 else ((1, 0.2, f"Rupee strengthened {x:+.1%}") if x < -0.015 else None)),
        ("brent_change_1m", lambda x: (-1, 0.3, f"Brent crude {x:+.1%} in 1 month (import-cost pressure)") if x > 0.1 else ((1, 0.2, f"Brent crude {x:+.1%} in 1 month") if x < -0.1 else None)),
        ("us10y_change_1m_bps", lambda x: (-1, 0.25, f"US 10Y yield {x:+.0f} bps in 1 month") if x > 30 else ((1, 0.2, f"US 10Y yield {x:+.0f} bps") if x < -30 else None)),
        ("cpi_yoy", lambda x: (-1, 0.25, f"CPI {x:.1f}% above RBI 6% upper tolerance") if x > 6 else None),
    ]
    for key, fn in rules:
        if key in v and v[key] is not None:
            r = fn(float(v[key]))
            if r:
                out.append(_item(snap, f"MACRO.{key.upper()}", C.MACRO_REGIME, r[2], r[0], r[1], key, float(v[key]),
                                 Provenance.CURRENT_OBSERVATION, m.source))
    return out


def fundamental(snap: MarketSnapshot) -> list[EvidenceItem]:
    f = snap.fundamentals
    if f is None:
        return [_item(snap, "FUND.UNAVAILABLE", C.FUNDAMENTAL,
                      "Point-in-time fundamental data not loaded — fundamentals not assessed", 0, 0.0, None, None,
                      Provenance.UNVERIFIED)]
    v, out = f.values, []
    rules = [
        ("pe_percentile_10y", lambda x: (-1, 0.5, f"P/E at {x:.0%} percentile of 10-year range — rich valuation") if x > 0.8 else ((1, 0.45, f"P/E at {x:.0%} percentile of 10-year range — inexpensive") if x < 0.2 else None)),
        ("eps_growth_yoy", lambda x: (1, 0.45, f"EPS growth {x:+.0%} YoY") if x > 0.12 else ((-1, 0.45, f"EPS growth {x:+.0%} YoY") if x < 0 else None)),
        ("revenue_growth_yoy", lambda x: (1, 0.3, f"Revenue growth {x:+.0%} YoY") if x > 0.1 else ((-1, 0.3, f"Revenue growth {x:+.0%} YoY") if x < 0 else None)),
        ("roe", lambda x: (1, 0.3, f"ROE {x:.0%}") if x > 0.18 else ((-1, 0.25, f"ROE {x:.0%} weak") if x < 0.08 else None)),
        ("debt_to_equity", lambda x: (-1, 0.4, f"Debt/Equity {x:.2f} — leverage risk") if x > 1.5 else None),
        ("promoter_pledge_pct", lambda x: (-1, 0.5, f"Promoter pledge {x:.0%} of holding") if x > 0.2 else None),
        ("earnings_surprise_pct", lambda x: (1 if x > 0 else -1, min(0.4, abs(x) * 4), f"Last earnings surprise {x:+.1%}") if abs(x) > 0.03 else None),
    ]
    for key, fn in rules:
        if key in v and v[key] is not None:
            r = fn(float(v[key]))
            if r:
                out.append(_item(snap, f"FUND.{key.upper()}", C.FUNDAMENTAL, r[2], r[0], r[1], key, float(v[key]),
                                 Provenance.VERIFIED_HISTORICAL, f.source))
    if not out:
        out.append(_item(snap, "FUND.NEUTRAL", C.FUNDAMENTAL, "Fundamentals loaded; no metric outside neutral bands",
                         0, 0.0, None, None, Provenance.VERIFIED_HISTORICAL, f.source))
    return out


def derivatives(snap: MarketSnapshot) -> list[EvidenceItem]:
    d = snap.derivatives
    if d is None:
        return [_item(snap, "DERIV.UNAVAILABLE", C.DERIVATIVES,
                      "Derivatives data (OI, PCR, IV, basis) not loaded — positioning not assessed", 0, 0.0, None, None,
                      Provenance.UNVERIFIED)]
    v, out = d.values, []
    if v.get("pcr") is not None:
        x = float(v["pcr"])
        if x > 1.3:
            out.append(_item(snap, "DERIV.PCR", C.DERIVATIVES, f"PCR {x:.2f} — heavy put writing (support) but crowded", 1, 0.3, "pcr", x, source=d.source))
        elif x < 0.7:
            out.append(_item(snap, "DERIV.PCR", C.DERIVATIVES, f"PCR {x:.2f} — call-heavy positioning (resistance)", -1, 0.3, "pcr", x, source=d.source))
    oi, px = v.get("oi_change_pct_5d"), v.get("price_change_pct_5d")
    if oi is not None and px is not None:
        oi, px = float(oi), float(px)
        if oi > 0.05 and px > 0:
            out.append(_item(snap, "DERIV.OI_BUILDUP", C.DERIVATIVES, f"Long build-up: OI {oi:+.0%}, price {px:+.1%}", 1, 0.4, "oi_change_pct_5d", oi, source=d.source))
        elif oi > 0.05 and px < 0:
            out.append(_item(snap, "DERIV.OI_BUILDUP", C.DERIVATIVES, f"Short build-up: OI {oi:+.0%}, price {px:+.1%}", -1, 0.4, "oi_change_pct_5d", oi, source=d.source))
        elif oi < -0.05 and px > 0:
            out.append(_item(snap, "DERIV.OI_BUILDUP", C.DERIVATIVES, f"Short covering: OI {oi:+.0%}, price {px:+.1%}", 1, 0.2, "oi_change_pct_5d", oi, source=d.source))
        elif oi < -0.05 and px < 0:
            out.append(_item(snap, "DERIV.OI_BUILDUP", C.DERIVATIVES, f"Long unwinding: OI {oi:+.0%}, price {px:+.1%}", -1, 0.2, "oi_change_pct_5d", oi, source=d.source))
    if v.get("iv_percentile") is not None:
        x = float(v["iv_percentile"])
        if x > 0.8:
            out.append(_item(snap, "DERIV.IV", C.DERIVATIVES, f"Implied vol at {x:.0%} percentile — event/hedging demand", -1, 0.3, "iv_percentile", x, source=d.source))
    if v.get("basis_annualised") is not None:
        x = float(v["basis_annualised"])
        if x < 0:
            out.append(_item(snap, "DERIV.BASIS", C.DERIVATIVES, f"Futures at discount ({x:.1%} annualised) — bearish positioning", -1, 0.3, "basis_annualised", x, source=d.source))
    return out or [_item(snap, "DERIV.NEUTRAL", C.DERIVATIVES, "Derivatives positioning neutral", 0, 0.0, None, None, source=d.source)]


def news_events(snap: MarketSnapshot, lookback_days: int = 7) -> list[EvidenceItem]:
    out = []
    target_entities = {snap.underlying.symbol, snap.instrument.symbol, "MACRO", "NIFTY 50"}
    recent = [n for n in snap.news if n.published_at >= snap.as_of - timedelta(days=lookback_days)
              and (set(n.entities) & target_entities)]
    verified = [n for n in recent if n.verified]
    unverified = [n for n in recent if not n.verified]
    for n in sorted(verified, key=lambda x: -x.importance)[:5]:
        src = SourceRef(source=n.source, url=n.url, published_at=n.published_at, retrieved_at=n.retrieved_at)
        out.append(_item(snap, f"NEWS.{n.id[:10]}", C.NEWS_EVENTS, f"[{n.source}] {n.title}", n.sentiment,
                         round(0.5 * n.importance, 3) if n.sentiment else 0.0, "news_sentiment", n.sentiment,
                         Provenance.CURRENT_OBSERVATION, src))
    for n in unverified[:3]:
        src = SourceRef(source=n.source, url=n.url, published_at=n.published_at, retrieved_at=n.retrieved_at)
        out.append(_item(snap, f"NEWS.UNV.{n.id[:10]}", C.NEWS_EVENTS,
                         f"UNVERIFIED/speculative: [{n.source}] {n.title} — not scored", 0, 0.0, "news_sentiment",
                         n.sentiment, Provenance.UNVERIFIED, src))
    if not recent:
        out.append(_item(snap, "NEWS.NONE", C.NEWS_EVENTS,
                         f"No verified news for this instrument in the last {lookback_days} days in the loaded feed",
                         0, 0.0, None, None, Provenance.UNVERIFIED))
    for ev in snap.events:
        out.append(_item(snap, f"EVENT.{ev['id']}", C.NEWS_EVENTS,
                         f"Scheduled event within holding period: {ev['name']} on {ev['date']} — outcome unknown (gap risk)",
                         0, 0.0, "event_date", ev["date"], Provenance.CURRENT_OBSERVATION,
                         SourceRef(source=ev.get("source", "event calendar"), url=ev.get("url"))))
    return out


def historical(snap: MarketSnapshot) -> list[EvidenceItem]:
    a = snap.analogs
    if not a.get("available"):
        return [_item(snap, "HIST.UNAVAILABLE", C.HISTORICAL, f"Historical analogs unavailable: {a.get('reason')}", 0, 0.0,
                      None, None, Provenance.UNVERIFIED)]
    pp, lt, st = a["pct_positive"], a["long_plan_target_first"], a["short_plan_target_first"]
    out = []
    d = 1 if pp > 0.55 else (-1 if pp < 0.45 else 0)
    out.append(_item(snap, "HIST.ANALOG_DIRECTION", C.HISTORICAL,
                     f"{a['n']} closest historical analogs: {pp:.0%} positive after {a['hold_bars']} bars "
                     f"(median {a['median_forward_return']:+.2%}, p10 {a['p10']:+.2%}, p90 {a['p90']:+.2%})",
                     d, min(0.6, abs(pp - 0.5) * 3) if d else 0.0, "pct_positive", pp, Provenance.VERIFIED_HISTORICAL))
    if lt > 0.4 or st > 0.4 or lt < 0.25:
        better = 1 if lt >= st else -1
        out.append(_item(snap, "HIST.ANALOG_PLAN", C.HISTORICAL,
                         f"In analogs a 2-ATR-stop/2R-target long hit target first {lt:.0%}, short {st:.0%} "
                         f"(break-even for 2R ≈ 33%)", better, min(0.5, abs(lt - st) * 2), "long_plan_target_first", lt,
                         Provenance.VERIFIED_HISTORICAL))
    return out


def extract_all(snap: MarketSnapshot) -> list[EvidenceItem]:
    return technical(snap) + regime(snap) + fundamental(snap) + derivatives(snap) + news_events(snap) + historical(snap)
