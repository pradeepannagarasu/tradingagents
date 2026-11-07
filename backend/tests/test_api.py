"""End-to-end API flow on SIMULATED data: ingest → research → ticket → confirm → emergency stop."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    from triagent import config
    config.get_settings.cache_clear()
    from triagent.api.main import app
    with TestClient(app) as c:
        yield c
    config.get_settings.cache_clear()


def test_full_flow(client):
    sysinfo = client.get("/api/system").json()
    assert sysinfo["mode"] == "PAPER" and sysinfo["live_trading_enabled"] is False
    r = client.post("/api/data/ingest", json={"source": "synthetic", "symbol": "NIFTY 50"})
    assert r.status_code == 200 and r.json()["dataset"]["simulated"] is True
    inv = client.get("/api/data/inventory").json()
    assert inv["loaded"][0]["simulated"] is True and len(inv["sources"]) > 10
    full = client.get("/api/market/bars/NIFTY 50?limit=100000")
    assert full.status_code == 200 and full.json()["bars"][0]["sma_200"] is None
    assert client.get("/api/market/overview").json()["items"]["NIFTY 50"]["simulated"] is True
    assert client.post("/api/research/run", json={"symbol": "NIFTY 50", "horizon": "INTRADAY"}).status_code == 400

    # scan history until a decision the risk engine approves (simulated series)
    bars = client.get("/api/market/bars/NIFTY 50?limit=2000").json()["bars"]
    approved = None
    for b in bars[300::40]:
        rec = client.post("/api/research/run", json={"symbol": "NIFTY 50", "horizon": "SWING", "as_of": b["time"]}).json()
        assert rec["final_decision"] in ("BUY / LONG", "SELL / SHORT", "NO TRADE")
        assert rec["data_quality"]["last_bar"] <= b["time"]  # quality is point-in-time
        assert rec["snapshot_hash"] and rec["bull"]["agent"] == "BULL" and rec["bear"]["agent"] == "BEAR"
        if rec["risk"] and rec["risk"]["approved"]:
            approved = rec
            break
    assert client.get(f"/api/decisions/{rec['id']}").json()["id"] == rec["id"]
    if approved is None:
        pytest.skip("no risk-approved decision found in sampled dates")
    stale = client.post("/api/orders/tickets", json={"decision_id": approved["id"]})
    assert stale.status_code == 400 and "stale" in stale.json()["detail"]
    approved = _current_approved(client)
    t = client.post("/api/orders/tickets", json={"decision_id": approved["id"]}).json()
    assert "risk_token" not in t["ticket_json"]
    bad = client.post(f"/api/orders/tickets/{t['id']}/confirm", json={"confirmation": "ok", "ticket_hash": t["ticket_hash"]})
    assert bad.status_code == 403
    client.post("/api/emergency/stop-all", json={"reason": "drill"})
    blocked = client.post(f"/api/orders/tickets/{t['id']}/confirm",
                          json={"confirmation": "CONFIRM TRADE", "ticket_hash": t["ticket_hash"]})
    assert blocked.status_code == 403
    assert client.get("/api/system").json()["trading_halted"] is True
    audit = client.get("/api/audit").json()
    assert audit["chain_valid"] and any(e["event"] == "EMERGENCY_STOP_ALL_TRADING" for e in audit["entries"])


def _current_approved(client):
    """Find a risk-approved decision on CURRENT data by trying simulated series with different seeds."""
    from triagent.api import main
    from triagent.data.connectors import SyntheticConnector
    for seed in range(40):
        main.svc.ingest(SyntheticConnector(seed=seed).fetch("NIFTY 50"))
        for h in ("SWING", "POSITIONAL", "SHORT"):
            rec = client.post("/api/research/run", json={"symbol": "NIFTY 50", "horizon": h}).json()
            if rec["risk"] and rec["risk"]["approved"]:
                return rec
    pytest.skip("no risk-approved current decision across 40 simulated seeds")


def test_paper_order_executes_after_confirmation(client):
    rec = _current_approved(client)
    t = client.post("/api/orders/tickets", json={"decision_id": rec["id"]}).json()
    ok = client.post(f"/api/orders/tickets/{t['id']}/confirm",
                     json={"confirmation": "CONFIRM TRADE", "ticket_hash": t["ticket_hash"]})
    assert ok.status_code == 200 and ok.json()["status"] in ("FILLED", "OPEN", "PARTIALLY_FILLED")
    assert ok.json()["broker_order_id"].startswith("PAPER-")


def test_csv_ingest_path_traversal_blocked(client):
    r = client.post("/api/data/ingest", json={"source": "csv", "path": "../../etc/passwd"})
    assert r.status_code == 400
