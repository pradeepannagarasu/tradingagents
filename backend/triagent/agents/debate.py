"""Structured challenge-and-response debate (bounded rounds, no repetition).

Each round, each agent must answer the strongest opposing points it has not yet
answered, using data: it either REFUTES (cites stronger same-category evidence),
CONTEXTUALISES (minor point, outweighed overall) or CONCEDES. Points are never
re-argued, and the debate stops early when nothing new remains.
"""
from __future__ import annotations

from triagent.core.models import AgentReport, EvidenceItem, Rebuttal


def _respond(own: list[EvidenceItem], opp: list[EvidenceItem], point: EvidenceItem, rnd: int, own_total: float,
             opp_total: float) -> Rebuttal:
    """Refute only when this side's aggregate evidence in the SAME category is stronger than the opponent's."""
    own_cat = sorted([e for e in own if e.category == point.category], key=lambda e: -e.strength)
    own_cat_total = sum(e.strength for e in own_cat)
    opp_cat_total = sum(e.strength for e in opp if e.category == point.category)
    if own_cat and own_cat_total > opp_cat_total:
        cited = own_cat[:2]
        return Rebuttal(round=rnd, target_evidence_id=point.id, response="refuted",
                        argument=f"In {point.category.value}, this side's evidence totals {own_cat_total:.2f} vs "
                                 f"{opp_cat_total:.2f}: " + "; ".join(e.claim for e in cited) + ".",
                        supporting_evidence_ids=[e.id for e in cited])
    if point.strength < 0.35 and own_total > opp_total:
        return Rebuttal(round=rnd, target_evidence_id=point.id, response="contextualised",
                        argument=f"Minor point (strength {point.strength}); aggregate evidence {own_total:.2f} "
                                 f"vs {opp_total:.2f} still favours this side.")
    return Rebuttal(round=rnd, target_evidence_id=point.id, response="conceded",
                    argument=f"Conceded: no stronger counter-evidence in {point.category.value} — {point.claim}")


def run_debate(bull: AgentReport, bear: AgentReport, max_rounds: int = 2, per_round: int = 3) -> list[str]:
    summary: list[str] = []
    answered = {"BULL": set(), "BEAR": set()}
    bull_total = sum(e.strength for e in bull.evidence)
    bear_total = sum(e.strength for e in bear.evidence)
    for rnd in range(1, max_rounds + 1):
        new_points = False
        for agent, opponent, own_total, opp_total in ((bull, bear, bull_total, bear_total),
                                                      (bear, bull, bear_total, bull_total)):
            pending = [e for e in sorted(opponent.evidence, key=lambda e: -e.strength)
                       if e.id not in answered[agent.agent] and e.strength > 0][:per_round]
            for p in pending:
                r = _respond(agent.evidence, opponent.evidence, p, rnd, own_total, opp_total)
                agent.rebuttals.append(r)
                answered[agent.agent].add(p.id)
                new_points = True
                summary.append(f"R{rnd} {agent.agent} → {opponent.agent}[{p.id}]: {r.response.upper()} — {r.argument}")
        # BULL must also answer BEAR's plan-specific challenges once (round 1)
        if rnd == 1 and bear.challenges_to_other:
            for ch in bear.challenges_to_other[:per_round]:
                if ch.startswith("("):
                    continue
                bull.rebuttals.append(Rebuttal(round=1, target_evidence_id="BEAR.PLAN_CHALLENGE", response="conceded",
                                               argument=f"Plan challenge stands (measured, not disputable by narrative): {ch}"))
                summary.append(f"R1 BULL → BEAR[plan]: CONCEDED — {ch}")
        if not new_points:
            summary.append(f"Debate ended after round {rnd - 1}: no unanswered points remain.")
            break
    return summary
