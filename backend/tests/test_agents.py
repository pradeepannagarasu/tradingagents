from datetime import datetime, timedelta, timezone

import pytest

from triagent.agents.calibration import IsotonicCalibrator, brier
from triagent.agents.debate import run_debate
from triagent.agents.evidence import extract_all
from triagent.agents.judge import judge
from triagent.agents.llm import LLMProvider, validate_narrative
from triagent.agents.researchers import run_bear, run_bull
from triagent.agents.snapshot import build_snapshot
from triagent.core.models import (Decision, DataQualityReport, Direction, Horizon, Instrument, InstrumentType,
                                  QualityStatus, SourceRef)
from triagent.data.quality import check_price_series
from triagent.news.service import NewsItem, classify
from triagent.quant.indicators import compute_all

W = {"technical": .2, "fundamental": .15, "macro_regime": .15, "news_events": .1, "derivatives": .1,
     "historical": .15, "risk_reward": .15}
TH = {"min_composite_for_trade": 0.25, "min_category_coverage": 0.45, "min_risk_reward": 1.5, "min_history_bars": 250}
EQ = Instrument(symbol="ABC", instrument_type=InstrumentType.EQUITY)


def snap_for(profile, ps, inst=EQ, horizon=Horizon.SWING, quality=None, **kw):
    q = quality or check_price_series(ps, profile, {"min_bars_required": 250}, InstrumentType.EQUITY)
    return build_snapshot(compute_all(ps.bars), inst, inst, horizon, profile, q,
                          SourceRef(source=ps.source, simulated=ps.simulated), **kw)


def run_all(snap):
    ev = extract_all(snap)
    bull = run_bull(snap, ev)
    bear = run_bear(snap, bull, ev)
    summary = run_debate(bull, bear)
    return bull, bear, summary, judge(snap, bull, bear, summary, W, TH)


def test_bull_and_bear_split_evidence_by_direction(profile, series_factory):
    snap = snap_for(profile, series_factory(n=900))
    ev = extract_all(snap)
    bull = run_bull(snap, ev)
    bear = run_bear(snap, bull, ev)
    assert all(e.direction > 0 for e in bull.evidence)
    assert all(e.direction < 0 for e in bear.evidence)
    assert all(e.direction < 0 for e in bull.acknowledged_weaknesses)  # BULL lists its own weaknesses
    assert bull.prompt_version.startswith("bull-v")


def test_every_evidence_item_has_source_and_provenance(profile, series_factory):
    for e in extract_all(snap_for(profile, series_factory(n=900))):
        assert e.source is not None and e.provenance is not None and e.as_of is not None


def test_missing_datasets_are_reported_not_invented(profile, series_factory):
    ev = {e.id: e for e in extract_all(snap_for(profile, series_factory(n=900)))}
    for k in ("FUND.UNAVAILABLE", "DERIV.UNAVAILABLE", "MACRO.UNAVAILABLE"):
        assert ev[k].provenance.value == "unverified_or_unavailable" and ev[k].direction == 0


def test_judge_can_only_output_three_decisions_and_can_reject_both(profile, series_factory):
    seen = set()
    for seed in range(6):
        _, _, _, jr = run_all(snap_for(profile, series_factory(n=900, seed=seed, drift=0.0)))
        seen.add(jr.decision)
        assert jr.decision in (Decision.BUY, Decision.SELL, Decision.NO_TRADE)
    assert Decision.NO_TRADE in seen


def test_strong_uptrend_bear_concedes_or_judge_buys(profile, series_factory):
    snap = snap_for(profile, series_factory(n=900, drift=0.003, vol=0.006, seed=3))
    bull, bear, _, jr = run_all(snap)
    assert bull.conviction > bear.conviction
    assert jr.composite_score > 0


def test_judge_rejects_tampered_agent_claims(profile, series_factory):
    snap = snap_for(profile, series_factory(n=900))
    ev = extract_all(snap)
    bull = run_bull(snap, ev)
    bear = run_bear(snap, bull, ev)
    if not bull.evidence:
        pytest.skip("no bullish evidence in fixture")
    bull.evidence[0] = bull.evidence[0].model_copy(update={"value": 999999.0})
    bull.evidence.append(bull.evidence[0].model_copy(update={"id": "FAKE.NEWS", "claim": "Invented catalyst"}))
    jr = judge(snap, bull, bear, [], W, TH)
    assert any("FAKE.NEWS" in r for r in jr.rejected_arguments)
    assert any("999999" in r for r in jr.rejected_arguments)


def test_failed_data_quality_forces_no_trade(profile, series_factory):
    ps = series_factory(n=900, drift=0.003, vol=0.006, seed=3)
    bad_q = DataQualityReport(instrument="ABC", status=QualityStatus.FAIL, source="t", simulated=True,
                              first_bar=None, last_bar=None, n_bars=900, issues=["broken"], gaps=[], stale=False,
                              checked_at=datetime.now(timezone.utc))
    _, _, _, jr = run_all(snap_for(profile, ps, quality=bad_q))
    assert jr.decision == Decision.NO_TRADE
    assert not next(g for g in jr.gates if g.name == "data_quality").passed


def test_stale_data_forces_no_trade(profile, series_factory):
    ps = series_factory(n=900, drift=0.003, vol=0.006, seed=3)
    q = check_price_series(ps, profile, {"min_bars_required": 250}, InstrumentType.EQUITY)
    q = q.model_copy(update={"stale": True})
    _, _, _, jr = run_all(snap_for(profile, ps, quality=q))
    assert jr.decision == Decision.NO_TRADE


def test_future_news_is_excluded_from_snapshot(profile, series_factory):
    ps = series_factory(n=900)
    future = classify(NewsItem(source="RBI", title="RBI cuts repo rate", url="https://rbi.org.in/x",
                               published_at=datetime.now(timezone.utc) + timedelta(days=3)))
    snap = snap_for(profile, ps, news=[future])
    assert snap.news == []


def test_index_is_not_tradable_and_short_rules(profile, series_factory):
    idx_inst = Instrument(symbol="NIFTY 50", instrument_type=InstrumentType.INDEX)
    snap = snap_for(profile, series_factory(n=900), inst=idx_inst)
    _, _, _, jr = run_all(snap)
    assert jr.decision == Decision.NO_TRADE
    assert not next(g for g in jr.gates if g.name == "tradable_instrument").passed


def test_debate_is_bounded_and_never_repeats(profile, series_factory):
    snap = snap_for(profile, series_factory(n=900, seed=5))
    bull, bear, summary, _ = run_all(snap)
    for a in (bull, bear):
        targets = [r.target_evidence_id for r in a.rebuttals if r.target_evidence_id != "BEAR.PLAN_CHALLENGE"]
        assert len(targets) == len(set(targets))
        assert all(r.round <= 2 for r in a.rebuttals)


def test_trade_decisions_always_have_invalidation(profile):
    from tests.conftest import make_series
    trades = 0
    for seed in range(8):
        _, _, _, jr = run_all(snap_for(profile, make_series(n=900, seed=seed, drift=0.002, vol=0.008)))
        if jr.decision != Decision.NO_TRADE:
            trades += 1
            assert jr.plan.stop_loss is not None and jr.plan.invalidation
            assert jr.plan.risk_reward >= TH["min_risk_reward"]
    assert trades > 0  # the check above is not vacuous


def test_llm_validator_blocks_fabrication():
    ids = {"TECH.RSI"}
    ev_text = '{"value": 61.2, "claim": "RSI 61.2"}'
    assert validate_narrative({"thesis": "RSI is 61.2 [TECH.RSI]", "cited_ids": ["TECH.RSI"]}, ids, ev_text)[0]
    assert not validate_narrative({"thesis": "Target 25000", "cited_ids": ["TECH.RSI"]}, ids, ev_text)[0]
    assert not validate_narrative({"thesis": "ok", "cited_ids": ["NEWS.FAKE"]}, ids, ev_text)[0]
    assert not validate_narrative(None, ids, ev_text)[0]


class _FabricatingLLM(LLMProvider):
    name = "fake"

    def complete_json(self, system, user, model, temperature=0.0):
        return {"thesis": "Guaranteed 47% upside to 98765!", "cited_ids": []}


def test_fabricating_llm_output_is_discarded(profile, series_factory):
    snap = snap_for(profile, series_factory(n=900))
    rep = run_bull(snap, extract_all(snap), _FabricatingLLM(), "m")
    assert rep.narrative_source == "template"
    assert "98765" not in rep.thesis
    assert any("rejected by validator" in c for c in rep.challenges_to_other)


def test_calibrator_monotone_and_brier():
    cal = IsotonicCalibrator.fit([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [0, 1, 0, 1, 1, 1])
    ps = [cal.predict(x) for x in (0.1, 0.3, 0.6)]
    assert ps == sorted(ps)
    assert brier([1, 0], [1, 0]) == 0.0
