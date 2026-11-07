# Architecture

## Principles

1. **Deterministic numbers, LLM words.** Every indicator, level, size, cost and score comes from Python code
   with tests. Language models (optional) may only *phrase* narratives over evidence that already exists, and
   their output is rejected if it cites unknown evidence or introduces numbers that aren't in it.
2. **Point-in-time by construction.** Indicators are causal (tested), snapshots are sliced to the decision
   bar, news after the snapshot is excluded, data-quality is evaluated on the data available *then*, and
   historical analogs only use setups whose outcome was already known.
3. **Fail closed.** Missing data → explicit "unavailable" evidence, not guesses. Missing risk approval → no order.
   Missing quote in LIVE → reject. Unknown broker outcome → reconcile, never re-send.
4. **Market-agnostic core.** India/NSE specifics (sessions, STT/stamp duty, lot sizes, expiry weekday, shorting
   rules, index proxies) live in `markets/india_nse.py` + `config/costs_in_nse.toml`. A new exchange = a new
   `MarketProfile`.

## Flow of one decision

```
 PriceSeries (source, licence, retrieved_at, adjusted?, simulated?)
      │  data/quality.py  → DataQualityReport (gaps, OHLC errors, staleness, abnormal moves)
      ▼
 quant/indicators.compute_all  (causal features)   quant/regime  (trend, vol regime, S/R)   quant/analogs
      ▼
 agents/snapshot.build_snapshot  ── immutable, hashed (SHA-256) MarketSnapshot at time t
      ▼
 agents/evidence.extract_all  → EvidenceItem[] {id, category, metric, value, direction, strength, provenance, source}
      ├──► BULL  (agents/researchers.run_bull)  supporting evidence, own weaknesses, long plan, invalidation
      └──► BEAR  (run_bear)  counter-evidence, measured plan challenges, short / STAY OUT, concession
      ▼
 agents/debate.run_debate  ≤ 2 rounds, ≤ 3 points/side/round, no point argued twice
      ▼
 JUDGE (agents/judge.judge)
   1. data gates (quality, freshness, timestamps, PIT news, history, tradable instrument)
   2. recompute evidence independently; reject agent claims that don't reproduce
   3. tanh-bounded category scores from verified evidence × configurable weights
   4. risk/reward from competing plans; agent track record (sample-size-shrunk expectancy)
   5. contradiction + coverage gates;  6. decision ∈ {BUY, SELL, NO TRADE} + explanation
      ▼
 risk/engine.RiskEngine.assess  (independent; HMAC-signed, expiring approval token)
      ▼
 DecisionRecord (append-only DB row, snapshot hash, all reports)  →  notifications (full context only)
      ▼  (human)  POST ticket → review exact ticket → type CONFIRM TRADE (hash-bound)
 execution/orders.OrderManager  → PaperBroker | LiveGuard(KiteAdapter)  → reconcile  → hash-chained audit log
```

## Folder structure

```
config/                 settings.toml (weights, thresholds, risk limits), costs_in_nse.toml (effective-dated)
backend/
  triagent/
    config.py           settings loader (env > local.toml > settings.toml); secrets only from env
    core/models.py      domain models (Instrument, EvidenceItem, TradePlan, AgentReport, JudgeReport, DecisionRecord…)
    markets/            MarketProfile ABC + IndiaNSEProfile (sessions, costs, shorting, proxies, expiry)
    data/               connectors (CSV, Yahoo-unofficial, SIMULATED), quality.py, inventory.py, store.py
    quant/              indicators, regime & S/R, strategies, backtest (+walk-forward), metrics, analogs
    news/service.py     allow-listed RSS ingestion, dedup, entity tagging, sentiment, speculation flag, PIT filter
    agents/             snapshot, evidence, plans, researchers (BULL/BEAR), debate, judge, calibration, llm, prompts/
    risk/               engine (checks, sizing, signed tokens), kill_switch (persisted emergency state)
    execution/          broker_base, paper_broker, orders (approval workflow, LiveGuard), brokers/zerodha.py
    notify/             message formatter (refuses bare signals), Telegram, email, in-app
    history/outcomes.py plan simulation, MFE/MAE, NO-TRADE evaluation, agent scorecards
    pipeline.py         Services container, run_research, replay, calibrate_and_validate
    api/main.py         FastAPI + WebSocket
    cli.py              ingest / research / report / paper-demo
  migrations/001_init.sql   PostgreSQL/TimescaleDB schema + append-only triggers
  tests/                88 tests
frontend/               React + TypeScript + Vite dashboard (TradingView Lightweight Charts, Apache-2.0)
docs/                   this documentation
```

## Storage

* SQLite by default (zero-setup); PostgreSQL + TimescaleDB via Docker (`price_bars` is a hypertable).
* `decision_records`, `audit_log`, `risk_events` reject UPDATE/DELETE via triggers (tested on SQLite and PostgreSQL 16).
* The audit log is hash-chained (`prev_hash` → `hash`); `GET /api/audit` verifies the chain.
* Redis is **not** used yet — the MVP has no background queue. Add it when scheduled ingestion/streaming lands.

## Scoring model

`composite = Σ wᶜ · tanh(Σ direction·strength over verified items in c) / Σ w`, in [−1, 1]. Missing categories
contribute 0 and reduce *coverage*. The composite is **not a probability**; a probability is shown only when an
isotonic calibrator fitted on a training period exists, with its out-of-sample Brier score next to a constant
baseline (see `docs/reports/BACKTEST_REPORT.md`).
