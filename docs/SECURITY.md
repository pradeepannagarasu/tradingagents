# Security

* **Secrets**: read only from environment variables (`.env`, git-ignored). `.env.example` contains names, no values.
  The API exposes only *whether* a credential is present (`credentials_present`), never its value. The Telegram
  error path avoids logging the token-bearing URL. The frontend contains no secrets.
* **Risk tokens**: HMAC-SHA256 with `RISK_SIGNING_KEY`. If it is unset, a random per-process key is used, so
  approvals don't survive a restart (safe default). Set a 32-byte random key in production. Tokens never leave
  the backend: API responses strip `risk_token`.
* **Network exposure**: Docker publishes ports on `127.0.0.1` only. There is **no user authentication yet**, so
  do not expose the API or dashboard to the internet. Put it behind a VPN or an authenticating reverse proxy
  first (an open item before LIVE).
* **File import**: CSV import is confined to `TRIAGENT_IMPORT_DIR`. Path traversal is rejected (tested).
* **Integrity**: decisions, audit and risk events are append-only (DB triggers). The audit log is hash-chained and verifiable.
* **LLM**: optional. Outputs are untrusted text. They are validated, can't change numbers or decisions, and
  prompts are versioned. Don't send account identifiers to an LLM provider.
* **Broker sessions**: Kite access tokens expire daily, and a 403 raises `AuthExpired` (no retry loop). Live
  orders additionally require a static IP registered with the broker.
* **Screenshots/logs**: the UI never shows credentials. Broker order IDs and account labels are not secrets, but treat them as private.
