"""Paper broker: simulated execution, no real money. State persisted via the Store."""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone

from triagent.execution.broker_base import BrokerAdapter, OrderRequest, OrderStatus, OrderUpdate

STATE_KEY = "paper_broker_state"


class PaperBroker(BrokerAdapter):
    name = "PAPER (simulated)"
    is_live = False

    def __init__(self, store=None, price_source=None, slippage_bps: float = 5.0, partial_fill_ratio: float = 1.0):
        """price_source: callable(symbol) -> last price (or None). partial_fill_ratio <1 simulates partial fills."""
        self.store = store
        self.price_source = price_source or (lambda s: None)
        self.slippage_bps = slippage_bps
        self.partial_fill_ratio = partial_fill_ratio
        self._lock = threading.Lock()
        state = (store.get_state(STATE_KEY) if store else None) or {"orders": {}, "positions": {}, "closed": [],
                                                                     "seq": 0}
        self.state = state
        self._seq = itertools.count(state.get("seq", 0) + 1)

    def _save(self):
        if self.store:
            self.store.set_state(STATE_KEY, self.state)

    def place_order(self, req: OrderRequest) -> OrderUpdate:
        with self._lock:
            # Idempotency: a repeated key returns the original order, never a second one.
            for oid, o in self.state["orders"].items():
                if o["idempotency_key"] == req.idempotency_key:
                    return self._update(oid)
            if req.quantity <= 0:
                return OrderUpdate(None, OrderStatus.REJECTED, message="quantity must be positive")
            oid = f"PAPER-{next(self._seq):06d}"
            self.state["seq"] = int(oid.split("-")[1])
            last = self.price_source(req.symbol)
            ref = req.price if req.price is not None else last
            order = {"id": oid, "idempotency_key": req.idempotency_key, "symbol": req.symbol, "side": req.side,
                     "qty": req.quantity, "filled": 0, "avg_price": None, "order_type": req.order_type,
                     "price": req.price, "status": OrderStatus.OPEN.value, "message": "",
                     "created": datetime.now(timezone.utc).isoformat()}
            self.state["orders"][oid] = order
            if ref is None:
                order["status"] = OrderStatus.REJECTED.value
                order["message"] = "no price available for simulation"
            else:
                marketable = req.order_type == "MARKET" or last is None or (
                    (req.side == "BUY" and last <= req.price) or (req.side == "SELL" and last >= req.price))
                if marketable:
                    px = (last if last is not None else ref)
                    px *= (1 + self.slippage_bps / 10_000) if req.side == "BUY" else (1 - self.slippage_bps / 10_000)
                    if req.order_type == "LIMIT":
                        px = min(px, req.price) if req.side == "BUY" else max(px, req.price)
                    fill_qty = max(1, int(req.quantity * self.partial_fill_ratio))
                    self._fill(order, fill_qty, round(px, 2))
            self._save()
            return self._update(oid)

    def _fill(self, order, qty, px):
        prev = order["filled"]
        order["avg_price"] = px if not prev else round((order["avg_price"] * prev + px * qty) / (prev + qty), 2)
        order["filled"] = prev + qty
        order["status"] = (OrderStatus.FILLED if order["filled"] >= order["qty"] else OrderStatus.PARTIALLY_FILLED).value
        sign = 1 if order["side"] == "BUY" else -1
        pos = self.state["positions"].setdefault(order["symbol"], {"qty": 0, "avg_price": 0.0})
        new_qty = pos["qty"] + sign * qty
        if pos["qty"] == 0 or (pos["qty"] > 0) == (sign > 0):
            pos["avg_price"] = round((pos["avg_price"] * abs(pos["qty"]) + px * qty) / abs(new_qty), 2) if new_qty else 0
        else:
            closed_qty = min(abs(pos["qty"]), qty)
            pnl = (px - pos["avg_price"]) * closed_qty * (1 if pos["qty"] > 0 else -1)
            self.state["closed"].append({"symbol": order["symbol"], "qty": closed_qty, "entry": pos["avg_price"],
                                         "exit": px, "pnl": round(pnl, 2), "ts": datetime.now(timezone.utc).isoformat()})
        pos["qty"] = new_qty
        if new_qty == 0:
            self.state["positions"].pop(order["symbol"], None)

    def _update(self, oid) -> OrderUpdate:
        o = self.state["orders"][oid]
        return OrderUpdate(oid, OrderStatus(o["status"]), o["filled"], o["avg_price"], o["message"], dict(o))

    def order_status(self, broker_order_id):
        return self._update(broker_order_id)

    def find_by_idempotency_key(self, key):
        for oid, o in self.state["orders"].items():
            if o["idempotency_key"] == key:
                return self._update(oid)
        return None

    def cancel_order(self, broker_order_id):
        with self._lock:
            o = self.state["orders"][broker_order_id]
            if o["status"] in (OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value):
                o["status"] = OrderStatus.CANCELLED.value
                self._save()
            return self._update(broker_order_id)

    def modify_order(self, broker_order_id, price=None, quantity=None):
        with self._lock:
            o = self.state["orders"][broker_order_id]
            if o["status"] != OrderStatus.OPEN.value:
                return OrderUpdate(broker_order_id, OrderStatus(o["status"]), o["filled"], o["avg_price"],
                                   "only OPEN orders can be modified")
            if price is not None:
                o["price"] = price
            if quantity is not None:
                o["qty"] = quantity
            self._save()
            return self._update(broker_order_id)

    def open_orders(self):
        return [self._update(oid) for oid, o in self.state["orders"].items()
                if o["status"] in (OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value)]

    def positions(self):
        return [{"symbol": s, **p} for s, p in self.state["positions"].items()]

    def realised(self) -> list[dict]:
        return list(self.state["closed"])
