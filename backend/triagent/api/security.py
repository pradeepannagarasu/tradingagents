"""Server-side security for cloud exposure (pure ASGI, covers HTTP and WebSocket).

AUTH_MODE
  none               local development only (default). Never use when the API is reachable from a network.
  cloudflare_access  every request except GET /api/health must carry a valid Cloudflare Access JWT
                     (header Cf-Access-Jwt-Assertion, added by Access at the edge). The JWT signature is
                     checked against the team's public keys, plus audience, issuer and expiry.
                       * user JWT          → its email must be in ALLOWED_EMAILS
                       * service-token JWT → its client id must be in CF_ACCESS_SERVICE_TOKEN_IDS (the Pages
                         gateway); the end-user email it forwards in X-Triagent-User must be in ALLOWED_EMAILS

Also: per-identity rate limits, security headers, and a generic 500 handler that never leaks internals.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

PUBLIC_PATHS = {"/api/health"}


@dataclass
class AuthConfig:
    mode: str
    team_domain: str
    audiences: list[str]
    allowed_emails: set[str]
    service_token_ids: set[str]

    @classmethod
    def from_env(cls) -> "AuthConfig":
        split = lambda v: [x.strip() for x in os.environ.get(v, "").split(",") if x.strip()]  # noqa: E731
        cfg = cls(mode=os.environ.get("AUTH_MODE", "none").strip().lower(),
                  team_domain=os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().removeprefix("https://").rstrip("/"),
                  audiences=split("CF_ACCESS_AUD"), allowed_emails={e.lower() for e in split("ALLOWED_EMAILS")},
                  service_token_ids=set(split("CF_ACCESS_SERVICE_TOKEN_IDS")))
        if cfg.mode not in ("none", "cloudflare_access"):
            raise RuntimeError(f"Unknown AUTH_MODE {cfg.mode!r}")
        if cfg.mode == "cloudflare_access" and not (cfg.team_domain and cfg.audiences and cfg.allowed_emails):
            raise RuntimeError("AUTH_MODE=cloudflare_access requires CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD and ALLOWED_EMAILS")
        return cfg


class AccessVerifier:
    """Verifies Cloudflare Access JWTs (RS256) with cached JWKS."""

    def __init__(self, cfg: AuthConfig, jwks_fetcher=None):
        self.cfg = cfg
        self._fetch = jwks_fetcher or self._fetch_jwks
        self._keys: dict | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch_jwks(self) -> dict:
        r = httpx.get(f"https://{self.cfg.team_domain}/cdn-cgi/access/certs", timeout=10)
        r.raise_for_status()
        return r.json()

    def _key_for(self, kid: str, retry: bool = True):
        import jwt
        with self._lock:
            if self._keys is None or time.time() - self._fetched_at > 3600:
                self._keys = {k["kid"]: k for k in self._fetch()["keys"]}
                self._fetched_at = time.time()
            k = self._keys.get(kid)
        if k is None and retry:  # key rotation
            with self._lock:
                self._keys = None
            return self._key_for(kid, retry=False)
        if k is None:
            raise PermissionError("unknown signing key")
        return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))

    def identity(self, token: str | None, forwarded_user: str | None) -> str:
        import jwt
        if not token:
            raise PermissionError("missing Access token")
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
            claims = jwt.decode(token, self._key_for(kid), algorithms=["RS256"], audience=self.cfg.audiences,
                                issuer=f"https://{self.cfg.team_domain}", options={"require": ["exp", "iat", "aud"]})
        except (jwt.PyJWTError, KeyError, ValueError) as e:
            raise PermissionError(f"invalid Access token ({type(e).__name__})") from None
        email = (claims.get("email") or "").lower()
        if email:
            if email not in self.cfg.allowed_emails:
                raise PermissionError("identity not allowed")
            return email
        cn = claims.get("common_name", "")
        if cn and cn in self.cfg.service_token_ids:
            user = (forwarded_user or "").lower()
            if user not in self.cfg.allowed_emails:
                raise PermissionError("gateway did not forward an allowed user")
            return user
        raise PermissionError("identity not allowed")


class RateLimiter:
    """Fixed-window per identity and route class. In-memory (single-process deployment)."""

    LIMITS = {"heavy": (10, 60), "order": (6, 60), "default": (180, 60)}
    HEAVY = ("/api/research/run", "/api/backtest", "/api/calibration", "/api/outcomes/evaluate", "/api/news/refresh",
             "/api/data/ingest")

    def __init__(self):
        self._hits: dict[tuple, list[float]] = {}
        self._lock = threading.Lock()

    def classify(self, path: str) -> str:
        if path.startswith(self.HEAVY):
            return "heavy"
        if path.startswith("/api/orders/tickets") or path.startswith("/api/emergency/resume"):
            return "order"
        return "default"

    def allow(self, who: str, path: str) -> tuple[bool, int]:
        cls = self.classify(path)
        limit, window = self.LIMITS[cls]
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get((who, cls), []) if now - t < window]
            if len(hits) >= limit:
                self._hits[(who, cls)] = hits
                return False, int(window - (now - hits[0])) + 1
            hits.append(now)
            self._hits[(who, cls)] = hits
        return True, 0


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]
DOCS_CSP = (b"content-security-policy",
            b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            b"frame-ancestors 'none'")


class SecurityMiddleware:
    def __init__(self, app, cfg: AuthConfig | None = None, verifier: AccessVerifier | None = None,
                 limiter: RateLimiter | None = None):
        self.app = app
        self.cfg = cfg or AuthConfig.from_env()
        self.verifier = verifier or (AccessVerifier(self.cfg) if self.cfg.mode == "cloudflare_access" else None)
        self.limiter = limiter or RateLimiter()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        who = "local"
        if self.verifier is not None and not (scope["type"] == "http" and path in PUBLIC_PATHS):
            try:
                who = self.verifier.identity(headers.get("cf-access-jwt-assertion"), headers.get("x-triagent-user"))
            except PermissionError as e:
                return await self._deny(scope, send, 401, str(e))
        ok, retry = self.limiter.allow(who, path)
        if not ok:
            return await self._deny(scope, send, 429, "rate limit exceeded", retry)
        scope.setdefault("state", {})["user"] = who

        is_docs = path in ("/docs", "/redoc") or path.startswith("/docs/")

        started = False

        async def send_wrapped(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                hs = [h for h in message.get("headers", []) if h[0].lower() not in {k for k, _ in SECURITY_HEADERS}]
                hs += [DOCS_CSP if (is_docs and k == b"content-security-policy") else (k, v) for k, v in SECURITY_HEADERS]
                message["headers"] = hs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        except Exception:  # noqa: BLE001 — never leak internals
            rid = uuid.uuid4().hex[:12]
            import logging
            logging.getLogger("triagent").exception("unhandled error request_id=%s path=%s", rid, path)
            if scope["type"] == "http" and not started:
                await self._json(send, 500, {"detail": "Internal server error", "request_id": rid})

    async def _deny(self, scope, send, status, detail, retry_after: int = 0):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4429})
            return
        extra = [(b"retry-after", str(retry_after).encode())] if retry_after else []
        await self._json(send, status, {"detail": detail}, extra)

    @staticmethod
    async def _json(send, status, body, extra=None):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
                    + SECURITY_HEADERS + (extra or [])})
        await send({"type": "http.response.body", "body": data})
