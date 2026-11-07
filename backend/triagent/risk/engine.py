"""Independent risk-control service.

No agent can bypass it: execution requires an HMAC-signed approval token that only
this engine can mint (keyed by a secret the agents never see), bound to the exact
decision, instrument, direction, quantity and stop, and expiring after a short TTL.
If the risk engine is unavailable, no token exists, so nothing can execute (fail-closed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from triagent.core.models import (Decision, Direction, Horizon, InstrumentType, QualityStatus, RiskAssessment, RiskCheck,
                                  TradePlan)
from triagent.markets.base import MarketProfile

TOKEN_TTL_SECONDS = 15 * 60


@dataclass
class RiskLimits:
    capital: float
    max_capital_per_trade_pct: float
    max_risk_per_trade_pct: float
    max_daily_loss: float
    max_weekly_loss: float
    max_open_positions: int
    max_sector_exposure_pct: float
    max_leverage: float
    max_drawdown_pct: float
    min_avg_daily_value_multiple: float
    max_spread_bps: float
    min_stop_distance_atr: float
    max_stop_distance_atr: float
    max_india_vix: float
    block_expiry_day_options: bool
    default_slippage_bps: float
    min_risk_reward: float = 1.5
    futures_margin_pct: float = 0.15   # estimate only — broker SPAN+exposure margin is authoritative

    @classmethod
    def from_config(cls, risk: dict, min_rr: float = 1.5) -> "RiskLimits":
        fields_ = {k: risk[k] for k in cls.__dataclass_fields__ if k in risk}
        lim = cls(**fields_, min_risk_reward=min_rr) if "min_risk_reward" not in fields_ else cls(**fields_)
        lim.validate()
        return lim

    def validate(self):
        if self.capital <= 0 or not math.isfinite(self.capital):
            raise ValueError("capital must be a positive finite number")
        for name in ("max_capital_per_trade_pct", "max_risk_per_trade_pct", "max_sector_exposure_pct",
                     "max_drawdown_pct"):
            v = getattr(self, name)
            if not (0 < v <= 100):
                raise ValueError(f"{name} must be in (0, 100]; unlimited sizing is not allowed")
        if self.max_risk_per_trade_pct > 5:
            raise ValueError("max_risk_per_trade_pct above 5% is not permitted")
        if self.max_open_positions < 1 or self.max_leverage <= 0 or self.max_leverage > 5:
            raise ValueError("invalid max_open_positions / max_leverage (leverage capped at 5x)")


@dataclass
class PortfolioState:
    equity: float
    peak_equity: float
    realised_pnl_today: float = 0.0
    realised_pnl_week: float = 0.0
    open_positions: list[dict] = field(default_factory=list)   # {symbol, sector, direction, value, unrealised}


@dataclass
class TradeContext:
    decision_id: str
    decision: Decision
    plan: TradePlan | None
    horizon: Horizon
    atr: float | None
    avg_daily_value: float | None
    data_quality: QualityStatus
    data_stale: bool
    simulated: bool
    mode: str                      # RESEARCH | PAPER | LIVE
    india_vix: float | None = None
    vol_regime: str = "UNKNOWN"
    high_impact_event_in_window: bool = False
    bid: float | None = None
    ask: float | None = None
    trade_date: datetime | None = None


class RiskEngine:
    def __init__(self, limits: RiskLimits, profile: MarketProfile, signing_key: bytes, kill_switch=lambda: False):
        self.limits = limits
        self.profile = profile
        self._key = signing_key
        self._kill_switch = kill_switch

    # ------------------------------------------------------------------ tokens
    def _sign(self, payload: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        sig = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_token(self, token: str | None, *, decision_id: str, symbol: str, direction: str, qty: int,
                     stop: float) -> tuple[bool, str]:
        if not token or "." not in token:
            return False, "missing risk approval token"
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False, "invalid risk token signature"
        p = json.loads(base64.urlsafe_b64decode(body.encode()))
        if p["exp"] < time.time():
            return False, "risk approval expired — re-run risk checks"
        if (p["decision_id"], p["symbol"], p["direction"], p["qty"]) != (decision_id, symbol, direction, qty) \
                or not math.isclose(p["stop"], stop):
            return False, "risk token does not match the order (decision/instrument/direction/qty/stop)"
        if self._kill_switch():
            return False, "kill switch engaged"
        return True, "ok"

    # ------------------------------------------------------------------ assessment
    def assess(self, ctx: TradeContext, pf: PortfolioState) -> RiskAssessment:
        L = self.limits
        checks: list[RiskCheck] = []

        def add(name, ok, detail):
            checks.append(RiskCheck(name=name, passed=bool(ok), detail=detail))

        add("kill_switch_off", not self._kill_switch(), "emergency stop not engaged" if not self._kill_switch()
            else "STOP ALL TRADING is engaged")
        add("decision_is_trade", ctx.decision != Decision.NO_TRADE, ctx.decision.value)
        plan = ctx.plan
        if plan is None or ctx.decision == Decision.NO_TRADE:
            return self._finish(ctx, checks, 0, plan)
        inst = plan.instrument
        add("tradable_instrument", inst.tradable, f"{inst.symbol} {inst.instrument_type.value}")
        add("mode_permits", ctx.mode in ("PAPER", "LIVE"),
            f"mode {ctx.mode}" + (" — research mode never executes" if ctx.mode == "RESEARCH" else ""))
        if ctx.mode == "LIVE":
            add("no_simulated_data_live", not ctx.simulated, "LIVE requires real market data")
        elif ctx.simulated:
            add("simulated_data_notice", True, "SIMULATED data — paper demonstration only")
        add("data_quality", ctx.data_quality != QualityStatus.FAIL and not ctx.data_stale,
            f"{ctx.data_quality.value}{', stale' if ctx.data_stale else ''}")

        entry = plan.entry_mid
        stop = plan.stop_loss
        has_inval = entry is not None and stop is not None and bool(plan.invalidation)
        add("invalidation_defined", has_inval, f"entry {entry}, stop {stop}")
        if not has_inval:
            return self._finish(ctx, checks, 0, plan)
        d = 1 if plan.direction == Direction.LONG else -1
        add("stop_on_correct_side", (entry - stop) * d > 0, f"entry {entry:.2f} vs stop {stop:.2f} for {plan.direction.value}")
        per_unit_risk = abs(entry - stop)
        if ctx.atr:
            dist_atr = per_unit_risk / ctx.atr
            add("stop_distance", L.min_stop_distance_atr <= dist_atr <= L.max_stop_distance_atr,
                f"{dist_atr:.2f} ATR (allowed {L.min_stop_distance_atr}–{L.max_stop_distance_atr})")
        rr = plan.risk_reward
        add("risk_reward", rr is not None and rr >= L.min_risk_reward, f"R:R {rr} (min {L.min_risk_reward})")

        short_ok, why = self.profile.can_hold_short(inst, ctx.horizon)
        if plan.direction == Direction.SHORT:
            add("short_permitted", short_ok, why)

        # sizing ------------------------------------------------------------
        equity = pf.equity
        risk_budget = equity * L.max_risk_per_trade_pct / 100
        reductions = []
        if ctx.vol_regime == "HIGH_VOL":
            risk_budget *= 0.5
            reductions.append("halved for HIGH_VOL regime")
        if ctx.high_impact_event_in_window and ctx.horizon != Horizon.INTRADAY:
            risk_budget *= 0.5
            reductions.append("halved for scheduled event gap risk")
        lot = max(1, inst.lot_size)
        is_fut = inst.instrument_type in (InstrumentType.FUTURE,)
        unit_capital = entry * (L.futures_margin_pct if is_fut else 1.0)
        cap_budget = equity * L.max_capital_per_trade_pct / 100 * L.max_leverage
        qty_risk = risk_budget / per_unit_risk
        qty_cap = cap_budget / unit_capital
        qty = int(math.floor(min(qty_risk, qty_cap) / lot)) * lot
        add("position_size_positive", qty > 0,
            f"qty {qty} (risk budget ₹{risk_budget:,.0f}{' — ' + ', '.join(reductions) if reductions else ''}; "
            f"per-unit risk {per_unit_risk:.2f}; lot {lot})" + ("" if qty > 0 else
                                                                " — one lot exceeds the risk budget; trade rejected"))
        notional = qty * entry
        capital_required = qty * unit_capital
        max_loss = qty * per_unit_risk
        add("capital_per_trade", capital_required <= equity * L.max_capital_per_trade_pct / 100 * L.max_leverage + 1e-6,
            f"₹{capital_required:,.0f} ≤ {L.max_capital_per_trade_pct}% × leverage {L.max_leverage}")
        add("risk_per_trade", max_loss <= equity * L.max_risk_per_trade_pct / 100 + 1e-6,
            f"max loss at stop ₹{max_loss:,.0f} ≤ ₹{equity * L.max_risk_per_trade_pct / 100:,.0f}")
        gross = sum(abs(p["value"]) for p in pf.open_positions) + notional
        add("leverage", gross <= equity * L.max_leverage + 1e-6, f"gross exposure ₹{gross:,.0f} vs equity ₹{equity:,.0f} × {L.max_leverage}")

        # portfolio --------------------------------------------------------
        add("open_positions", len(pf.open_positions) < L.max_open_positions,
            f"{len(pf.open_positions)} open (max {L.max_open_positions})")
        same = [p for p in pf.open_positions if p["symbol"] == inst.symbol]
        add("no_averaging_or_pyramiding", not same,
            "no existing position" if not same else "existing position in this instrument — adding is blocked")
        sector = inst.sector or "INDEX"
        sec_val = sum(abs(p["value"]) for p in pf.open_positions if p.get("sector", "INDEX") == sector) + notional
        add("sector_exposure", sec_val <= equity * L.max_sector_exposure_pct / 100 * L.max_leverage + 1e-6,
            f"{sector}: ₹{sec_val:,.0f} (max {L.max_sector_exposure_pct}%)")
        add("daily_loss_limit", -pf.realised_pnl_today < L.max_daily_loss,
            f"today P&L ₹{pf.realised_pnl_today:,.0f} (limit −₹{L.max_daily_loss:,.0f})")
        add("weekly_loss_limit", -pf.realised_pnl_week < L.max_weekly_loss,
            f"week P&L ₹{pf.realised_pnl_week:,.0f} (limit −₹{L.max_weekly_loss:,.0f})")
        dd = (pf.equity / pf.peak_equity - 1) * 100 if pf.peak_equity else 0
        add("drawdown_threshold", -dd < L.max_drawdown_pct, f"drawdown {dd:.2f}% (max {L.max_drawdown_pct}%)")

        # market conditions -------------------------------------------------
        if ctx.avg_daily_value:
            add("liquidity", ctx.avg_daily_value >= notional * L.min_avg_daily_value_multiple,
                f"avg daily traded value ₹{ctx.avg_daily_value:,.0f} vs {L.min_avg_daily_value_multiple}× position")
        else:
            add("liquidity", False, "no traded-value data — liquidity cannot be verified (fail-closed)")
        if ctx.bid and ctx.ask:
            spread = (ctx.ask - ctx.bid) / ((ctx.ask + ctx.bid) / 2) * 10_000
            add("bid_ask_spread", spread <= L.max_spread_bps, f"{spread:.1f} bps (max {L.max_spread_bps})")
        else:
            add("bid_ask_spread", ctx.mode != "LIVE", "no live quote — spread unchecked" +
                (" (fail-closed in LIVE)" if ctx.mode == "LIVE" else " (paper: will be checked at execution)"))
        if ctx.india_vix is not None:
            add("volatility_vix", ctx.india_vix <= L.max_india_vix, f"India VIX {ctx.india_vix} (max {L.max_india_vix})")
        td = (ctx.trade_date or datetime.now(timezone.utc)).date()
        if inst.instrument_type == InstrumentType.OPTION and L.block_expiry_day_options:
            add("expiry_risk", not self.profile.is_expiry_day(td) if hasattr(self.profile, "is_expiry_day") else True,
                "no new option positions on expiry day")
        if inst.instrument_type == InstrumentType.FUTURE and hasattr(self.profile, "is_expiry_day"):
            add("expiry_risk", not self.profile.is_expiry_day(td), "no new futures positions on (approximate) expiry day")
        add("gap_risk", not (ctx.high_impact_event_in_window and ctx.vol_regime == "HIGH_VOL"),
            "scheduled event in HIGH_VOL regime" if ctx.high_impact_event_in_window else "no scheduled event in window")

        # costs --------------------------------------------------------------
        cost = slip = 0.0
        if qty > 0:
            t1 = plan.target_1 or entry
            cb = self.profile.round_trip_costs(inst, ctx.horizon, entry, t1, qty, td, plan.direction)
            cost = cb.total
            slip = notional * L.default_slippage_bps / 10_000 * 2
            reward = abs(t1 - entry) * qty
            add("costs_vs_reward", cost + slip <= 0.15 * reward,
                f"costs ₹{cost:,.0f} + slippage ₹{slip:,.0f} vs reward to T1 ₹{reward:,.0f} (max 15%)")
        return self._finish(ctx, checks, qty, plan, capital_required, max_loss, cost, slip,
                            capital_required if is_fut else None)

    def _finish(self, ctx, checks, qty, plan, capital=0.0, max_loss=0.0, cost=0.0, slip=0.0, margin=None):
        approved = all(c.passed for c in checks) and qty > 0
        token = None
        if approved and plan is not None:
            token = self._sign({"decision_id": ctx.decision_id, "symbol": plan.instrument.symbol,
                                "direction": plan.direction.value, "qty": qty, "stop": plan.stop_loss,
                                "exp": time.time() + TOKEN_TTL_SECONDS})
        return RiskAssessment(approved=approved, checks=checks, quantity=qty if approved else 0,
                              capital_required=round(capital, 2), max_loss_at_stop=round(max_loss, 2),
                              estimated_costs=round(cost, 2), estimated_slippage=round(slip, 2),
                              margin_required=round(margin, 2) if margin else None, approval_token=token)
