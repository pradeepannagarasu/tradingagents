# Data sources & coverage

**No single API provides 20 years of all the required Indian datasets.** The live inventory is served at
`GET /api/data/inventory` (and on the dashboard's Data page) from `backend/triagent/data/inventory.py`. *Claimed
coverage* is what the provider documents. *Actual coverage* is measured from loaded data, together with its gaps.

## Status at the end of this build (2026-11-07)

| Dataset | Best source | Status | Notes |
|---|---|---|---|
| NIFTY 50 / India VIX daily history | niftyindices.com historical data (manual download) | **CSV import implemented** | NSE terms apply; redistribution needs a data licence |
| NIFTY 50 TRI (benchmark) | niftyindices.com | CSV import implemented | Backtests currently benchmark against the price series and say so |
| NIFTY 50 daily via API | Yahoo Finance (unofficial) | **Connector implemented** | Personal, non-commercial use only. Not exchange-licensed. Known gaps in older data |
| Constituent OHLCV, F&O bhavcopy, OI | NSE archives / broker API / vendor | Planned | Automated bulk download from NSE may be restricted; use a licensed feed for production |
| Intraday candles | Broker API (e.g. Kite Connect ₹500/mo data tier) or vendor | Planned | INTRADAY research is **refused** until intraday bars are loaded |
| Point-in-time index constituents | NSE Indices press releases / vendor | Planned | Needed to remove survivorship bias in constituent research |
| Corporate actions | NSE/BSE corporate actions | Adjustment function implemented | `apply_corporate_actions()`; abnormal moves are flagged for review |
| Fundamentals (results, shareholding, pledging) | NSE/BSE XBRL filings | Planned | Agents report "fundamentals not assessed" until loaded |
| Index valuation (P/E, P/B) | niftyindices.com | Context interface ready | Pass as a `ContextBlock` with source and as-of date |
| Macro (repo, CPI, GDP, IIP, USD/INR) | RBI DBIE, MoSPI | Planned | |
| US rates, Brent | FRED | Planned | |
| FII/DII flows | NSE / NSDL FPI Monitor | Planned | |
| Option chain, PCR, IV | Broker/vendor feed | Requires licence | Scraping the NSE website for automation is not permitted |
| RBI / SEBI / PIB announcements | Official RSS | **Implemented** | Allow-listed, timestamped, de-duplicated |
| SIMULATED OHLCV | Built-in generator | Implemented | Always labelled; LIVE execution blocked on it |

## Verifying network reachability

These were checked from the build sandbox on 2026-11-07. Yahoo, NSE and Stooq were **unreachable from the
sandbox**, because the sandbox network policy blocks them, so no real data was loaded during the build. All
numbers in `docs/reports/` therefore come from **SIMULATED** data. On your own machine, run:

```bash
python -m triagent.cli ingest --source yahoo --symbol "NIFTY 50"
python -m triagent.cli ingest --source csv --path data/import/NIFTY50_2006_2026.csv --symbol "NIFTY 50"
```

## Per-dataset metadata recorded

Each dataset records the source name, licence, instrument, timeframe, retrieval timestamp, last successful
update, first and last bar, bar count, adjustment status, a simulated flag, and the full quality report: issues,
multi-day gaps, staleness and abnormal moves. News items also record their publication and retrieval timestamps.

## Quality rules (`data/quality.py`)

Missing OHLC values, non-positive prices, OHLC inconsistency, duplicate timestamps, zero volume, abnormal
single-day moves (index >12%, equity >25%), multi-day gaps against the trading calendar, staleness (more than 3
trading days), insufficient history (fewer than 250 bars) and unadjusted prices. The result is a status of
PASS, WARN or FAIL. FAIL or stale data makes the JUDGE choose NO TRADE.

The NSE holiday list is **not bundled**. Add dates to `config/holidays_in_nse.txt`, one per line. Until you do,
1–2 day gaps are reported as "likely holidays" rather than errors.
