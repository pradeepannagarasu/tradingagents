# API

Interactive OpenAPI docs: `http://localhost:8000/docs`. All bodies are JSON.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | liveness |
| `GET /api/system` | mode, live flags, kill-switch state, weights, thresholds, risk limits, which credentials are present (booleans) |
| `GET /api/data/inventory` | source inventory + loaded datasets with coverage and quality |
| `POST /api/data/ingest` `{source: synthetic\|yahoo\|csv, symbol, path?, start?, instrument_type?, adjusted?}` | load data; returns metadata and quality report. Source failure → 502, never substituted |
| `GET /api/data/quality/{symbol}` | stored quality report |
| `GET /api/market/bars/{symbol}?limit=` | OHLCV and key indicators, regime |
| `GET /api/market/overview` | NIFTY/VIX snapshot, explicit list of unavailable feeds, events, news |
| `POST /api/research/run` `{symbol, horizon: SHORT\|SWING\|POSITIONAL\|INTRADAY, as_of?}` | full BULL/BEAR/debate/JUDGE/risk run. Returns an immutable DecisionRecord. INTRADAY → 400 until intraday data exists |
| `GET /api/decisions`, `GET /api/decisions/{id}` | signal history / full record |
| `POST /api/outcomes/evaluate/{symbol}` | evaluate past decisions against later prices (MFE/MAE, target/stop, NO-TRADE assessment) and update the scorecard |
| `GET /api/scorecard` | per-agent expectancy, win rate with 95% CI, avg loss, drawdown in R, by horizon/instrument; NO-TRADE avoided/missed |
| `POST /api/backtest` `{symbol, strategy, params, stop_atr, target_r, max_hold_bars, start?, end?, walk_forward?}` | backtest after costs + optional walk-forward |
| `POST /api/calibration` `{symbol, horizon, start, split, end, step}` | point-in-time replay of the judge; weight-set comparison; isotonic calibration with test Brier and reliability |
| `POST /api/orders/tickets` `{decision_id}` | create an order ticket from a risk-approved, non-stale decision. Returns `ticket_hash` and `confirm_phrase` |
| `POST /api/orders/tickets/{id}/confirm` `{confirmation: "CONFIRM TRADE", ticket_hash}` | the **only** path to submission. 403 on wrong phrase, changed ticket, invalid/expired risk token, halted trading or disabled live |
| `POST /api/orders/tickets/{id}/reject` · `/reconcile` | user rejection; broker reconciliation (never resubmits) |
| `GET /api/orders` | tickets, paper positions, realised paper P&L |
| `POST /api/emergency/stop-all` · `cancel-all` · `disable-live` · `resume` `{reason}` | emergency controls (audited) |
| `GET /api/audit` | hash-chained audit log + chain verification |
| `GET /api/risk/events` | every risk assessment incl. rejections |
| `GET /api/notifications` | in-app notifications |
| `POST /api/news/refresh` | fetch RBI/SEBI/PIB RSS (errors reported, nothing fabricated) |
| `WS /ws/updates` | push events: decision, order, emergency |
