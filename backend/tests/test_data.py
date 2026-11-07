from datetime import datetime, timezone

import httpx
import pandas as pd
import pytest

from triagent.core.models import InstrumentType, QualityStatus
from triagent.data.connectors import CsvConnector, SyntheticConnector, YahooChartConnector
from triagent.data.connectors.base import ConnectorUnavailable, PriceSeries
from triagent.data.quality import apply_corporate_actions, check_price_series

CFG = {"max_staleness_trading_days": 3, "abnormal_move_pct_index": 12, "min_bars_required": 50}


def test_clean_series_passes_or_warns_only_for_simulation(profile, series_factory):
    q = check_price_series(series_factory(n=300, simulated=False), profile, CFG, InstrumentType.EQUITY)
    assert q.status == QualityStatus.PASS, q.issues
    q2 = check_price_series(series_factory(n=300), profile, CFG)
    assert q2.status == QualityStatus.WARN and any("SIMULATED" in i for i in q2.issues)


def test_detects_ohlc_violation_duplicates_and_gaps(profile, series_factory):
    ps = series_factory(n=300, simulated=False)
    df = ps.bars.copy()
    df.iloc[10, df.columns.get_loc("high")] = df.iloc[10]["low"] - 1
    df = df.drop(df.index[100:110])                        # 10-day hole
    bad = PriceSeries("X", "1d", df, "t", "t", adjusted=True)
    q = check_price_series(bad, profile, CFG, InstrumentType.EQUITY)
    assert any("OHLC" in i for i in q.issues)
    assert q.gaps and q.gaps[0]["missing_days"] == 10


def test_detects_stale_data(profile, series_factory):
    ps = series_factory(n=300, end=pd.Timestamp("2025-01-10"), simulated=False)
    q = check_price_series(ps, profile, CFG, as_of=datetime(2025, 2, 1, tzinfo=timezone.utc))
    assert q.stale


def test_insufficient_history_fails(profile, series_factory):
    q = check_price_series(series_factory(n=20), profile, CFG)
    assert q.status == QualityStatus.FAIL


def test_abnormal_move_flagged(profile, series_factory):
    ps = series_factory(n=300, simulated=False)
    ps.bars.iloc[200:, :4] *= 0.5   # looks like an unadjusted 1:2 split
    q = check_price_series(ps, profile, CFG, InstrumentType.INDEX)
    assert any("Abnormal move" in i for i in q.issues)


def test_corporate_action_adjustment_removes_split_jump():
    idx = pd.bdate_range("2024-01-01", periods=4)
    df = pd.DataFrame({"open": [200, 200, 100, 100], "high": [200, 200, 100, 100], "low": [200, 200, 100, 100],
                       "close": [200, 200, 100, 100], "volume": [10, 10, 20, 20]}, index=idx, dtype=float)
    out = apply_corporate_actions(df, [{"ex_date": str(idx[2].date()), "type": "split", "ratio": 2}])
    assert out["close"].tolist() == [100, 100, 100, 100]
    assert out["volume"].tolist() == [20, 20, 20, 20]


def test_csv_connector_parses_nse_index_format(tmp_path):
    p = tmp_path / "nifty.csv"
    p.write_text("Date ,Open ,High ,Low ,Close ,Shares Traded ,Turnover (₹ Cr)\n"
                 "02-JAN-2024,21751.35,21755.6,21555.65,21665.8,\"3,13,85,563\",28441.17\n"
                 "01-JAN-2024,21727.75,21834.35,21680.85,21741.9,\"1,54,74,436\",13690.83\n")
    ps = CsvConnector(p, source_name="NSE Indices").fetch("NIFTY 50")
    assert list(ps.bars.index.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-02"]
    assert ps.bars["close"].iloc[0] == pytest.approx(21741.9)
    assert not ps.simulated


def test_yahoo_connector_with_mock_transport():
    payload = {"chart": {"result": [{"timestamp": [1704167100, 1704253500],
                                     "indicators": {"quote": [{"open": [1, 2], "high": [2, 3], "low": [0.5, 1.5],
                                                               "close": [1.5, 2.5], "volume": [10, 20]}],
                                                    "adjclose": [{"adjclose": [1.5, 2.5]}]}}], "error": None}}
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    ps = YahooChartConnector(client).fetch("NIFTY 50")
    assert len(ps.bars) == 2 and ps.adjusted and "unofficial" in ps.source.lower()


def test_yahoo_connector_failure_raises_not_fakes():
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    with pytest.raises(ConnectorUnavailable):
        YahooChartConnector(client).fetch("NIFTY 50")


def test_synthetic_is_labelled():
    ps = SyntheticConnector().fetch("NIFTY 50", start="2020-01-01", end="2021-01-01")
    assert ps.simulated and "SIMULATED" in ps.source


def test_store_roundtrip_and_append_only(store, series_factory):
    ps = series_factory(n=50)
    store.save_series(ps)
    back = store.load_series("TEST")
    pd.testing.assert_frame_equal(back.bars.astype(float), ps.bars.astype(float), check_freq=False, check_names=False)
    store.audit("t", "A", {"x": 1})
    store.audit("t", "B", {"y": 2})
    assert store.verify_audit_chain() == (True, 2)
    from sqlalchemy import text
    with pytest.raises(Exception):
        with store.engine.begin() as c:
            c.execute(text("UPDATE audit_log SET event='HACK'"))
