from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from triagent.history.outcomes import evaluate_decision, scorecard, simulate_plan
from triagent.news.service import NewsItem, classify, deduplicate, parse_rss, point_in_time
from triagent.notify.notifier import IncompleteNotification, Notifier, format_decision, render_text

T0 = datetime(2025, 6, 1, 10, tzinfo=timezone.utc)


def test_news_classification_and_verification():
    official = classify(NewsItem("RBI", "RBI announces repo rate cut of 25 bps; growth accelerating", "u", T0))
    assert official.sentiment == 1 and official.verified and "MACRO" in official.entities
    rumour = classify(NewsItem("Twitter", "Infosys reportedly in merger talks, sources said", "u", T0))
    assert rumour.speculative and not rumour.verified and "INFY" in rumour.entities
    media_spec = classify(NewsItem("Economic Times", "HDFC Bank profit may fall, analysts say", "u", T0))
    assert not media_spec.verified


def test_dedup_keeps_earliest_and_pit_filter():
    a = classify(NewsItem("PTI", "Reliance Industries Q1 profit rises 12 percent on retail growth", "a", T0))
    b = classify(NewsItem("Economic Times", "Reliance Industries Q1 profit rises 12 percent on retail growth: report", "b",
                          T0 + timedelta(hours=1)))
    c = classify(NewsItem("RBI", "RBI keeps repo unchanged", "c", T0 + timedelta(days=2)))
    items = deduplicate([b, a, c])
    assert b.duplicate_of == a.id and a.duplicate_of is None
    assert point_in_time(items, T0 + timedelta(days=1)) == [a]


def test_rss_parsing_requires_timestamps():
    xml = """<rss><channel>
      <item><title>SEBI issues circular on algo trading</title><link>https://sebi.gov.in/x</link>
            <pubDate>Tue, 04 Feb 2025 18:00:00 +0530</pubDate><description>&lt;p&gt;text&lt;/p&gt;</description></item>
      <item><title>No date item</title><link>https://sebi.gov.in/y</link></item>
    </channel></rss>"""
    items = parse_rss(xml, "SEBI")
    assert len(items) == 1 and items[0].trust_tier == "official"
    assert items[0].published_at.utcoffset() == timedelta(hours=5, minutes=30)


RECORD = {"id": "abc", "final_decision": "BUY / LONG", "horizon": "SWING", "simulated_data": True,
          "instrument": {"symbol": "NIFTY-FUT", "exchange": "NSE"},
          "judge": {"reasons": ["r"], "plan": {"entry_low": 99, "entry_high": 101, "stop_loss": 95, "target_1": 110,
                                               "target_2": 115, "risk_reward": 2.0, "direction": "LONG"}},
          "risk": {"approved": True, "max_loss_at_stop": 5000},
          "bull": {"plan": {"entry_low": 99, "entry_high": 101, "stop_loss": 95, "target_1": 110, "direction": "LONG"}},
          "bear": {"plan": None}}


def test_notification_contains_full_context_and_refuses_bare_signal():
    text = render_text(format_decision(RECORD, "http://x"))
    for s in ("BUY / LONG", "NIFTY-FUT", "SWING", "Stop-loss: 95", "T1 110", "max loss", "http://x/decisions/abc",
              "SIMULATED"):
        assert s in text
    with pytest.raises(IncompleteNotification):
        render_text({"decision": "BUY", "instrument": "X"})
    n = Notifier({"in_app_enabled": True, "telegram_enabled": True})
    res = n.send_decision(RECORD, "http://x")
    assert res["in_app"] == "ok" and res["telegram"].startswith("skipped")


def _future(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range("2025-06-02", periods=len(rows)))


def test_simulate_plan_target_and_stop():
    p = RECORD["judge"]["plan"]
    win = simulate_plan(p, _future([[100, 102, 98, 101], [101, 111, 100, 110]]), 10)
    assert win["result"] == "target_1" and win["r_multiple"] == pytest.approx(2.0)
    loss = simulate_plan(p, _future([[100, 102, 98, 101], [99, 100, 90, 92]]), 10)
    assert loss["result"] == "stop" and loss["r_multiple"] == pytest.approx(-1.0)
    gap = simulate_plan(p, _future([[100, 102, 98, 101], [90, 91, 88, 89]]), 10)
    assert gap["r_multiple"] < -1  # gap through stop is worse than −1R
    assert simulate_plan(p, _future([[120, 125, 119, 121]] * 3), 10)["result"] == "not_triggered"


def test_no_trade_credited_for_avoiding_loss():
    rec = {**RECORD, "final_decision": "NO TRADE", "judge": {"plan": None, "reasons": []}}
    out = evaluate_decision(rec, _future([[100, 102, 98, 101], [99, 100, 90, 92]]), 10)
    assert out["no_trade_assessment"] == "avoided_loss"
    out2 = evaluate_decision(rec, _future([[100, 102, 98, 101], [101, 111, 100, 110]]), 10)
    assert out2["no_trade_assessment"] == "missed_opportunity"
    sc = scorecard([out, out2])
    assert sc["NO_TRADE"] == {"n": 2, "avoided_loss": 1, "missed_opportunity": 1, "neutral": 0}
    assert sc["BULL"]["n"] == 2 and sc["BULL"]["sample_warning"]
