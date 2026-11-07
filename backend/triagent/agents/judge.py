"""JUDGE — independent decision engine.

The judge does not trust either agent. It:
  1. validates the data (freshness, quality, timestamp consistency, point-in-time news),
  2. independently recomputes the evidence from the snapshot and verifies every agent claim
     (id, value and direction must match its own recomputation; mismatches are rejected),
  3. scores categories from VERIFIED evidence only, with configurable weights,
  4. scores risk/reward from the competing plans, and adjusts the historical category by each
     agent's sample-size-shrunk track record (not raw win rate),
  5. applies hard gates (any failure → NO TRADE regardless of score),
  6. explains the decision.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from triagent.agents.evidence import extract_all
from triagent.agents.snapshot import MarketSnapshot
from triagent.core.models import (AgentReport, Category, CategoryScore, Decision, Direction, EvidenceItem, GateResult,
                                  JudgeReport, QualityStatus)


def _values_match(a, b) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)
    return str(a) == str(b)


def verify_claims(agent: AgentReport, reference: dict[str, EvidenceItem]) -> tuple[list[EvidenceItem], list[str]]:
    ok, rejected = [], []
    for e in agent.evidence:
        ref = reference.get(e.id)
        if ref is None:
            rejected.append(f"{agent.agent}[{e.id}] rejected: judge could not reproduce this evidence from the snapshot.")
        elif ref.direction != e.direction or not _values_match(ref.value, e.value):
            rejected.append(f"{agent.agent}[{e.id}] rejected: claimed value/direction ({e.value}, {e.direction}) "
                            f"≠ recomputed ({ref.value}, {ref.direction}).")
        else:
            ok.append(ref)  # use the judge's own copy, not the agent's
    return ok, rejected


def shrunk_expectancy(stats: dict | None, k: int = 30) -> tuple[float, str]:
    """Expectancy in R shrunk toward 0 by sample size: E * n / (n + k)."""
    if not stats or not stats.get("n"):
        return 0.0, "no track record"
    n, e = stats["n"], stats.get("avg_r", 0.0)
    return e * n / (n + k), f"n={n}, avg R {e:+.2f}, shrunk {e * n / (n + k):+.2f}"


def judge(snap: MarketSnapshot, bull: AgentReport, bear: AgentReport, debate_summary: list[str],
          weights: dict[str, float], thresholds: dict, scorecard: dict | None = None,
          calibrator=None, decision_time: datetime | None = None) -> JudgeReport:
    decision_time = decision_time or datetime.now(timezone.utc)
    reference = {e.id: e for e in extract_all(snap)}

    # ---------------------------------------------------------------- 1. data gates
    gates: list[GateResult] = []
    q = snap.quality
    gates.append(GateResult(name="data_quality", passed=q.status != QualityStatus.FAIL,
                            detail=f"{q.status.value}: " + ("; ".join(q.issues[:3]) or "no issues")))
    gates.append(GateResult(name="data_freshness", passed=not q.stale,
                            detail=f"last bar {q.last_bar}" + (" — STALE" if q.stale else "")))
    gates.append(GateResult(name="timestamp_consistency", passed=snap.as_of <= decision_time,
                            detail=f"market data as of {snap.as_of.isoformat()} ≤ decision {decision_time.isoformat()}"))
    leaked = [n for n in snap.news if n.published_at > snap.as_of]
    gates.append(GateResult(name="point_in_time_news", passed=not leaked,
                            detail=f"{len(leaked)} news items published after the snapshot" if leaked else "all news ≤ snapshot time"))
    min_hist = int(thresholds.get("min_history_bars", 250))
    gates.append(GateResult(name="sufficient_history", passed=len(snap.features) >= min_hist,
                            detail=f"{len(snap.features)} bars (min {min_hist})"))
    gates.append(GateResult(name="tradable_instrument", passed=snap.instrument.tradable,
                            detail=f"{snap.instrument.symbol} ({snap.instrument.instrument_type.value})"))
    if q.simulated:
        gates.append(GateResult(name="simulated_data_notice", passed=True,
                                detail="SIMULATED data — decision is a demonstration only; LIVE execution is blocked."))

    # ---------------------------------------------------------------- 2. verify claims
    bull_ok, bull_rej = verify_claims(bull, reference)
    bear_ok, bear_rej = verify_claims(bear, reference)
    verified = {e.id: e for e in bull_ok + bear_ok}
    # Judge also considers evidence neither agent cited (independent review)
    uncited = [e for e in reference.values() if e.id not in verified and e.direction != 0]
    for e in uncited:
        verified[e.id] = e

    # ---------------------------------------------------------------- 3. category scores
    scores: dict[Category, CategoryScore] = {}
    total_w = sum(weights.values()) or 1.0
    for cat in Category:
        w = float(weights.get(cat.value, 0.0))
        items = [e for e in verified.values() if e.category == cat and e.direction != 0]
        unavailable = any(e.category == cat and e.provenance.value == "unverified_or_unavailable"
                          and e.metric is None for e in reference.values())
        raw = sum(e.direction * e.strength for e in items)
        has = bool(items) or (not unavailable and any(e.category == cat for e in reference.values()))
        rejected_n = sum(1 for e in bull.evidence + bear.evidence
                         if e.category == cat and e.id not in {x.id for x in bull_ok + bear_ok})
        scores[cat] = CategoryScore(category=cat, weight=w, score=round(math.tanh(raw), 4), has_evidence=has,
                                    verified_items=len(items), rejected_items=rejected_n)

    # risk/reward category: from the competing plans
    min_rr = float(thresholds.get("min_risk_reward", 1.5))
    bull_rr = bull.plan.risk_reward if bull.plan and bull.plan.risk_reward else None
    bear_rr = bear.plan.risk_reward if bear.plan and bear.plan.risk_reward else None
    rr_score = 0.0
    if bull_rr is not None and bull_rr >= min_rr:
        rr_score += min(1.0, (bull_rr - 1) / 2)
    if bear_rr is not None and bear_rr >= min_rr:
        rr_score -= min(1.0, (bear_rr - 1) / 2)
    scores[Category.RISK_REWARD] = CategoryScore(category=Category.RISK_REWARD,
                                                 weight=float(weights.get("risk_reward", 0)), score=round(rr_score, 4),
                                                 has_evidence=bull_rr is not None or bear_rr is not None,
                                                 verified_items=int(bull_rr is not None) + int(bear_rr is not None),
                                                 rejected_items=0)
    # historical category: add agents' shrunk track record
    sc = scorecard or {}
    bull_e, bull_note = shrunk_expectancy(sc.get("BULL"))
    bear_e, bear_note = shrunk_expectancy(sc.get("BEAR"))
    h = scores[Category.HISTORICAL]
    h.score = round(max(-1.0, min(1.0, h.score + 0.3 * math.tanh(bull_e) - 0.3 * math.tanh(bear_e))), 4)

    composite = sum(s.weight * s.score for s in scores.values()) / total_w
    coverage = sum(s.weight for s in scores.values() if s.has_evidence) / total_w

    # ---------------------------------------------------------------- 4. contradiction & evidence gates
    strong_pos = [s.category.value for s in scores.values() if s.score > 0.4 and s.weight >= 0.1]
    strong_neg = [s.category.value for s in scores.values() if s.score < -0.4 and s.weight >= 0.1]
    contradictory = len(strong_pos) >= 2 and len(strong_neg) >= 2
    gates.append(GateResult(name="evidence_not_contradictory", passed=not contradictory,
                            detail=f"strong bullish: {strong_pos or '—'}; strong bearish: {strong_neg or '—'}"))
    min_cov = float(thresholds.get("min_category_coverage", 0.45))
    gates.append(GateResult(name="evidence_coverage", passed=coverage >= min_cov,
                            detail=f"{coverage:.0%} of weighted categories have usable evidence (min {min_cov:.0%})"))

    # ---------------------------------------------------------------- 5. decision
    thr = float(thresholds.get("min_composite_for_trade", 0.25))
    gates_ok = all(g.passed for g in gates)
    decision, side, plan, reasons = Decision.NO_TRADE, "NEITHER", None, []
    if not gates_ok:
        failed = [g.name for g in gates if not g.passed]
        reasons.append(f"Hard gate(s) failed: {', '.join(failed)} → NO TRADE regardless of score.")
    elif composite >= thr:
        if bull.plan and bull_rr is not None and bull_rr >= min_rr and bull.plan.stop_loss is not None:
            decision, side, plan = Decision.BUY, "BULL", bull.plan
            reasons.append(f"Composite {composite:+.3f} ≥ +{thr} and long plan R:R {bull_rr} ≥ {min_rr}.")
        else:
            side = "BULL"
            reasons.append(f"Evidence leans bullish ({composite:+.3f}) but the long plan fails R:R/invalidation "
                           f"requirements (R:R {bull_rr}) → NO TRADE.")
    elif composite <= -thr:
        if not snap.short_allowed:
            side = "BEAR"
            reasons.append(f"Evidence leans bearish ({composite:+.3f}); short not permitted ({snap.short_reason}). "
                           f"BEAR's STAY OUT recommendation accepted → NO TRADE.")
        elif bear.plan and bear_rr is not None and bear_rr >= min_rr and bear.plan.stop_loss is not None:
            decision, side, plan = Decision.SELL, "BEAR", bear.plan
            reasons.append(f"Composite {composite:+.3f} ≤ −{thr} and short plan R:R {bear_rr} ≥ {min_rr}.")
        else:
            side = "BEAR"
            reasons.append(f"Evidence leans bearish ({composite:+.3f}) but no short plan meets R:R {min_rr} → NO TRADE.")
    else:
        reasons.append(f"Composite {composite:+.3f} inside the no-trade band (±{thr}): evidence is not decisive. "
                       "Both agents rejected.")

    # ---------------------------------------------------------------- 6. explanation
    direction_sign = 1 if decision == Decision.BUY else (-1 if decision == Decision.SELL else (
        1 if composite > 0 else -1))
    aligned = sorted([e for e in verified.values() if e.direction == direction_sign], key=lambda e: -e.strength)
    opposed = sorted([e for e in verified.values() if e.direction == -direction_sign], key=lambda e: -e.strength)
    strongest = [f"[{e.id}] {e.claim} (strength {e.strength})" for e in aligned[:4]]
    rejected = bull_rej + bear_rej
    if decision != Decision.NO_TRADE:
        rejected += [f"[{e.id}] outweighed: {e.claim}" for e in opposed[:3]]
    concessions = [r.argument for a in (bull, bear) for r in a.rebuttals if r.response == "conceded"]

    L = snap.latest
    key_points = [
        f"Close {L.get('close')} as of {snap.as_of.date()} (source: {snap.price_source.source})",
        f"ATR(14) {L.get('atr_14')}, RSI(14) {L.get('rsi_14')}, ADX {L.get('adx')}",
        f"Trend {snap.trend}; regime {snap.regime}; volatility {snap.vol_regime}",
        f"Weighted composite {composite:+.3f} (not a probability); coverage {coverage:.0%}",
    ]
    if snap.analogs.get("available"):
        a = snap.analogs
        key_points.append(f"Analogs: {a['pct_positive']:.0%} positive over {a['hold_bars']} bars (n={a['n']})")
    key_points.append(f"Agent track record — BULL: {bull_note}; BEAR: {bear_note}")

    unresolved = [f"[{e.id}] {e.claim}" for e in opposed[:3]]
    unresolved += [e.claim for e in reference.values() if e.id.startswith("EVENT.")]
    unresolved += [e.claim for e in reference.values() if e.provenance.value == "unverified_or_unavailable"
                   and e.metric is None][:4]
    unresolved += concessions[:3]

    change = []
    if decision == Decision.NO_TRADE:
        change.append(f"Composite moving beyond ±{thr} with a plan meeting R:R ≥ {min_rr} and all gates passing.")
        change += [f"Gate '{g.name}' passing" for g in gates if not g.passed]
    if plan is not None:
        change += plan.invalidation
    change.append("Material new verified news or a scheduled event outcome.")

    disagreement = round(min(bull.conviction, bear.conviction) / max(bull.conviction, bear.conviction, 1e-9), 3)

    calibrated, cal_note = None, "No calibration model loaded — score is NOT a probability."
    if calibrator is not None and decision != Decision.NO_TRADE:
        calibrated = round(float(calibrator.predict(composite if decision == Decision.BUY else -composite)), 3)
        cal_note = calibrator.describe()

    return JudgeReport(
        decision=decision, composite_score=round(composite, 4), category_scores=list(scores.values()),
        coverage=round(coverage, 3), gates=gates, winning_side=side, reasons=reasons,
        strongest_arguments=strongest, rejected_arguments=rejected, key_data_points=key_points,
        unresolved_risks=unresolved, change_conditions=change, agent_disagreement=disagreement,
        calibrated_probability=calibrated, calibration_note=cal_note, debate_summary=debate_summary, plan=plan,
    )


def plan_direction(plan) -> Direction:
    return plan.direction if plan else Direction.FLAT
