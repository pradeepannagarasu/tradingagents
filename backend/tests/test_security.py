"""Cloud-exposure security: Access JWT auth, allowlists, rate limits, headers, error hygiene, paper-only lock."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from triagent.api.security import AccessVerifier, AuthConfig, RateLimiter, SecurityMiddleware

TEAM = "team.cloudflareaccess.com"
AUD = "aud-123"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks():
    import json
    k = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    k["kid"] = "k1"
    return {"keys": [k]}


def token(key=KEY, **claims):
    base = {"aud": [AUD], "iss": f"https://{TEAM}", "iat": int(time.time()), "exp": int(time.time()) + 300}
    base.update(claims)
    return jwt.encode(base, key, algorithm="RS256", headers={"kid": "k1"})


CFG = AuthConfig(mode="cloudflare_access", team_domain=TEAM, audiences=[AUD],
                 allowed_emails={"me@example.com"}, service_token_ids={"gw.access"})


def make_client(limiter=None):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/data")
    def data():
        return {"secret": "visible only when authorised"}

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("db password=hunter2 leaked?")

    app.add_middleware(SecurityMiddleware, cfg=CFG, verifier=AccessVerifier(CFG, jwks_fetcher=jwks),
                       limiter=limiter or RateLimiter())
    return TestClient(app, raise_server_exceptions=False)


def test_health_public_everything_else_requires_access_jwt():
    c = make_client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/data").status_code == 401
    assert c.get("/docs").status_code == 401
    assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": "garbage"}).status_code == 401


def test_valid_user_allowed_and_others_denied():
    c = make_client()
    ok = c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": token(email="me@example.com")})
    assert ok.status_code == 200
    for bad in (token(email="attacker@example.com"), token(KEY, email="me@example.com", aud=["other-app"]),
                token(OTHER, email="me@example.com"), token(email="me@example.com", exp=int(time.time()) - 10),
                token(email="me@example.com", iss="https://evil.cloudflareaccess.com")):
        assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": bad}).status_code == 401


def test_service_token_requires_known_gateway_and_allowed_user():
    c = make_client()
    gw = token(common_name="gw.access")
    assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": gw, "X-Triagent-User": "me@example.com"}).status_code == 200
    assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": gw}).status_code == 401
    assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": gw, "X-Triagent-User": "x@y.z"}).status_code == 401
    rogue = token(common_name="someone-else.access")
    assert c.get("/api/data", headers={"Cf-Access-Jwt-Assertion": rogue, "X-Triagent-User": "me@example.com"}).status_code == 401


def test_security_headers_and_generic_500():
    c = make_client()
    r = c.get("/api/health")
    for h in ("x-content-type-options", "x-frame-options", "strict-transport-security", "content-security-policy"):
        assert h in r.headers
    boom = c.get("/api/boom", headers={"Cf-Access-Jwt-Assertion": token(email="me@example.com")})
    assert boom.status_code == 500 and "hunter2" not in boom.text and "Traceback" not in boom.text


def test_rate_limits():
    rl = RateLimiter()
    rl.LIMITS = {"heavy": (2, 60), "order": (1, 60), "default": (3, 60)}
    c = make_client(rl)
    h = {"Cf-Access-Jwt-Assertion": token(email="me@example.com")}
    codes = [c.get("/api/data", headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert c.get("/api/data", headers=h).headers.get("retry-after")
    assert rl.classify("/api/research/run") == "heavy" and rl.classify("/api/orders/tickets/x/confirm") == "order"


def test_misconfigured_access_mode_refuses_to_start(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "cloudflare_access")
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    with pytest.raises(RuntimeError):
        AuthConfig.from_env()


def test_paper_only_lock(monkeypatch, settings, store):
    from triagent.execution.broker_base import LiveTradingDisabled
    from triagent.pipeline import Services
    monkeypatch.setenv("TRIAGENT_PAPER_ONLY", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    from triagent.config import load_settings
    s = load_settings(overrides={"system": {"live_trading_enabled": True}})
    assert s.live_trading_enabled is False
    svc = Services.create(s, store)
    assert svc.orders.live is None
    with pytest.raises(LiveTradingDisabled):
        svc.orders.broker_for("LIVE")
    monkeypatch.setenv("TRIAGENT_MODE", "LIVE")
    with pytest.raises(RuntimeError):
        _ = s.mode
