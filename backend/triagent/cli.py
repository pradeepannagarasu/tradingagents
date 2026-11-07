"""Command-line entry points.

  python -m triagent.cli ingest --source synthetic|yahoo|csv --symbol "NIFTY 50" [--path file.csv]
  python -m triagent.cli research --symbol "NIFTY 50" --horizon SWING [--as-of 2024-06-28]
  python -m triagent.cli report --symbol "NIFTY 50" --out ../docs/reports/BACKTEST_REPORT.md
  python -m triagent.cli paper-demo
  python -m triagent.cli migrate        # create/verify schema, triggers, hypertable
  python -m triagent.cli demo-setup     # SIMULATED data + one risk-approved decision for a dashboard paper trade
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from triagent.config import get_settings
from triagent.core.models import Horizon
from triagent.data.connectors import CsvConnector, SyntheticConnector, YahooChartConnector
from triagent.execution.orders import CONFIRM_PHRASE
from triagent.pipeline import Services, calibrate_and_validate
from triagent.quant.backtest import BacktestConfig, run_backtest, walk_forward
from triagent.quant.strategies import STRATEGIES, StrategySpec


def _svc() -> Services:
    return Services.create(get_settings())


def cmd_ingest(a):
    svc = _svc()
    conn = {"synthetic": SyntheticConnector(), "yahoo": YahooChartConnector()}.get(a.source) or CsvConnector(a.path)
    ps = conn.fetch(a.symbol, start=a.start)
    q = svc.ingest(ps)
    print(json.dumps({"dataset": ps.meta(), "quality": {k: q[k] for k in ("status", "issues", "gaps", "stale")}},
                     indent=2, default=str))


def cmd_research(a):
    rec = _svc().run_research(a.symbol, Horizon(a.horizon), a.as_of)
    j = rec.judge
    print(f"{rec.final_decision.value}  ({rec.instrument.symbol}, {rec.horizon.value}, data as of {rec.market_data_as_of})")
    print("composite", j.composite_score, "coverage", j.coverage, "simulated", rec.simulated_data)
    for r in j.reasons:
        print(" -", r)
    print("decision id:", rec.id)


def cmd_report(a):
    svc = _svc()
    fc = svc.features(a.symbol)
    _, inst = svc.resolve_instrument(a.symbol)
    rk = svc.settings.section("risk")
    cfg = BacktestConfig(initial_capital=rk["capital"], risk_per_trade_pct=rk["max_risk_per_trade_pct"],
                         slippage_bps=rk["default_slippage_bps"])
    sim = fc.series.simulated
    L = [f"# Backtest & calibration report — {a.symbol}", "",
         f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} from `python -m triagent.cli report`.", "",
         f"**Data:** {fc.series.source} · {fc.series.bars.index[0].date()} → {fc.series.bars.index[-1].date()} · "
         f"{len(fc.series.bars)} bars · licence: {fc.series.licence}", ""]
    if sim:
        L += ["> **SIMULATED DATA.** These numbers validate that the engine runs end-to-end with costs, slippage, "
              "walk-forward and calibration. They say **nothing** about how any strategy performs on the real NIFTY 50. "
              "Re-run this command after loading real data.", ""]
    L += [f"Instrument traded: `{inst.symbol}` (lot {inst.lot_size}); capital ₹{rk['capital']:,.0f}; risk "
          f"{rk['max_risk_per_trade_pct']}%/trade; slippage {rk['default_slippage_bps']} bps/side; NSE cost schedule "
          "from `config/costs_in_nse.toml`.", "", "## Strategy backtests (full period, after all costs)", "",
          "| Strategy | Trades | Win % (95% CI) | Avg R | Profit factor | CAGR | Max DD | Sharpe | Costs ₹ | Net ₹ |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    bench = None
    for name in STRATEGIES:
        r = run_backtest(fc.features, StrategySpec(name), inst, Horizon.SWING, svc.profile, cfg)
        m = r.metrics
        bench = r.benchmark
        L.append(f"| {name} | {m['n_trades']} | {m['win_rate']:.0%} ({m['win_rate_95ci'][0]:.0%}–{m['win_rate_95ci'][1]:.0%}) "
                 f"| {m['avg_r_multiple']:+.3f} | {m['profit_factor']} | {m['cagr']:+.2%} | {m['max_drawdown']:.1%} | "
                 f"{m['sharpe']} | {m['total_costs']:,.0f} | {m['net_profit_after_costs']:,.0f} |")
    L += ["", f"Benchmark — {bench['name']}: total return {bench['total_return']:+.1%}, CAGR {bench['cagr']:+.2%}, "
          f"max DD {bench['max_drawdown']:.1%}.", "", "## Walk-forward (4y train → 1y unseen test, rolling)", "",
          "| Strategy | OOS trades | OOS avg R | OOS win % | OOS net ₹ |", "|---|---:|---:|---:|---:|"]
    for name in STRATEGIES:
        wf = walk_forward(fc.features, StrategySpec(name), {"stop_atr": [1.5, 2.0, 3.0], "target_r": [1.5, 2.0, 3.0]},
                          inst, Horizon.SWING, svc.profile, cfg)
        L.append(f"| {name} | {wf['oos_trades']} | {wf['oos_avg_r']:+.3f} | {wf['oos_win_rate']:.0%} | {wf['oos_net_pnl']:,.0f} |")
    start = str(fc.features.index[260].date())
    split = str(fc.features.index[int(len(fc.features) * 0.6)].date())
    end = str(fc.features.index[-20].date())
    cal = calibrate_and_validate(svc, a.symbol, Horizon.SWING, split, start, end, step=a.step, weight_sets={
        "configured": svc.settings.weights, "equal": {k: 1 / 7 for k in svc.settings.weights},
        "technical_heavy": {**svc.settings.weights, "technical": 0.40, "fundamental": 0.05}})
    L += ["", f"## JUDGE replay: weights & calibration (train {start}→{split}, test {split}→{end}, every {a.step} bars)", "",
          "Composite ≥ threshold counted as a trade signal; outcome = plan hits Target 1 before stop.", "",
          "| Weight set | Train signals | Train avg R | Test signals | Test avg R | Test hit % | Brier (test) | Brier (constant baseline) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, v in cal["weight_sets"].items():
        def f(x):
            return "—" if x is None else f"{x:+.3f}"
        hit = "—" if v["test_hit_rate"] is None else f"{v['test_hit_rate']:.0%}"
        L.append(f"| {k} | {v['train_trades']} | {f(v['train_avg_r'])} | {v['test_trades']} | {f(v['test_avg_r'])} | "
                 f"{hit} | {v.get('brier_test', '—')} | {v.get('brier_baseline_constant', '—')} |")
    rel = cal["weight_sets"]["configured"].get("reliability_test") or []
    if rel:
        L += ["", "Reliability (test period, configured weights):", "", "| Bin | n | Mean predicted | Observed |",
              "|---:|---:|---:|---:|"] + [f"| {r['bin']} | {r['n']} | {r['mean_predicted']:.0%} | {r['observed_rate']:.0%} |"
                                         for r in rel]
    L += ["", f"_{cal['note']}_", "", "## Interpretation rules", "",
          "* A strategy is **not** considered successful because of in-sample results. Only the walk-forward and "
          "test-period rows count, and only with ≥30 trades and a confidence interval that excludes break-even.",
          "* If calibrated Brier is not better than the constant baseline, the composite carries no probabilistic "
          "information and the dashboard must keep showing it as an uncalibrated score.", ""]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L))
    print(f"wrote {out}")


def cmd_paper_demo(a):
    """Scan history for a risk-approved decision and walk it through ticket → CONFIRM → paper fill → audit."""
    svc = _svc()
    rec = None
    for seed in range(40):  # paper trading needs CURRENT data; try simulated series until one is risk-approved
        svc.ingest(SyntheticConnector(seed=seed).fetch("NIFTY 50"))
        for h in (Horizon.SWING, Horizon.POSITIONAL, Horizon.SHORT):
            r = svc.run_research("NIFTY 50", h)
            if r.risk and r.risk.approved:
                rec = r
                break
        if rec:
            break
    if rec is None:
        print("No risk-approved decision found — every signal was rejected (a valid outcome).")
        return
    ts = rec.market_data_as_of
    print(f"Decision {rec.id} (seed {seed}, data as of {ts.date()}): {rec.final_decision.value} {rec.instrument.symbol} "
          f"qty {rec.risk.quantity}, max loss ₹{rec.risk.max_loss_at_stop:,.0f}")
    t = svc.orders.create_ticket(rec.model_dump(mode="json"), "PAPER")
    print("Ticket", t["id"], t["status"])
    done = svc.orders.confirm_and_submit(t["id"], CONFIRM_PHRASE, t["ticket_hash"], actor="paper-demo")
    print("After CONFIRM TRADE:", done["status"], done["broker_order_id"])
    print("Paper positions:", svc.paper.positions())
    print("Audit chain valid:", svc.store.verify_audit_chain())


def cmd_migrate(a):
    """Create/verify the schema (idempotent) and report tables, append-only triggers and the Timescale hypertable."""
    from sqlalchemy import inspect, text
    svc = _svc()
    eng = svc.store.engine
    tables = sorted(inspect(eng).get_table_names())
    print("database:", eng.dialect.name, "| tables:", ", ".join(tables))
    if eng.dialect.name == "postgresql":
        with eng.connect() as c:
            trig = c.execute(text("SELECT tgname FROM pg_trigger WHERE tgname LIKE '%append_only'")).scalars().all()
            print("append-only triggers:", trig)
            try:
                ht = c.execute(text("SELECT hypertable_name FROM timescaledb_information.hypertables")).scalars().all()
                print("timescale hypertables:", ht)
            except Exception:  # noqa: BLE001
                print("timescale hypertables: TimescaleDB extension not available (plain PostgreSQL is fine)")
    print("audit chain valid:", svc.store.verify_audit_chain())
    print("paper-only lock:", svc.settings.paper_only, "| live trading enabled:", svc.settings.live_trading_enabled,
          "| mode:", svc.settings.mode)


def cmd_demo_setup(a):
    """Load a clearly-labelled SIMULATED 'NIFTY 50' series whose CURRENT decision passes the risk engine, so the
    full research → ticket → CONFIRM TRADE → paper fill workflow can be exercised from the dashboard.
    Replaces any loaded NIFTY 50 dataset. Places NO orders."""
    svc = _svc()
    for seed in range(a.max_seeds):
        svc.ingest(SyntheticConnector(seed=seed).fetch("NIFTY 50"))
        for h in (Horizon.SWING, Horizon.POSITIONAL, Horizon.SHORT):
            r = svc.run_research("NIFTY 50", h, persist=False)
            if r.risk and r.risk.approved:
                rec = svc.run_research("NIFTY 50", h)  # persisted decision for the dashboard
                print(f"SIMULATED series loaded (seed {seed}). Decision {rec.id}: {rec.final_decision.value} "
                      f"{rec.instrument.symbol} {h.value}, data as of {rec.market_data_as_of:%Y-%m-%d}, "
                      f"risk APPROVED qty {rec.risk.quantity}.")
                print("Open the dashboard → History → click this decision → 'Prepare order ticket…' → type CONFIRM TRADE.")
                print("The risk approval expires 15 minutes after the decision; if it expires, re-run this command.")
                return
    print(f"No risk-approved decision in {a.max_seeds} simulated seeds — every signal was rejected (valid outcome).")


def main():
    p = argparse.ArgumentParser(prog="triagent")
    sp = p.add_subparsers(required=True)
    i = sp.add_parser("ingest")
    i.add_argument("--source", choices=["synthetic", "yahoo", "csv"], required=True)
    i.add_argument("--symbol", default="NIFTY 50")
    i.add_argument("--path")
    i.add_argument("--start", default="2006-01-01")
    i.set_defaults(fn=cmd_ingest)
    r = sp.add_parser("research")
    r.add_argument("--symbol", default="NIFTY 50")
    r.add_argument("--horizon", default="SWING", choices=[h.value for h in Horizon])
    r.add_argument("--as-of", dest="as_of")
    r.set_defaults(fn=cmd_research)
    rp = sp.add_parser("report")
    rp.add_argument("--symbol", default="NIFTY 50")
    rp.add_argument("--out", default="../docs/reports/BACKTEST_REPORT.md")
    rp.add_argument("--step", type=int, default=5)
    rp.set_defaults(fn=cmd_report)
    sp.add_parser("paper-demo").set_defaults(fn=cmd_paper_demo)
    sp.add_parser("migrate").set_defaults(fn=cmd_migrate)
    ds = sp.add_parser("demo-setup")
    ds.add_argument("--max-seeds", type=int, default=40)
    ds.set_defaults(fn=cmd_demo_setup)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
