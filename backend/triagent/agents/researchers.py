"""BULL and BEAR research agents.

Both read the same immutable snapshot and the same deterministic evidence set.
Their *perspectives* differ: BULL builds and defends a long plan; BEAR attacks
that plan specifically (late entry, poor R:R, noise-level stops, event risk,
analog failure rates) and proposes a short or STAY OUT — and must concede when
the bullish evidence is genuinely stronger.
"""
from __future__ import annotations

import json

from triagent.agents.evidence import extract_all
from triagent.agents.llm import LLMProvider, NullProvider, load_prompt, validate_narrative
from triagent.agents.plans import build_plan
from triagent.agents.snapshot import MarketSnapshot
from triagent.core.models import AgentReport, Direction, EvidenceItem


def _top(items: list[EvidenceItem], n: int) -> list[EvidenceItem]:
    return sorted(items, key=lambda e: -e.strength)[:n]


def _cite(items: list[EvidenceItem]) -> str:
    return ", ".join(f"[{e.id}]" for e in items)


def _maybe_llm(report: AgentReport, role: str, provider: LLMProvider, model: str, extra: dict) -> AgentReport:
    system, version = load_prompt(role)
    report.prompt_version = version
    if isinstance(provider, NullProvider):
        return report
    ev = report.evidence + report.acknowledged_weaknesses
    payload = {"evidence": [e.model_dump(mode="json") for e in report.evidence],
               "acknowledged_weaknesses": [e.model_dump(mode="json") for e in report.acknowledged_weaknesses],
               "plan": report.plan.model_dump(mode="json") if report.plan else None, **extra}
    user = json.dumps(payload, default=str)
    try:
        out = provider.complete_json(system, user, model)
    except Exception as e:  # noqa: BLE001
        report.challenges_to_other.append(f"(LLM narrative unavailable: {type(e).__name__})")
        return report
    ok, why = validate_narrative(out, {e.id for e in ev}, user)
    if ok:
        report.thesis = out["thesis"]
        report.narrative_source = "llm"
        report.model = f"{provider.name}:{model}"
    else:
        report.challenges_to_other.append(f"(LLM narrative rejected by validator: {why}; template used)")
    return report


def run_bull(snap: MarketSnapshot, evidence: list[EvidenceItem] | None = None,
             provider: LLMProvider | None = None, model: str = "") -> AgentReport:
    evidence = evidence if evidence is not None else extract_all(snap)
    support = [e for e in evidence if e.direction > 0]
    against = [e for e in evidence if e.direction < 0]
    s_sup, s_ag = sum(e.strength for e in support), sum(e.strength for e in against)
    conviction = s_sup / (s_sup + s_ag) if (s_sup + s_ag) > 0 else 0.0
    plan = build_plan(snap, Direction.LONG)
    top = _top(support, 4)
    if not support:
        thesis = ("BULL finds no measurable evidence supporting a long position in the current snapshot. "
                  "A bullish case would be manufactured; BULL does not propose a trade.")
    else:
        thesis = (f"Long case for {snap.instrument.symbol} ({snap.horizon.value}): "
                  + "; ".join(e.claim for e in top) + f". Evidence: {_cite(top)}. "
                  + (f"Proposed plan R:R {plan.risk_reward}." if plan.risk_reward else "Plan levels not computable.")
                  + f" Supporting strength {s_sup:.2f} vs opposing {s_ag:.2f}.")
    a = snap.analogs
    if a.get("available"):
        thesis += (f" Historical analogs: long plan reached target first in {a['long_plan_target_first']:.0%} of "
                   f"{a['n']} similar setups (descriptive, overlapping samples).")
    report = AgentReport(
        agent="BULL", stance=Direction.LONG if support else Direction.FLAT, thesis=thesis,
        conviction=round(conviction, 3), evidence=support, acknowledged_weaknesses=_top(against, 6),
        plan=plan if support else None,
        invalidation=plan.invalidation + ["Bearish evidence strength exceeding bullish strength on re-evaluation"],
    )
    return _maybe_llm(report, "bull", provider or NullProvider(), model, {})


def critique_long_plan(snap: MarketSnapshot, plan, min_rr: float = 1.5) -> list[str]:
    """Specific, measurable challenges to BULL's plan."""
    L = snap.latest
    out = []
    atr, c = L.get("atr_14"), L.get("close")
    if plan is None or plan.stop_loss is None or not atr:
        return ["BULL plan has no computable invalidation level — untradeable as specified."]
    entry = plan.entry_mid
    stop_atr = abs(entry - plan.stop_loss) / atr
    if plan.risk_reward is not None and plan.risk_reward < min_rr:
        out.append(f"R:R {plan.risk_reward} is below the {min_rr} minimum — reward does not justify risk.")
    if stop_atr < 0.75:
        out.append(f"Stop is only {stop_atr:.2f} ATR away — inside normal daily noise; high chance of being stopped out.")
    if stop_atr > 3.0:
        out.append(f"Stop is {stop_atr:.2f} ATR away — loss on invalidation would be large relative to typical moves.")
    ema20 = L.get("ema_20")
    if ema20 and c and (c - ema20) / atr > 2.0:
        out.append(f"Entry is late: price is {(c - ema20) / atr:.1f} ATR above EMA20.")
    res = [lv for lv in snap.levels if lv.kind == "RESISTANCE"]
    if res and (res[0].price - c) / atr < 1.0:
        out.append(f"Overhead resistance at {res[0].price:.2f} within 1 ATR of entry.")
    if snap.events:
        out.append("Scheduled event(s) fall inside the holding period: " + ", ".join(
            f"{e['name']} ({e['date']})" for e in snap.events[:3]) + " — gap risk cannot be priced.")
    a = snap.analogs
    if a.get("available") and a["long_plan_target_first"] < 0.34:
        out.append(f"In {a['n']} historical analogs the long plan hit target first only "
                   f"{a['long_plan_target_first']:.0%} — below 2R break-even (~33%).")
    if snap.vol_regime == "HIGH_VOL":
        out.append("High-volatility regime: stops are more likely to be hit by noise; gap risk elevated.")
    if snap.quality.status.value != "PASS":
        out.append(f"Data quality is {snap.quality.status.value}: " + "; ".join(snap.quality.issues[:2]))
    return out


def run_bear(snap: MarketSnapshot, bull: AgentReport, evidence: list[EvidenceItem] | None = None,
             provider: LLMProvider | None = None, model: str = "", min_rr: float = 1.5) -> AgentReport:
    evidence = evidence if evidence is not None else extract_all(snap)
    support = [e for e in evidence if e.direction < 0]
    against = [e for e in evidence if e.direction > 0]
    s_sup, s_ag = sum(e.strength for e in support), sum(e.strength for e in against)
    conviction = s_sup / (s_sup + s_ag) if (s_sup + s_ag) > 0 else 0.0
    challenges = critique_long_plan(snap, bull.plan, min_rr)
    concedes = s_ag > 0 and s_ag >= 1.6 * max(s_sup, 1e-9) and len(challenges) <= 1

    plan, alternative, stance = None, None, Direction.FLAT
    if s_sup > s_ag:
        short_plan = build_plan(snap, Direction.SHORT)
        if snap.short_allowed:
            plan, stance = short_plan, Direction.SHORT
            alternative = f"SHORT {snap.instrument.symbol} (R:R {short_plan.risk_reward})."
        else:
            alternative = f"STAY OUT — short not permitted for this instrument/horizon: {snap.short_reason}"
    else:
        alternative = "STAY OUT unless the challenges below are resolved." if not concedes else None

    top = _top(support, 4)
    if concedes:
        thesis = (f"BEAR concedes: bullish evidence (strength {s_ag:.2f}) clearly outweighs bearish evidence "
                  f"({s_sup:.2f}) and BULL's plan survives the risk checks. Residual risks: "
                  + ("; ".join(e.claim for e in top) if top else "none material in the loaded data") + ".")
    else:
        thesis = (f"Case against the long in {snap.instrument.symbol}: "
                  + ("; ".join(e.claim for e in top) + f" {_cite(top)}. " if top else "no strong bearish signals, but ")
                  + ("Plan challenges: " + " ".join(challenges) if challenges else "")
                  + f" Bearish strength {s_sup:.2f} vs bullish {s_ag:.2f}. Recommended: {alternative}")
    invalidation = []
    if plan is not None:
        invalidation = plan.invalidation
    else:
        L = snap.latest
        if L.get("sma_50"):
            invalidation.append(f"Sustained close above SMA50 ({L['sma_50']:.2f}) with expanding volume")
        invalidation.append("Bullish evidence strength rising above bearish strength with plan challenges resolved")
    report = AgentReport(
        agent="BEAR", stance=stance, thesis=thesis, conviction=round(conviction, 3), evidence=support,
        acknowledged_weaknesses=_top(against, 6), challenges_to_other=challenges, plan=plan,
        alternative=alternative, invalidation=invalidation, concedes=concedes,
    )
    return _maybe_llm(report, "bear", provider or NullProvider(), model,
                      {"bull_plan": bull.plan.model_dump(mode="json") if bull.plan else None, "concedes": concedes,
                       "challenges": challenges})
