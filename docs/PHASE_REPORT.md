# Phase report — 2026-11-07

Status legend: ✅ built and tested · 🟡 built, partially tested or simulated-only · ⛔ not built / blocked.

## Environment inspected

* Empty repository; Linux container with 4 vCPU, 15 GB RAM, Python 3.13, Node 22. PostgreSQL 16 binaries were
  present (no TimescaleDB). There was no Docker daemon, so `docker compose` was **written but not run**.
* Network: package registries were reachable. **Yahoo Finance, nseindia.com, archives.nseindia.com and Stooq
  were blocked** by the sandbox policy, so no real market data could be loaded here. Every result in
  `docs/reports/` uses SIMULATED data and is labelled as such.
* Credentials: none were provided, and none are required for research or paper mode. Missing for later phases:
  `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN` (or another broker's), optional `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`, and `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `SMTP_*` for alerts.

## Phase 1 — Market data foundation

| | |
|---|---|
| Built | Source inventory (17 datasets with licence, claimed coverage, status); connectors for CSV (NSE header formats), Yahoo (unofficial, labelled) and SIMULATED; quality engine; corporate-action adjustment; SQLite/Postgres store with append-only triggers; SQL migration with Timescale hypertable |
| Tested | OHLC/duplicate/gap/stale/abnormal-move detection, CSV parsing of the NSE index format, Yahoo with a mocked HTTP transport (success and failure; failure never substitutes data), split adjustment, store round-trip, append-only on SQLite **and PostgreSQL 16** |
| Incomplete | Real 20-year NIFTY dataset not loaded (network blocked). Constituent, F&O, fundamentals, macro, FII/DII and option-chain connectors not built (see DATA_SOURCES.md). NSE holiday list not bundled |

## Phase 2 — Quantitative engine

| | |
|---|---|
| Built | SMA, EMA, RSI, MACD, ATR, ADX/DI, Bollinger, VWAP (session and rolling), volume MA, ROC, relative strength, HV, gaps, volume breakouts, 52-week range, S/R pivot clustering, trend/volatility/market regimes; 4 strategies + combination; event-driven backtester (next-open fills, adverse slippage, gap-through-stop fills, stop-first on ambiguous bars, effective-dated NSE costs, shorting rules, lot sizing); walk-forward; regime and crash-window slices; historical analogs |
| Tested | Indicators against hand/reference implementations; **causality test** (appending future bars changes no past value, for every feature); execution assumptions; cost hand-calculation; STT effective dates; walk-forward test windows used once |
| Result (SIMULATED) | No strategy beat buy & hold after costs. Walk-forward OOS avg R ranged from −0.43 to +0.02 (`BACKTEST_REPORT.md`). That is the expected, honest outcome for simple rules on a random-walk-like series |

## Phases 3–4 — Agents, debate, decision engine

| | |
|---|---|
| Built | Shared hashed snapshot; ~40 evidence rules across 6 categories with provenance; BULL (thesis, weaknesses, plan, invalidation); BEAR (counter-case, measured plan challenges, short/STAY OUT, concession); bounded 2-round debate; JUDGE (data gates, independent recomputation and claim rejection, weighted tanh scoring, shrunk track-record adjustment, contradiction and coverage gates, explanations); isotonic calibration; provider-independent LLM layer with a fabrication validator; versioned prompts |
| Tested | Evidence split and sourcing, missing data reported not invented, three-way decisions and rejecting both sides, tampered and invented claims rejected, FAIL/stale data → NO TRADE, future news excluded, index not tradable, debate bounded and non-repeating, trades always carry stop/invalidation/R:R ≥ min, fabricating LLM discarded |
| Result (SIMULATED) | The judge replay's calibration Brier was 0.2013 vs a 0.2008 constant baseline, i.e. **no probabilistic skill**. The composite stays labelled "not a probability" |
| Incomplete | LLM narratives not exercised against a real provider (no key). Fundamentals/macro/derivatives evidence rules exist but have no live data feeding them |

## Phase 5 — Dashboard

✅ Pages: Dashboard, Research (chart + BULL/BEAR/JUDGE panels with expandable evidence, gates, scores,
debate, risk checks), decision card with every required field, approval modal (CONFIRM TRADE), History and
scorecard, Backtest and calibration, Data (inventory, quality, gaps, ingest), Orders and Audit. Dark/light/system
themes, mobile layout, persistent emergency buttons, WebSocket refresh. Type-checks and builds. It was driven
end-to-end in headless Chromium with no console errors (screenshots in `docs/img/`). Unavailable feeds are
listed explicitly rather than faked.

## Phase 6 — Paper trading

✅ Paper broker (limit/market, slippage, partial fills, idempotency, cancel/modify, positions, realised P&L).
The ticket workflow is: risk-approved, non-stale decision → hash-bound ticket → `CONFIRM TRADE` → single
submission → reconcile → audit. Demonstrated in `reports/PAPER_DEMO.md`. 🟡 Paper exits on stop or target are
not monitored automatically yet; outcomes are evaluated via `/api/outcomes/evaluate`.

## Phase 7 — Evaluation

🟡 The machinery exists: walk-forward, held-out calibration, weight-set comparison, regime and crash slices,
scorecards with confidence intervals and NO-TRADE credit. It has only been run on simulated data. **Phase 7 is
not complete until it is re-run on real NIFTY data** and a paper-trading period has been reviewed.

## Phase 8 — Live integration

⛔ Deliberately not enabled. A Kite adapter skeleton exists, is wrapped by `LiveGuard`, is unit-tested with
mocks, and has **never been called against the live API**. The blockers are listed in `REGULATORY_CHECKLIST.md`.

## Final acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Retrieve & validate historical NIFTY 50 data | 🟡 connectors and validation tested; real retrieval blocked in this sandbox. Run `cli ingest --source yahoo` or import the NSE CSV locally |
| 2 | Identify historical data gaps | ✅ |
| 3 | Indicators correct | ✅ (reference and causality tests) |
| 4 | BULL sourced thesis | ✅ (every item has source + provenance; news items carry URL + timestamp) |
| 5 | BEAR sourced counter-thesis | ✅ |
| 6 | JUDGE challenges both, can reject both | ✅ |
| 7 | BUY / SELL / NO TRADE | ✅ |
| 8 | Auditable evidence trail | ✅ snapshot hash, append-only records, hash-chained audit |
| 9 | Position sizing & risk controls independently tested | ✅ 29 risk & execution tests |
| 10 | Realistic costs + OOS evaluation | 🟡 implemented and tested; real-data run pending |
| 11 | Paper trades recorded & reconciled | ✅ (automatic paper exit monitoring pending) |
| 12 | Dashboard shows full debate & judgement | ✅ |
| 13 | Stop immediately | ✅ STOP ALL TRADING (persisted, cancels open orders and pending tickets, blocks confirmation) |
| 14 | No real-money order while LIVE disabled | ✅ tested with a spy broker: zero calls |

## Outstanding risks

1. **No authentication** on the API/dashboard. Keep it on localhost or behind a VPN.
2. Cost and contract parameters (2026 STT change, lot 65, Tuesday expiry) come from secondary sources and must be verified.
3. Historical backtests use today's lot size for the futures proxy, and treat index levels as futures prices
   (basis ignored). Both are documented in the plan notes.
4. Survivorship bias controls are needed before any constituent-level research (PIT constituents not loaded).
5. The weights and thresholds are untested on real data. Treat every signal as unvalidated until Phase 7 is done on real data.
