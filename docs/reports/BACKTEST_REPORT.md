# Backtest & calibration report — NIFTY 50

Generated 2026-11-07T12:54:18+00:00 from `python -m triagent.cli report`.

**Data:** SIMULATED regime-switching generator · 2006-01-02 → 2026-11-06 · 5440 bars · licence: Synthetic — no licence restrictions; NOT real market data

> **SIMULATED DATA.** These numbers validate that the engine runs end-to-end with costs, slippage, walk-forward and calibration. They say **nothing** about how any strategy performs on the real NIFTY 50. Re-run this command after loading real data.

Instrument traded: `NIFTY-FUT` (lot 65); capital ₹1,000,000; risk 1.0%/trade; slippage 5.0 bps/side; NSE cost schedule from `config/costs_in_nse.toml`.

## Strategy backtests (full period, after all costs)

| Strategy | Trades | Win % (95% CI) | Avg R | Profit factor | CAGR | Max DD | Sharpe | Costs ₹ | Net ₹ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| trend_pullback | 96 | 35% (27%–45%) | -0.186 | 0.676 | -0.67% | -15.8% | -0.336 | 10,406 | -134,751 |
| volume_breakout | 55 | 40% (28%–53%) | -0.083 | 0.861 | -0.16% | -11.7% | -0.106 | 6,257 | -33,531 |
| macd_trend | 75 | 43% (32%–54%) | -0.066 | 0.838 | -0.24% | -10.0% | -0.145 | 8,232 | -51,233 |
| bollinger_reversion | 78 | 37% (27%–48%) | -0.177 | 0.709 | -0.49% | -17.2% | -0.279 | 8,564 | -99,835 |

Benchmark — Buy & hold of the same series (price return; TRI preferred when available): total return +211.9%, CAGR +5.41%, max DD -48.2%.

## Walk-forward (4y train → 1y unseen test, rolling)

| Strategy | OOS trades | OOS avg R | OOS win % | OOS net ₹ |
|---|---:|---:|---:|---:|
| trend_pullback | 75 | -0.156 | 37% | -85,898 |
| volume_breakout | 37 | -0.137 | 35% | -40,100 |
| macd_trend | 58 | +0.022 | 43% | -1,244 |
| bollinger_reversion | 50 | -0.425 | 26% | -166,184 |

## JUDGE replay: weights & calibration (train 2007-01-01→2018-07-06, test 2018-07-06→2026-10-12, every 5 bars)

Composite ≥ threshold counted as a trade signal; outcome = plan hits Target 1 before stop.

| Weight set | Train signals | Train avg R | Test signals | Test avg R | Test hit % | Brier (test) | Brier (constant baseline) |
|---|---:|---:|---:|---:|---:|---:|---:|
| configured | 312 | +0.040 | 229 | +0.136 | 25% | 0.2013 | 0.2008 |
| equal | 226 | -0.012 | 164 | +0.150 | 26% | 0.2049 | 0.2033 |
| technical_heavy | 435 | +0.013 | 310 | +0.167 | 28% | 0.2011 | 0.2007 |

Reliability (test period, configured weights):

| Bin | n | Mean predicted | Observed |
|---:|---:|---:|---:|
| 0 | 83 | 19% | 32% |
| 4 | 331 | 25% | 26% |

_Composite thresholds and weights were NOT tuned on the test period. Analogous replays overlap in time (step < hold), so samples are not independent; treat significance with caution._

## Interpretation rules

* A strategy is **not** considered successful because of in-sample results. Only the walk-forward and test-period rows count, and only with ≥30 trades and a confidence interval that excludes break-even.
* If calibrated Brier is not better than the constant baseline, the composite carries no probabilistic information and the dashboard must keep showing it as an uncalibrated score.
