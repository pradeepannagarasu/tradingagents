"""Zerodha Kite Connect v3 adapter — SKELETON, NOT VERIFIED AGAINST THE LIVE API.

It is only reachable through LiveGuard, which refuses every call unless live trading is
enabled in config AND environment AND the emergency 'live disabled' flag is cleared.
Before enabling: register the strategy/algo as required by your broker under the SEBI
retail-algo framework, configure a static IP with Kite (required for order placement),
and test against the broker's sandbox/minimum-quantity procedures.

Docs: https://kite.trade/docs/connect/v3/orders/
"""
from __future__ import annotations

import os

import httpx

from triagent.execution.broker_base import (AuthExpired, BrokerAdapter, BrokerError, OrderRequest, OrderStatus,
                                            OrderUpdate)

_STATUS = {"COMPLETE": OrderStatus.FILLED, "CANCELLED": OrderStatus.CANCELLED, "REJECTED": OrderStatus.REJECTED,
           "OPEN": OrderStatus.OPEN, "TRIGGER PENDING": OrderStatus.OPEN, "MODIFY VALIDATION PENDING": OrderStatus.OPEN,
           "PUT ORDER REQ RECEIVED": OrderStatus.OPEN, "VALIDATION PENDING": OrderStatus.OPEN,
           "OPEN PENDING": OrderStatus.OPEN}


class KiteAdapter(BrokerAdapter):
    name = "Zerodha Kite Connect"
    is_live = True
    BASE = "https://api.kite.trade"

    def __init__(self, api_key: str | None = None, access_token: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or os.environ.get("KITE_API_KEY", "")
        self.access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN", "")
        self.client = client or httpx.Client(base_url=self.BASE, timeout=10)

    def _headers(self):
        if not (self.api_key and self.access_token):
            raise AuthExpired("Kite credentials missing (KITE_API_KEY / KITE_ACCESS_TOKEN)")
        return {"X-Kite-Version": "3", "Authorization": f"token {self.api_key}:{self.access_token}"}

    def _req(self, method, path, **kw) -> dict:
        r = self.client.request(method, path, headers=self._headers(), **kw)
        if r.status_code == 403:
            raise AuthExpired("Kite session expired or invalid (403) — re-login required")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or body.get("status") == "error":
            raise BrokerError(f"Kite {r.status_code}: {body.get('message', r.text[:200])}")
        return body.get("data", {})

    @staticmethod
    def build_params(req: OrderRequest) -> dict:
        p = {"tradingsymbol": req.symbol, "exchange": req.exchange, "transaction_type": req.side,
             "quantity": req.quantity, "product": req.product, "order_type": req.order_type, "validity": "DAY",
             "tag": req.idempotency_key[:20]}
        if req.price is not None:
            p["price"] = req.price
        if req.trigger_price is not None:
            p["trigger_price"] = req.trigger_price
        return p

    def _to_update(self, o: dict) -> OrderUpdate:
        st = _STATUS.get(o.get("status", ""), OrderStatus.UNKNOWN)
        if st == OrderStatus.OPEN and o.get("filled_quantity", 0) > 0:
            st = OrderStatus.PARTIALLY_FILLED
        return OrderUpdate(o.get("order_id"), st, int(o.get("filled_quantity") or 0), o.get("average_price"),
                           o.get("status_message") or "", o)

    def place_order(self, req):
        data = self._req("POST", "/orders/regular", data=self.build_params(req))
        oid = data.get("order_id")
        return self.order_status(oid) if oid else OrderUpdate(None, OrderStatus.UNKNOWN, message="no order_id returned")

    def order_status(self, broker_order_id):
        hist = self._req("GET", f"/orders/{broker_order_id}")
        return self._to_update(hist[-1]) if hist else OrderUpdate(broker_order_id, OrderStatus.UNKNOWN)

    def find_by_idempotency_key(self, key):
        for o in self._req("GET", "/orders"):
            if o.get("tag") == key[:20]:
                return self._to_update(o)
        return None

    def cancel_order(self, broker_order_id):
        self._req("DELETE", f"/orders/regular/{broker_order_id}")
        return self.order_status(broker_order_id)

    def modify_order(self, broker_order_id, price=None, quantity=None):
        data = {k: v for k, v in {"price": price, "quantity": quantity}.items() if v is not None}
        self._req("PUT", f"/orders/regular/{broker_order_id}", data=data)
        return self.order_status(broker_order_id)

    def open_orders(self):
        return [self._to_update(o) for o in self._req("GET", "/orders")
                if _STATUS.get(o.get("status", "")) == OrderStatus.OPEN]

    def positions(self):
        return self._req("GET", "/portfolio/positions").get("net", [])
