"""Trade-plan construction from support/resistance and ATR (deterministic)."""
from __future__ import annotations

from triagent.agents.snapshot import MarketSnapshot
from triagent.core.models import HORIZON_SPEC, Direction, TradePlan


def build_plan(snap: MarketSnapshot, direction: Direction, min_stop_atr: float = 0.75,
               max_stop_atr: float = 3.0, default_stop_atr: float = 2.0) -> TradePlan:
    L = snap.latest
    c, atr = L.get("close"), L.get("atr_14")
    spec = HORIZON_SPEC[snap.horizon]
    plan = TradePlan(instrument=snap.instrument, direction=direction, holding_period=spec["label"])
    if snap.plan_price_note:
        plan.notes.append(snap.plan_price_note)
    if not c or not atr:
        plan.notes.append("Entry/stop/targets not computable: missing close or ATR.")
        return plan
    d = 1 if direction == Direction.LONG else -1
    tick = snap.instrument.tick_size or 0.05

    def rnd(x: float) -> float:
        return round(round(x / tick) * tick, 2)

    plan.entry_low = rnd(c - 0.25 * atr) if d == 1 else rnd(c - 0.1 * atr)
    plan.entry_high = rnd(c + 0.1 * atr) if d == 1 else rnd(c + 0.25 * atr)
    entry = (plan.entry_low + plan.entry_high) / 2

    # Stop: just beyond the nearest structural level if it is a sensible distance; otherwise ATR-based.
    levels = [lv for lv in snap.levels if lv.kind == ("SUPPORT" if d == 1 else "RESISTANCE")]
    stop = None
    if levels:
        lv = levels[0].price
        candidate = lv - d * 0.25 * atr
        dist = abs(entry - candidate) / atr
        if min_stop_atr <= dist <= max_stop_atr:
            stop = candidate
            plan.notes.append(f"Stop placed 0.25 ATR beyond {'support' if d == 1 else 'resistance'} {lv:.2f}.")
    if stop is None:
        stop = entry - d * default_stop_atr * atr
        plan.notes.append(f"Stop at {default_stop_atr} ATR (no structural level at a sensible distance).")
    plan.stop_loss = rnd(stop)
    risk = abs(entry - plan.stop_loss)

    # Targets: nearest opposing level (if beyond 1R) else 2R projection; T2 = next level or 3R projection.
    opp = [lv.price for lv in snap.levels if lv.kind == ("RESISTANCE" if d == 1 else "SUPPORT")]
    opp = sorted(opp, reverse=(d == -1))
    t1 = next((p for p in opp if abs(p - entry) >= 1.0 * risk), None)
    if t1 is None:
        t1 = entry + d * 2 * risk
        plan.notes.append("Target 1 is a 2R projection (no qualifying structural level).")
    else:
        t1 = t1 - d * 0.1 * atr
    t2_candidates = [p for p in opp if abs(p - entry) > abs(t1 - entry) + 0.5 * atr]
    if t2_candidates:
        t2 = t2_candidates[0] - d * 0.1 * atr
    else:
        t2 = entry + d * max(3 * risk, abs(t1 - entry) * 1.5)
        plan.notes.append("Target 2 is a projection, not a structural level — lower reliability.")
    if any(lv.price for lv in snap.levels if lv.kind == ("RESISTANCE" if d == 1 else "SUPPORT")
           and abs(lv.price - entry) < abs(t1 - entry) - 0.1 * atr):
        plan.notes.append("An opposing level lies before Target 1 — expect friction there.")
    plan.target_1, plan.target_2 = rnd(t1), rnd(t2)
    plan.risk_reward = round(abs(plan.target_1 - entry) / risk, 2) if risk > 0 else None
    side = "below" if d == 1 else "above"
    plan.invalidation = [
        f"Daily close {side} stop {plan.stop_loss:.2f}",
        f"{'Loss' if d == 1 else 'Reclaim'} of SMA50 ({L.get('sma_50') or float('nan'):.2f}) on a closing basis",
        f"No progress toward Target 1 within {spec['hold_bars']} bars (time stop)",
    ]
    return plan
