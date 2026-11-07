# Cloudflare deployment (private research & paper trading)

```
Browser ──HTTPS──► markets.webloom.network  (Cloudflare Pages, project triagent-markets)
                     │  Cloudflare Access "TRI-AGENT dashboard": only pradeepandigital@gmail.com (one-time PIN)
                     │  static dashboard + Pages Function gateway on /api/* and /ws/*
                     ▼  (re-verifies the visitor's Access JWT, then calls the backend with a service token)
                   markets-api.webloom.network  (Cloudflare Access "TRI-AGENT API": service token only)
                     ▼  Cloudflare Tunnel "triagent-pc" (outbound-only from your PC, no open ports)
                   Windows PC ─ Docker Compose: cloudflared → FastAPI (AUTH_MODE=cloudflare_access) → TimescaleDB
```

The dashboard is reachable only after an Access login. The `*.pages.dev` production and preview URLs are
covered by the same Access app, so they are not an unprotected way around the custom domain. The backend
hostname admits only the gateway's service token, and the backend verifies that JWT again, signature included.

## Cloudflare resources (none of these are secrets)

| Resource | Value |
|---|---|
| Account | Pradeepandigital@gmail.com's Account |
| Pages project | `triagent-markets`: GitHub `pradeepannagarasu/tradingagents`, production branch `claude/affectionate-turing-3f1pya`, root `frontend`, build `npm run build`, output `dist`, Node 22, previews for all branches |
| Dashboard | https://markets.webloom.network (also https://triagent-markets.pages.dev, protected) |
| API route | same-origin `https://markets.webloom.network/api/*` → gateway → `https://markets-api.webloom.network` |
| Access team domain | `charlies-kitchen-pages.cloudflareaccess.com` |
| Access app (dashboard) | `TRI-AGENT dashboard` (09b32927-…): markets.webloom.network, triagent-markets.pages.dev, *.triagent-markets.pages.dev. Policy "Owner only": email = pradeepandigital@gmail.com. 24h session, HttpOnly cookie, One-time PIN |
| Access app (backend) | `TRI-AGENT API (backend via tunnel)` (2ab9835f-…): markets-api.webloom.network. Policy: Service Auth, token `triagent-pages-gateway` |
| Service token | `triagent-pages-gateway`, client id `15d675929ac6349b77910c539ad1250f.access`, valid 1 year. The secret exists only as an encrypted Pages secret |
| Tunnel | `triagent-pc` (749ccdc8-7cfc-4dff-9202-07ae16c7de0c), remotely managed. Ingress `markets-api.webloom.network → http://backend:8000`, with Access JWT enforcement at cloudflared |
| DNS (zone webloom.network) | **added only**: `CNAME markets → triagent-markets.pages.dev` (proxied), `CNAME markets-api → 749ccdc8-….cfargotunnel.com` (proxied). Existing website, MX and SPF records are untouched |

### Pages environment variables (production and preview)

`NODE_VERSION=22`, `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD` (dashboard app AUD), `ALLOWED_EMAILS`, `BACKEND_ORIGIN`,
`ACCESS_CLIENT_ID` are plain values. `ACCESS_CLIENT_SECRET` is an **encrypted secret**. There are **no `VITE_*`
variables**, so nothing secret is compiled into the JavaScript.

## Run the backend on your Windows PC

1. Install **Docker Desktop** with the WSL2 backend, and **Git**. Start Docker Desktop.
2. In PowerShell:

```powershell
git clone https://github.com/pradeepannagarasu/tradingagents.git
```

```powershell
cd tradingagents
git checkout claude/affectionate-turing-3f1pya
copy .env.example .env
notepad .env
```

3. Fill in `.env`. These are not secrets, except the last one and the two you generate:

```
POSTGRES_PASSWORD=<choose a long random password>
RISK_SIGNING_KEY=<run: python -c "import secrets;print(secrets.token_hex(32))", or any 64 hex chars>
CF_ACCESS_TEAM_DOMAIN=charlies-kitchen-pages.cloudflareaccess.com
CF_ACCESS_BACKEND_AUD=<AUD tag of "TRI-AGENT API": Zero Trust → Access → Applications → TRI-AGENT API → Overview>
CF_ACCESS_SERVICE_TOKEN_ID=15d675929ac6349b77910c539ad1250f.access
ALLOWED_EMAILS=pradeepandigital@gmail.com
DASHBOARD_ORIGIN=https://markets.webloom.network
CLOUDFLARE_TUNNEL_TOKEN=<Zero Trust → Networks → Tunnels → triagent-pc → Configure → copy the token from the install command>
```

The tunnel token is a secret. Keep it only in `.env`, which git ignores. Never paste it into chat or commit it.

4. Start the stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build
```

5. Load data. This uses Yahoo, which works from your PC. Alternatively, import the NSE CSV from the Data page:

```powershell
docker compose exec backend python -m triagent.cli ingest --source yahoo --symbol "NIFTY 50"
```

6. Open https://markets.webloom.network, enter your email, then the PIN from your inbox.

Check status with:

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml ps
```

Watch the tunnel with:

```powershell
docker compose logs -f tunnel
```

The tunnel shows **HEALTHY** in Zero Trust → Networks → Tunnels.

**Availability:** the backend is offline whenever the PC is off, asleep or disconnected. The dashboard then
shows "Backend unreachable". Data persists in the Docker volume `dbdata` on your PC; back it up with
`docker compose exec db pg_dump -U triagent triagent > backup.sql`.

## Safety locks in this deployment

* `TRIAGENT_PAPER_ONLY=true` and `LIVE_TRADING_ENABLED=false` are hard-coded in `docker-compose.yml`. LIVE mode
  makes the server refuse to start, and no live broker object is ever constructed (tested).
* No broker credentials are configured. Every order goes to the paper broker after an explicit `CONFIRM TRADE`.

## Teardown (reverse order)

Delete the DNS records `markets` and `markets-api`, the Access apps `TRI-AGENT dashboard` and `TRI-AGENT API`,
the service token `triagent-pages-gateway`, the tunnel `triagent-pc` and the Pages project `triagent-markets`.
Revoke the deployment API token.
