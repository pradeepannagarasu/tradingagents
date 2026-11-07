import numpy as np
import pandas as pd
import pytest

from triagent.quant import indicators as ind
from triagent.quant.regime import support_resistance, trend_label


def _ref_wilder(values, n):
    out = [np.nan] * len(values)
    out[n - 1] = sum(values[:n]) / n
    for i in range(n, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def test_sma_exact():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert ind.sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    assert np.isnan(ind.sma(s, 3).iloc[1])


def test_ema_seeded_with_sma_and_recursion():
    s = pd.Series([10, 11, 12, 13, 14, 15], dtype=float)
    e = ind.ema(s, 3)
    assert e.iloc[2] == pytest.approx(11.0)
    alpha = 0.5
    assert e.iloc[3] == pytest.approx(alpha * 13 + (1 - alpha) * 11.0)
    assert e.iloc[5] == pytest.approx(alpha * 15 + (1 - alpha) * (alpha * 14 + (1 - alpha) * 12.0))


def test_rsi_matches_reference_implementation(series_factory):
    close = series_factory(n=200).bars["close"]
    d = close.diff().iloc[1:].tolist()
    gains = [max(x, 0) for x in d]
    losses = [max(-x, 0) for x in d]
    ag, al = _ref_wilder(gains, 14), _ref_wilder(losses, 14)
    ref = [np.nan] + [100 - 100 / (1 + g / l) if l else 100.0 for g, l in zip(ag, al)]
    got = ind.rsi(close, 14)
    np.testing.assert_allclose(got.iloc[20:].to_numpy(), np.array(ref[20:]), rtol=1e-10)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100.0
    down = pd.Series(np.arange(50, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_macd_is_ema_difference(series_factory):
    c = series_factory(n=150).bars["close"]
    m = ind.macd(c)
    np.testing.assert_allclose((ind.ema(c, 12) - ind.ema(c, 26)).dropna(), m["macd"].dropna())
    np.testing.assert_allclose(m["hist"].dropna(), (m["macd"] - m["signal"]).dropna())


def test_atr_constant_range():
    idx = pd.bdate_range("2024-01-01", periods=40)
    df = pd.DataFrame({"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1}, index=idx)
    assert ind.atr(df, 14).iloc[-1] == pytest.approx(4.0)


def test_true_range_uses_previous_close():
    idx = pd.bdate_range("2024-01-01", periods=2)
    df = pd.DataFrame({"open": [100, 110], "high": [101, 112], "low": [99, 109], "close": [100, 111], "volume": 1},
                      index=idx, dtype=float)
    assert ind.true_range(df).iloc[1] == pytest.approx(12.0)  # |112 - 100|


def test_bollinger_known_values():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    b = ind.bollinger(s, n=5, k=2)
    sd = np.std([1, 2, 3, 4, 5])
    assert b["bb_mid"].iloc[-1] == pytest.approx(3.0)
    assert b["bb_upper"].iloc[-1] == pytest.approx(3 + 2 * sd)
    assert b["bb_pct_b"].iloc[-1] == pytest.approx((5 - (3 - 2 * sd)) / (4 * sd))


def test_adx_high_in_clean_trend_low_in_chop():
    idx = pd.bdate_range("2023-01-01", periods=120)
    c = pd.Series(np.linspace(100, 220, 120), index=idx)
    trend = pd.DataFrame({"open": c - 0.5, "high": c + 1, "low": c - 1, "close": c, "volume": 1})
    assert ind.adx(trend)["adx"].iloc[-1] > 40
    chop_c = pd.Series(100 + np.tile([1.0, -1.0], 60), index=idx)
    chop = pd.DataFrame({"open": chop_c, "high": chop_c + 1.5, "low": chop_c - 1.5, "close": chop_c, "volume": 1})
    assert ind.adx(chop)["adx"].iloc[-1] < 20


def test_vwap_session_reset():
    idx = pd.date_range("2024-01-01 09:15", periods=4, freq="5min")
    df = pd.DataFrame({"open": 1, "high": [10, 20, 10, 30], "low": [10, 20, 10, 30], "close": [10, 20, 10, 30],
                       "volume": [1, 1, 1, 3]}, index=idx, dtype=float)
    key = pd.Series(["d1", "d1", "d2", "d2"], index=idx)
    v = ind.vwap(df, key)
    assert v.iloc[1] == pytest.approx(15.0)
    assert v.iloc[3] == pytest.approx((10 + 90) / 4)


def test_all_features_are_causal(series_factory):
    """Appending future bars must not change any past feature value (no look-ahead)."""
    full = series_factory(n=700).bars
    cut = 550
    a = ind.compute_all(full.iloc[:cut])
    b = ind.compute_all(full).iloc[:cut]
    for col in a.columns:
        x, y = a[col].astype(float).to_numpy(), b[col].astype(float).to_numpy()
        np.testing.assert_allclose(x, y, equal_nan=True, err_msg=col)


def test_trend_label_and_levels(series_factory):
    f = ind.compute_all(series_factory(n=600, drift=0.002, vol=0.004).bars)
    assert "UPTREND" in trend_label(f.iloc[-1])
    lv = support_resistance(f)
    last = f["close"].iloc[-1]
    assert all((l.price < last) == (l.kind == "SUPPORT") for l in lv)
