"""Runs only when TEST_POSTGRES_URL is set (e.g. postgresql+psycopg://user@host:port/db)."""
import os

import pytest
from sqlalchemy import text

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")


def test_postgres_store_and_append_only(series_factory):
    from triagent.data.store import Store
    s = Store(URL)
    ps = series_factory(n=60, symbol="PGTEST")
    s.save_series(ps)
    assert len(s.load_series("PGTEST").bars) == 60
    s.audit("t", "PG_AUDIT", {"a": 1})
    assert s.verify_audit_chain()[0]
    with pytest.raises(Exception, match="append-only"):
        with s.engine.begin() as c:
            c.execute(text("UPDATE audit_log SET event='X'"))
    with pytest.raises(Exception, match="append-only"):
        with s.engine.begin() as c:
            c.execute(text("DELETE FROM audit_log"))  # row trigger needs ≥1 row
