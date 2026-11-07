from functools import lru_cache

from triagent.markets.base import CostBreakdown, MarketProfile
from triagent.markets.india_nse import IndiaNSEProfile

PROFILES = {"IN_NSE": IndiaNSEProfile}


@lru_cache(maxsize=4)
def get_profile(market_id: str = "IN_NSE") -> MarketProfile:
    if market_id not in PROFILES:
        raise ValueError(f"Unknown market profile {market_id!r}. Available: {sorted(PROFILES)}")
    return PROFILES[market_id]()


__all__ = ["CostBreakdown", "MarketProfile", "IndiaNSEProfile", "get_profile"]
