"""Common broker interface. Adding a broker = implementing this class; the engine is unchanged."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class OrderStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    SUBMITTING = "SUBMITTING"          # persisted BEFORE the broker call (crash-safe)
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"                # broker call failed mid-flight — must reconcile, NEVER blind-retry
    EXPIRED = "EXPIRED"


@dataclass
class OrderRequest:
    idempotency_key: str
    symbol: str
    exchange: str
    side: str                 # BUY | SELL
    quantity: int
    order_type: str = "LIMIT"  # LIMIT | MARKET | SL | SL-M
    price: float | None = None
    trigger_price: float | None = None
    product: str = "NRML"     # CNC (delivery) | MIS (intraday) | NRML (F&O carry)
    tag: str = ""


@dataclass
class OrderUpdate:
    broker_order_id: str | None
    status: OrderStatus
    filled_qty: int = 0
    avg_price: float | None = None
    message: str = ""
    raw: dict = field(default_factory=dict)


class BrokerError(RuntimeError):
    pass


class AuthExpired(BrokerError):
    pass


class LiveTradingDisabled(BrokerError):
    pass


class BrokerAdapter(ABC):
    name: str
    is_live: bool = False

    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderUpdate: ...

    @abstractmethod
    def order_status(self, broker_order_id: str) -> OrderUpdate: ...

    @abstractmethod
    def find_by_idempotency_key(self, key: str) -> OrderUpdate | None:
        """Used for reconciliation after a failed/unknown submission."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> OrderUpdate: ...

    @abstractmethod
    def modify_order(self, broker_order_id: str, price: float | None = None, quantity: int | None = None) -> OrderUpdate: ...

    @abstractmethod
    def open_orders(self) -> list[OrderUpdate]: ...

    @abstractmethod
    def positions(self) -> list[dict]: ...
