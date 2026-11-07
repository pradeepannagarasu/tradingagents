import numpy as np
import pandas as pd
import pytest

from triagent.config import load_settings
from triagent.data.connectors.base import PriceSeries
from triagent.data.store import Store
from triagent.markets import IndiaNSEProfile


@pytest.fixture
def profile():
    return IndiaNSEProfile(holidays=set())


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def store():
    return Store("sqlite:///:memory:")


def make_series(n=900, drift=0.0006, vol=0.01, seed=1, end=None, symbol="TEST", simulated=True):
    rng = np.random.default_rng(seed)
    end = end or (pd.Timestamp.now(tz="Asia/Kolkata").normalize() - pd.offsets.BDay(1)).tz_localize(None)
    idx = pd.bdate_range(end=end, periods=n)
    r = drift + vol * rng.standard_normal(n)
    close = 1000 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + 0.002 * rng.standard_normal(n))
    high = np.maximum(open_, close) * (1 + np.abs(0.004 * rng.standard_normal(n)))
    low = np.minimum(open_, close) * (1 - np.abs(0.004 * rng.standard_normal(n)))
    vol_ = rng.integers(1_000_000, 3_000_000, n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol_}, index=idx)
    return PriceSeries(symbol=symbol, timeframe="1d", bars=df, source="test-fixture", licence="test",
                       adjusted=True, simulated=simulated)


@pytest.fixture
def series_factory():
    return make_series
