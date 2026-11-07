# Agents: BULL, BEAR, JUDGE

Prompts (versioned) live in `backend/triagent/agents/prompts/` (`bull-v1.0.0`, `bear-v1.0.0`, `judge-v1.0.0`).
The prompt version and model are stored in every decision record. **With `llm.provider = "none"` (default) the
agents are fully deterministic**. Their reasoning is rule-based over measured evidence, and narratives come from
templates.

## Shared input

One immutable `MarketSnapshot` per decision. It holds features up to the decision bar, levels, regimes, analogs,
point-in-time news, events in the holding window, a data-quality report, and optional fundamentals, macro,
derivatives and breadth blocks, each with its source and as-of date. It is hashed, and the hash is stored with
the decision.

## Evidence (`agents/evidence.py`)

| Category | Items (id prefix) |
|---|---|
| technical | MA stack, RSI, MACD, ADX/DI, Bollinger %B, volume breakout/breakdown, 52-week proximity, 63/126-bar momentum, bullish/bearish RSI divergence, failed breakout/breakdown, extension from EMA20, near support/resistance, relative strength vs NIFTY, missing volume |
| macro_regime | market regime (model estimate), volatility regime, India VIX, repo change, FII 5-day flow, USD/INR, Brent, US10Y, CPI (only when loaded) |
| fundamental | P/E percentile, EPS/revenue growth, ROE, D/E, promoter pledge, earnings surprise (only when loaded, PIT) |
| derivatives | PCR, OI build-up type, IV percentile, futures basis (only when loaded) |
| news_events | verified official/wire news (sentiment × importance), unverified items listed but **not scored**, scheduled events in the holding window (risk, not direction) |
| historical | k-nearest historical analogs (pct positive, plan target-first rates) — descriptive, overlapping samples |

Every item carries a provenance class: verified historical fact, current observation, agent interpretation,
model estimate, or unverified/unavailable.

## BULL

Supporting evidence (direction +1), its own weaknesses (the strongest −1 items), and a long plan built from
structure and ATR. The plan has an entry zone, a stop 0.25 ATR beyond support (or 2 ATR), a T1 at the next
resistance beyond 1R (or a 2R projection) and a T2 (projection flagged). It also gives R:R, holding period and
invalidation. If no supporting evidence exists, BULL proposes nothing.

## BEAR

Counter-evidence (−1), the bullish points it acknowledges, and **measured challenges to BULL's plan**:
- R:R below the minimum
- stop within noise (<0.75 ATR) or too wide
- late entry (>2 ATR above EMA20)
- resistance within 1 ATR
- scheduled events inside the hold
- analog target-first rate below break-even
- a HIGH_VOL regime
- non-PASS data quality

BEAR proposes a SHORT only when the instrument and horizon allow it (no overnight cash-equity shorts in India).
Otherwise it recommends STAY OUT. It **concedes** when bullish strength is at least 1.6× bearish and at most one
plan challenge remains.

## Debate

At most 2 rounds and 3 points per side per round. Each point gets one response:
- **refuted** if the responder's evidence in the same category is stronger in aggregate
- **contextualised** if the point is minor and the overall balance favours the responder
- **conceded** otherwise

No point is argued twice. BULL must answer BEAR's plan challenges, which are measured facts and are conceded.

## JUDGE

1. **Data gates**: quality not FAIL, data not stale, snapshot ≤ decision time, no news after the snapshot,
   at least 250 bars, tradable instrument.
2. **Claim verification**: the judge recomputes all evidence from the snapshot. An agent item whose id, value or
   direction doesn't reproduce is rejected and listed. The judge also considers evidence neither agent cited.
3. **Weighted scoring** (`config/settings.toml [scoring.weights]`): category score = tanh(Σ direction·strength).
   Risk/reward is scored from the competing plans. The historical score is adjusted by each agent's track record
   using **sample-size-shrunk expectancy** E·n/(n+30), never raw win rate.
4. **Gates**: contradiction (≥2 strongly bullish and ≥2 strongly bearish categories) and coverage (≥45% of
   weight must have real data).
5. **Decision**: BUY if composite ≥ +0.25 and the long plan's R:R ≥ 1.5. SELL if composite ≤ −0.25, shorting is
   allowed and the short plan's R:R ≥ 1.5. NO TRADE otherwise, including when any gate fails.
6. The **risk engine** can then still turn BUY or SELL into NO TRADE; the record keeps both.

The thresholds and weights are starting assumptions. `python -m triagent.cli report` replays the pipeline
point-in-time, compares weight sets on a held-out period and fits an isotonic calibrator. On the bundled
simulated data, calibration did **not** beat a constant baseline. So the dashboard labels the composite
"not a probability" and shows no calibrated probability unless one is fitted.
