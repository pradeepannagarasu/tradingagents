"""Order-ticket workflow: decision → risk-approved ticket → explicit human CONFIRM → single submission → reconcile.

Guarantees
* Never executes because an agent produced a signal — a human must confirm the exact ticket (hash-bound).
* LIVE submission is impossible unless config flag, env flag and the emergency 'live disabled' state all allow it.
* Each ticket has an idempotency key; repeated confirms/submits return the existing order.
* Status is persisted as SUBMITTING before the broker call; an exception leaves it UNKNOWN and the
  system reconciles by querying the broker — it never blindly re-sends.
* Every state change is written to the hash-chained audit log.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from triagent.core.models import Decision, Direction
from triagent.data.store import Store
from triagent.execution.broker_base import (BrokerAdapter, BrokerError, LiveTradingDisabled, OrderRequest, OrderStatus)
from triagent.risk.engine import RiskEngine
from triagent.risk.kill_switch import EmergencyControls

CONFIRM_PHRASE = "CONFIRM TRADE"


class LiveGuard(BrokerAdapter):
    """Wraps a live adapter; refuses every mutating call unless live trading is fully enabled."""

    def __init__(self, inner: BrokerAdapter, settings, controls: EmergencyControls):
        self.inner, self.settings, self.controls = inner, settings, controls
        self.name = f"LiveGuard({inner.name})"
        self.is_live = True

    def _check(self):
        if not self.settings.live_trading_enabled:
            raise LiveTradingDisabled("LIVE trading disabled (config live_trading_enabled and env LIVE_TRADING_ENABLED "
                                      "must both be true)")
        if self.controls.live_disabled():
            raise LiveTradingDisabled("LIVE execution disabled by emergency control")
        if self.controls.trading_halted():
            raise LiveTradingDisabled("STOP ALL TRADING engaged")

    def place_order(self, req):
        self._check()
        return self.inner.place_order(req)

    def cancel_order(self, oid):
        return self.inner.cancel_order(oid)  # cancelling is always allowed

    def modify_order(self, oid, price=None, quantity=None):
        self._check()
        return self.inner.modify_order(oid, price, quantity)

    def order_status(self, oid):
        return self.inner.order_status(oid)

    def find_by_idempotency_key(self, key):
        return self.inner.find_by_idempotency_key(key)

    def open_orders(self):
        return self.inner.open_orders()

    def positions(self):
        return self.inner.positions()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ticket_hash(ticket: dict) -> str:
    return hashlib.sha256(json.dumps(ticket, sort_keys=True, default=str).encode()).hexdigest()


class OrderManager:
    def __init__(self, store: Store, risk: RiskEngine, controls: EmergencyControls, settings,
                 paper: BrokerAdapter, live: BrokerAdapter | None = None, account: str = "PAPER-ACCOUNT",
                 max_decision_age_days: float = 4.0):
        self.store, self.risk, self.controls, self.settings = store, risk, controls, settings
        self.max_decision_age_days = max_decision_age_days
        self.paper, self.live, self.account = paper, live, account

    def broker_for(self, mode: str) -> BrokerAdapter:
        if mode == "LIVE":
            if self.live is None:
                raise LiveTradingDisabled("No live broker configured")
            return self.live
        if mode == "PAPER":
            return self.paper
        raise LiveTradingDisabled(f"Mode {mode} does not execute orders")

    # ------------------------------------------------------------------ create
    def create_ticket(self, record: dict, mode: str) -> dict:
        if record["final_decision"] == Decision.NO_TRADE.value:
            raise ValueError("NO TRADE decisions cannot become orders")
        as_of = datetime.fromisoformat(str(record["market_data_as_of"]).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - as_of).total_seconds() / 86400
        if age_days > self.max_decision_age_days:
            raise ValueError(f"Decision is based on market data {age_days:.1f} days old — stale data cannot become an "
                             "order. Re-run research on current data.")
        risk = record.get("risk") or {}
        if not risk.get("approved"):
            raise ValueError("Risk engine did not approve this decision")
        plan = record["judge"]["plan"]
        inst = plan["instrument"]
        side = "BUY" if plan["direction"] == Direction.LONG.value else "SELL"
        idem = hashlib.sha256(f"{record['id']}|{inst['symbol']}|{side}|{risk['quantity']}".encode()).hexdigest()[:32]
        product = "NRML" if inst["instrument_type"] in ("FUTURE", "OPTION") else (
            "MIS" if record["horizon"] == "INTRADAY" else "CNC")
        ticket = {
            "broker": self.broker_for(mode).name if mode in ("PAPER", "LIVE") else "n/a",
            "account": self.account, "mode": mode,
            "instrument": inst["symbol"], "exchange": inst["exchange"], "instrument_type": inst["instrument_type"],
            "direction": plan["direction"], "side": side, "quantity": risk["quantity"], "order_type": "LIMIT",
            "limit_price": plan["entry_high"] if side == "BUY" else plan["entry_low"],
            "entry_range": [plan["entry_low"], plan["entry_high"]], "stop_loss": plan["stop_loss"],
            "target_1": plan["target_1"], "target_2": plan["target_2"], "product": product,
            "capital_required": risk["capital_required"], "margin_required": risk.get("margin_required"),
            "max_loss_at_stop": risk["max_loss_at_stop"], "estimated_costs": risk["estimated_costs"],
            "estimated_slippage": risk["estimated_slippage"],
            "judge_reasoning": record["judge"]["reasons"], "risk_approved": True,
            "price_notes": plan.get("notes", []), "simulated_data": record["simulated_data"],
        }
        tid = uuid.uuid4().hex[:16]
        row = {"id": tid, "decision_id": record["id"], "idempotency_key": idem, "status": OrderStatus.PENDING_APPROVAL.value,
               "mode": mode, "ticket_json": {**ticket, "risk_token": risk["approval_token"]},
               "ticket_hash": ticket_hash(ticket), "broker_order_id": None, "created_at": _now(), "updated_at": _now()}
        if not self.store.insert_ticket(row):
            existing = [t for t in self.store.list_tickets(1000) if t["idempotency_key"] == idem]
            self.store.audit("system", "DUPLICATE_TICKET_REQUEST", {"decision_id": record["id"]})
            return existing[0]
        self.store.audit("system", "TICKET_CREATED", {"ticket_id": tid, "decision_id": record["id"], **ticket})
        return row

    # ------------------------------------------------------------------ confirm & submit
    def confirm_and_submit(self, ticket_id: str, confirmation: str, shown_hash: str, actor: str = "user") -> dict:
        t = self.store.get_ticket(ticket_id)
        if t is None:
            raise ValueError("unknown ticket")
        if t["status"] in (OrderStatus.CANCELLED.value, OrderStatus.EXPIRED.value):
            raise PermissionError(f"Ticket is {t['status']} and cannot be submitted; create a new ticket")
        if t["status"] != OrderStatus.PENDING_APPROVAL.value:
            self.store.audit(actor, "DUPLICATE_CONFIRM_IGNORED", {"ticket_id": ticket_id, "status": t["status"]})
            return t  # idempotent: never submits twice
        if confirmation != CONFIRM_PHRASE:
            raise PermissionError(f"Explicit confirmation required: type '{CONFIRM_PHRASE}'")
        if shown_hash != t["ticket_hash"]:
            raise PermissionError("Ticket changed since it was displayed — review again before confirming")
        if self.controls.trading_halted():
            raise LiveTradingDisabled("STOP ALL TRADING is engaged")
        tk = t["ticket_json"]
        ok, why = self.risk.verify_token(tk["risk_token"], decision_id=t["decision_id"], symbol=tk["instrument"],
                                         direction=tk["direction"], qty=tk["quantity"], stop=tk["stop_loss"])
        if not ok:
            self.store.audit(actor, "SUBMIT_BLOCKED_RISK", {"ticket_id": ticket_id, "reason": why})
            raise PermissionError(f"Risk approval invalid: {why}")
        if t["mode"] == "LIVE" and tk.get("simulated_data"):
            raise PermissionError("LIVE orders cannot be based on simulated data")
        broker = self.broker_for(t["mode"])  # raises if LIVE not configured
        self.store.audit(actor, "TICKET_CONFIRMED", {"ticket_id": ticket_id, "hash": shown_hash})
        self.store.update_ticket(ticket_id, status=OrderStatus.SUBMITTING.value)
        req = OrderRequest(idempotency_key=t["idempotency_key"], symbol=tk["instrument"], exchange=tk["exchange"],
                           side=tk["side"], quantity=tk["quantity"], order_type=tk["order_type"],
                           price=tk["limit_price"], product=tk["product"], tag=t["idempotency_key"][:20])
        try:
            upd = broker.place_order(req)
        except LiveTradingDisabled as e:
            self.store.update_ticket(ticket_id, status=OrderStatus.REJECTED.value)
            self.store.audit("system", "SUBMIT_REFUSED_LIVE_DISABLED", {"ticket_id": ticket_id, "reason": str(e)})
            raise
        except Exception as e:  # noqa: BLE001 — network/API failure: unknown outcome
            self.store.update_ticket(ticket_id, status=OrderStatus.UNKNOWN.value)
            self.store.audit("system", "SUBMIT_OUTCOME_UNKNOWN", {"ticket_id": ticket_id, "error": repr(e)})
            return self.reconcile(ticket_id)
        self.store.update_ticket(ticket_id, status=upd.status.value, broker_order_id=upd.broker_order_id)
        self.store.audit("broker", "ORDER_UPDATE", {"ticket_id": ticket_id, "status": upd.status.value,
                                                     "broker_order_id": upd.broker_order_id,
                                                     "filled": upd.filled_qty, "avg_price": upd.avg_price,
                                                     "message": upd.message})
        return self.store.get_ticket(ticket_id)

    def reconcile(self, ticket_id: str) -> dict:
        """Query the broker for the true state. Never resubmits."""
        t = self.store.get_ticket(ticket_id)
        broker = self.broker_for(t["mode"])
        try:
            upd = (broker.order_status(t["broker_order_id"]) if t["broker_order_id"]
                   else broker.find_by_idempotency_key(t["idempotency_key"]))
        except BrokerError as e:
            self.store.audit("system", "RECONCILE_FAILED", {"ticket_id": ticket_id, "error": str(e)})
            return t
        if upd is None:
            # Broker has no record: the order never reached it. Leave UNKNOWN→REJECTED; a human must re-confirm a NEW ticket.
            self.store.update_ticket(ticket_id, status=OrderStatus.REJECTED.value)
            self.store.audit("system", "RECONCILED_NOT_FOUND", {"ticket_id": ticket_id})
        else:
            self.store.update_ticket(ticket_id, status=upd.status.value, broker_order_id=upd.broker_order_id)
            self.store.audit("system", "RECONCILED", {"ticket_id": ticket_id, "status": upd.status.value,
                                                      "filled": upd.filled_qty})
        return self.store.get_ticket(ticket_id)

    def reject_ticket(self, ticket_id: str, actor: str = "user") -> dict:
        t = self.store.get_ticket(ticket_id)
        if t and t["status"] == OrderStatus.PENDING_APPROVAL.value:
            self.store.update_ticket(ticket_id, status=OrderStatus.CANCELLED.value)
            self.store.audit(actor, "TICKET_REJECTED_BY_USER", {"ticket_id": ticket_id})
        return self.store.get_ticket(ticket_id)

    def cancel_all(self, actor: str = "user") -> dict:
        cancelled, errors = [], []
        for mode, broker in (("PAPER", self.paper), ("LIVE", self.live)):
            if broker is None:
                continue
            try:
                for o in broker.open_orders():
                    broker.cancel_order(o.broker_order_id)
                    cancelled.append(o.broker_order_id)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{mode}: {e}")
        for t in self.store.list_tickets(1000):
            if t["status"] == OrderStatus.PENDING_APPROVAL.value:
                self.store.update_ticket(t["id"], status=OrderStatus.CANCELLED.value)
        self.store.audit(actor, "EMERGENCY_CANCEL_ALL", {"cancelled": cancelled, "errors": errors})
        return {"cancelled": cancelled, "errors": errors}
