"""Emergency controls persisted in the database so they survive restarts."""
from __future__ import annotations

from triagent.data.store import Store

KEY_TRADING_HALTED = "trading_halted"
KEY_LIVE_DISABLED = "live_execution_disabled"


class EmergencyControls:
    def __init__(self, store: Store):
        self.store = store

    def trading_halted(self) -> bool:
        return bool(self.store.get_state(KEY_TRADING_HALTED, False))

    def live_disabled(self) -> bool:
        # Default True: live execution is disabled until explicitly re-enabled (and env/config also allow it).
        return bool(self.store.get_state(KEY_LIVE_DISABLED, True))

    def stop_all_trading(self, actor: str, reason: str) -> None:
        self.store.set_state(KEY_TRADING_HALTED, True)
        self.store.audit(actor, "EMERGENCY_STOP_ALL_TRADING", {"reason": reason})

    def resume_trading(self, actor: str, reason: str) -> None:
        self.store.set_state(KEY_TRADING_HALTED, False)
        self.store.audit(actor, "TRADING_RESUMED", {"reason": reason})

    def disable_live(self, actor: str, reason: str) -> None:
        self.store.set_state(KEY_LIVE_DISABLED, True)
        self.store.audit(actor, "LIVE_EXECUTION_DISABLED", {"reason": reason})

    def enable_live(self, actor: str, reason: str) -> None:
        self.store.set_state(KEY_LIVE_DISABLED, False)
        self.store.audit(actor, "LIVE_EXECUTION_ENABLE_REQUESTED", {"reason": reason})
