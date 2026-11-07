"""Market-profile abstraction.

The decision engine talks only to this interface, so adding NYSE/LSE later means
adding a new profile — not editing the agents, judge or risk engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time

from triagent.core.models import Direction, Horizon, Instrument, InstrumentType


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    transaction_tax: float = 0.0      # e.g. STT in India
    exchange_charges: float = 0.0
    regulator_fee: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    other: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(
            self.brokerage + self.transaction_tax + self.exchange_charges + self.regulator_fee
            + self.stamp_duty + self.gst + self.other,
            2,
        )

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 2),
            "transaction_tax": round(self.transaction_tax, 2),
            "exchange_charges": round(self.exchange_charges, 2),
            "regulator_fee": round(self.regulator_fee, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "other": round(self.other, 2),
            "total": self.total,
            "notes": self.notes,
        }


class MarketProfile(ABC):
    id: str
    timezone: str
    currency: str
    benchmark_symbol: str

    @abstractmethod
    def session(self, d: date) -> tuple[datetime, datetime] | None:
        """Regular session open/close for a date, or None if closed."""

    @abstractmethod
    def is_trading_day(self, d: date) -> bool: ...

    @abstractmethod
    def round_trip_costs(
        self, instrument: Instrument, horizon: Horizon, price_in: float, price_out: float, qty: int,
        trade_date: date, direction: Direction = Direction.LONG,
    ) -> CostBreakdown: ...

    @abstractmethod
    def can_hold_short(self, instrument: Instrument, horizon: Horizon) -> tuple[bool, str]:
        """Whether a short position can be held for this horizon under market rules."""

    @abstractmethod
    def tradable_proxies(self, index_symbol: str) -> list[Instrument]:
        """Tradable instruments that express a view on a (non-tradable) index."""

    def is_open(self, now: datetime) -> bool:
        s = self.session(now.date())
        return bool(s and s[0] <= now <= s[1])

    @staticmethod
    def default_market_open() -> time:
        return time(9, 15)

    def is_index(self, instrument: Instrument) -> bool:
        return instrument.instrument_type == InstrumentType.INDEX
