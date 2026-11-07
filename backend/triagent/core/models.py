"""Core domain models shared by every service.

Nothing in this module is market-specific: India/NSE details live in
``triagent.markets``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class InstrumentType(str, Enum):
    INDEX = "INDEX"          # benchmark only — NOT tradable
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class Horizon(str, Enum):
    INTRADAY = "INTRADAY"
    SHORT = "SHORT"          # 1-3 days
    SWING = "SWING"          # 1-4 weeks
    POSITIONAL = "POSITIONAL"  # 1-6 months


HORIZON_SPEC: dict[Horizon, dict[str, Any]] = {
    Horizon.INTRADAY: {"bar": "5m", "hold_bars": 1, "hold_days": (0, 1), "label": "Intraday (same session)"},
    Horizon.SHORT: {"bar": "1d", "hold_bars": 3, "hold_days": (1, 3), "label": "1-3 trading days"},
    Horizon.SWING: {"bar": "1d", "hold_bars": 15, "hold_days": (5, 20), "label": "Swing: 1-4 weeks"},
    Horizon.POSITIONAL: {"bar": "1d", "hold_bars": 60, "hold_days": (20, 125), "label": "Positional: 1-6 months"},
}


class Decision(str, Enum):
    BUY = "BUY / LONG"
    SELL = "SELL / SHORT"
    NO_TRADE = "NO TRADE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Provenance(str, Enum):
    """The five epistemic classes every report must distinguish."""
    VERIFIED_HISTORICAL = "verified_historical_fact"
    CURRENT_OBSERVATION = "current_market_observation"
    AGENT_INTERPRETATION = "agent_interpretation"
    MODEL_ESTIMATE = "model_estimate"
    UNVERIFIED = "unverified_or_unavailable"


class Category(str, Enum):
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    MACRO_REGIME = "macro_regime"
    NEWS_EVENTS = "news_events"
    DERIVATIVES = "derivatives"
    HISTORICAL = "historical"
    RISK_REWARD = "risk_reward"


class QualityStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Instrument(BaseModel):
    symbol: str
    exchange: str = "NSE"
    name: str = ""
    instrument_type: InstrumentType = InstrumentType.EQUITY
    currency: str = "INR"
    lot_size: int = 1
    tick_size: float = 0.05
    sector: str | None = None
    underlying: str | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: Literal["CE", "PE"] | None = None

    @property
    def tradable(self) -> bool:
        return self.instrument_type != InstrumentType.INDEX

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


class SourceRef(BaseModel):
    """Where a fact came from. Every evidence item carries one."""
    source: str
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    licence: str | None = None
    simulated: bool = False


class EvidenceItem(BaseModel):
    id: str
    category: Category
    claim: str
    direction: int = Field(ge=-1, le=1, description="+1 supports LONG, -1 supports SHORT/avoid, 0 neutral")
    strength: float = Field(ge=0.0, le=1.0)
    metric: str | None = None
    value: float | str | None = None
    provenance: Provenance = Provenance.CURRENT_OBSERVATION
    source: SourceRef | None = None
    as_of: datetime | None = None


class TradePlan(BaseModel):
    instrument: Instrument
    direction: Direction
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    risk_reward: float | None = None
    holding_period: str = ""
    invalidation: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)   # e.g. "target 2 not reliably computable"

    @property
    def entry_mid(self) -> float | None:
        if self.entry_low is None or self.entry_high is None:
            return None
        return (self.entry_low + self.entry_high) / 2


class Rebuttal(BaseModel):
    round: int
    target_evidence_id: str
    response: Literal["refuted", "conceded", "contextualised"]
    argument: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class AgentReport(BaseModel):
    agent: Literal["BULL", "BEAR"]
    stance: Direction
    thesis: str
    conviction: float = Field(ge=0, le=1)
    evidence: list[EvidenceItem]
    acknowledged_weaknesses: list[EvidenceItem] = Field(default_factory=list)
    challenges_to_other: list[str] = Field(default_factory=list)
    plan: TradePlan | None = None
    alternative: str | None = None
    invalidation: list[str] = Field(default_factory=list)
    concedes: bool = False
    rebuttals: list[Rebuttal] = Field(default_factory=list)
    model: str = "deterministic-rules"
    prompt_version: str = ""
    narrative_source: Literal["template", "llm"] = "template"


class GateResult(BaseModel):
    name: str
    passed: bool
    detail: str


class CategoryScore(BaseModel):
    category: Category
    weight: float
    score: float           # -1..1 (positive = bullish)
    has_evidence: bool
    verified_items: int
    rejected_items: int


class RiskCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class RiskAssessment(BaseModel):
    approved: bool
    checks: list[RiskCheck]
    quantity: int = 0
    capital_required: float = 0.0
    max_loss_at_stop: float = 0.0
    estimated_costs: float = 0.0
    estimated_slippage: float = 0.0
    margin_required: float | None = None
    approval_token: str | None = None


class JudgeReport(BaseModel):
    decision: Decision
    composite_score: float
    category_scores: list[CategoryScore]
    coverage: float
    gates: list[GateResult]
    winning_side: Literal["BULL", "BEAR", "NEITHER"]
    reasons: list[str]
    strongest_arguments: list[str]
    rejected_arguments: list[str]
    key_data_points: list[str]
    unresolved_risks: list[str]
    change_conditions: list[str]
    agent_disagreement: float
    calibrated_probability: float | None = None
    calibration_note: str = ""
    debate_summary: list[str] = Field(default_factory=list)
    plan: TradePlan | None = None
    model: str = "deterministic-judge"
    prompt_version: str = ""


class DataQualityReport(BaseModel):
    instrument: str
    status: QualityStatus
    source: str
    simulated: bool
    first_bar: str | None
    last_bar: str | None
    n_bars: int
    issues: list[str]
    gaps: list[dict[str, Any]]
    stale: bool
    checked_at: datetime
    data_label: str = "UNKNOWN"   # REAL | DELAYED | SIMULATED


class DecisionRecord(BaseModel):
    id: str
    created_at: datetime
    market_data_as_of: datetime | None
    instrument: Instrument
    horizon: Horizon
    mode: str
    bull: AgentReport
    bear: AgentReport
    judge: JudgeReport
    risk: RiskAssessment | None
    final_decision: Decision
    data_quality: DataQualityReport
    snapshot_hash: str
    simulated_data: bool
    disclaimer: str = (
        "Research and decision-support output only. Not investment advice. No outcome is guaranteed; "
        "scores are not probabilities of profit unless explicitly marked as calibrated."
    )


def stable_hash(obj: Any) -> str:
    """SHA-256 of a JSON-serialisable object with sorted keys (used for immutable snapshots)."""
    data = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()
