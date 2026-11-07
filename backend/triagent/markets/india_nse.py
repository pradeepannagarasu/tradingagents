"""India / NSE market profile: sessions, cost schedule, shorting rules, proxies."""
from __future__ import annotations

import tomllib
from bisect import bisect_right
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from triagent.config import CONFIG_DIR
from triagent.core.models import Direction, Horizon, Instrument, InstrumentType
from triagent.markets.base import CostBreakdown, MarketProfile

IST = ZoneInfo("Asia/Kolkata")


def _schedule_lookup(schedule: list[list], d: date) -> float:
    """Return the value in force on date d from [[YYYY-MM-DD, value], ...] (sorted)."""
    dates = [date.fromisoformat(s[0]) for s in schedule]
    i = bisect_right(dates, d) - 1
    if i < 0:
        return float(schedule[0][1])
    return float(schedule[i][1])


class IndiaNSEProfile(MarketProfile):
    id = "IN_NSE"
    timezone = "Asia/Kolkata"
    currency = "INR"
    benchmark_symbol = "NIFTY 50"

    def __init__(self, cost_file: Path | None = None, holidays: set[date] | None = None):
        path = cost_file or (CONFIG_DIR / "costs_in_nse.toml")
        self.costs = tomllib.loads(Path(path).read_text())
        self.holidays: set[date] = set(holidays or set())
        hfile = CONFIG_DIR / "holidays_in_nse.txt"
        if holidays is None and hfile.exists():
            for line in hfile.read_text().splitlines():
                line = line.split("#")[0].strip()
                if line:
                    self.holidays.add(date.fromisoformat(line))

    # sessions ---------------------------------------------------------------
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session(self, d: date):
        if not self.is_trading_day(d):
            return None
        return (datetime.combine(d, time(9, 15), IST), datetime.combine(d, time(15, 30), IST))

    # contracts ----------------------------------------------------------------
    def nifty_lot_size(self, d: date) -> int:
        return int(_schedule_lookup(self.costs["contracts"]["nifty_lot_size"], d))

    def expiry_weekday(self, d: date) -> int:
        return int(_schedule_lookup(self.costs["contracts"]["nifty_expiry_weekday"], d))

    def is_expiry_day(self, d: date) -> bool:
        """Approximate weekly-expiry check (holiday shifts not modelled — verify with NSE)."""
        return d.weekday() == self.expiry_weekday(d)

    # shorting ---------------------------------------------------------------
    def can_hold_short(self, instrument: Instrument, horizon: Horizon) -> tuple[bool, str]:
        t = instrument.instrument_type
        if t == InstrumentType.INDEX:
            return False, "An index is not tradable; use futures/options/ETF proxy."
        if t in (InstrumentType.FUTURE, InstrumentType.OPTION):
            return True, "Short via F&O is permitted subject to margin."
        if horizon == Horizon.INTRADAY:
            return True, "Intraday equity short sale permitted (must square off same session)."
        return False, (
            "Overnight short selling of cash equities/ETFs is not permitted for retail in India "
            "(delivery default risk). Express bearish views via futures or put options instead."
        )

    def tradable_proxies(self, index_symbol: str) -> list[Instrument]:
        if index_symbol.upper().replace(" ", "") in {"NIFTY50", "NIFTY", "^NSEI"}:
            today = datetime.now(IST).date()
            return [
                Instrument(symbol="NIFTYBEES", name="Nippon India ETF Nifty 50 BeES",
                           instrument_type=InstrumentType.ETF, lot_size=1, tick_size=0.01),
                Instrument(symbol="NIFTY-FUT", name="NIFTY near-month future", underlying="NIFTY 50",
                           instrument_type=InstrumentType.FUTURE, lot_size=self.nifty_lot_size(today)),
            ]
        return []

    # costs ------------------------------------------------------------------
    def round_trip_costs(self, instrument, horizon, price_in, price_out, qty, trade_date,
                         direction=Direction.LONG) -> CostBreakdown:
        c = self.costs
        cb = CostBreakdown(notes=["Rates from config/costs_in_nse.toml — verify before relying on them."])
        buy_px, sell_px = (price_in, price_out) if direction == Direction.LONG else (price_out, price_in)
        buy_val, sell_val = buy_px * qty, sell_px * qty
        turnover = buy_val + sell_val
        t = instrument.instrument_type
        d = trade_date
        b = c["brokerage"]

        def order_brokerage(val: float, pct: float, cap: float) -> float:
            return min(val * pct, cap) if cap else val * pct

        if t == InstrumentType.OPTION:
            cb.brokerage = 2 * b["options_flat"]
            cb.transaction_tax = sell_val * _schedule_lookup(c["stt"]["options_sell_premium"], d)
            cb.exchange_charges = turnover * _schedule_lookup(c["exchange_txn"]["options_premium"], d)
            cb.stamp_duty = buy_val * _schedule_lookup(c["stamp_duty_buy"]["options"], d)
        elif t == InstrumentType.FUTURE:
            cb.brokerage = order_brokerage(buy_val, b["futures_pct"], b["futures_flat_max"]) + order_brokerage(
                sell_val, b["futures_pct"], b["futures_flat_max"])
            cb.transaction_tax = sell_val * _schedule_lookup(c["stt"]["futures_sell"], d)
            cb.exchange_charges = turnover * _schedule_lookup(c["exchange_txn"]["futures"], d)
            cb.stamp_duty = buy_val * _schedule_lookup(c["stamp_duty_buy"]["futures"], d)
        elif horizon == Horizon.INTRADAY:
            cb.brokerage = order_brokerage(buy_val, b["intraday_pct"], b["intraday_flat_max"]) + order_brokerage(
                sell_val, b["intraday_pct"], b["intraday_flat_max"])
            cb.transaction_tax = sell_val * _schedule_lookup(c["stt"]["intraday_sell"], d)
            cb.exchange_charges = turnover * _schedule_lookup(c["exchange_txn"]["equity"], d)
            cb.stamp_duty = buy_val * _schedule_lookup(c["stamp_duty_buy"]["intraday"], d)
        else:  # delivery equity / ETF
            cb.brokerage = order_brokerage(buy_val, b["delivery_pct"], b["delivery_flat_max"]) + order_brokerage(
                sell_val, b["delivery_pct"], b["delivery_flat_max"])
            if t == InstrumentType.ETF:
                # Equity-oriented fund units sold on exchange: STT 0.001% on sell side.
                cb.transaction_tax = sell_val * 0.00001
                cb.notes.append("ETF: STT 0.001% on sell side assumed — verify current treatment.")
            else:
                cb.transaction_tax = turnover * _schedule_lookup(c["stt"]["delivery_both_sides"], d)
            cb.exchange_charges = turnover * _schedule_lookup(c["exchange_txn"]["equity"], d)
            cb.stamp_duty = buy_val * _schedule_lookup(c["stamp_duty_buy"]["delivery"], d)
            cb.other = c["dp"]["delivery_sell_per_scrip"]
        cb.regulator_fee = turnover * c["sebi"]["fee_rate"]
        cb.gst = (cb.brokerage + cb.exchange_charges + cb.regulator_fee) * c["gst"]["rate"]
        return cb
