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

**Requirements:**
- Windows 10 22H2 or Windows 11, 64-bit, with CPU virtualisation enabled.
- 8 GB RAM recommended (4 GB minimum).
- At least **15 GB free on C:**. Docker Desktop and WSL use about 5 GB; the images (Python, TimescaleDB, cloudflared) about 2 GB; and the 20-year daily database is under 100 MB.
- An admin account, needed once to install Docker and WSL.

All commands are PowerShell. Run them one block at a time.

### 1. Check the PC

```powershell
Get-ComputerInfo -Property OsName,OsVersion,OsArchitecture,CsTotalPhysicalMemory
```

```powershell
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
```

```powershell
(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled
```

```powershell
docker --version
```

```powershell
git --version
```

```powershell
wsl --status
```

### 2. Install anything missing (admin PowerShell, then reboot)

```powershell
wsl --install
```

```powershell
winget install -e --id Git.Git
```

```powershell
winget install -e --id Docker.DockerDesktop
```

After rebooting, start **Docker Desktop**, accept the terms, and wait for "Engine running". Then check it:

```powershell
docker run --rm hello-world
```

### 3. Get the code

```powershell
cd $HOME
git clone -b claude/affectionate-turing-3f1pya https://github.com/pradeepannagarasu/tradingagents.git
cd tradingagents
git log --oneline -1
```

### 4. Create `.env`

```powershell
Copy-Item .env.example .env
```

Generate two random values for the next step. Hex only, so they are safe inside a database URL:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | % { $_.ToString('x2') }) -join ''
```

Run it twice: once for `POSTGRES_PASSWORD`, once for `RISK_SIGNING_KEY`. Then open the file:

```powershell
notepad .env
```

Set these lines. Use no quotes and no spaces around `=`. Keep the other lines as they are:

```
POSTGRES_PASSWORD=<first value>
RISK_SIGNING_KEY=<second value>
CF_ACCESS_TEAM_DOMAIN=charlies-kitchen-pages.cloudflareaccess.com
CF_ACCESS_BACKEND_AUD=b82a98b71d77198f568b41395f9ca70dc97b98d9fb5019f5de9c8a3dd234f757
CF_ACCESS_SERVICE_TOKEN_ID=15d675929ac6349b77910c539ad1250f.access
ALLOWED_EMAILS=pradeepandigital@gmail.com
DASHBOARD_ORIGIN=https://markets.webloom.network
CLOUDFLARE_TUNNEL_TOKEN=<see step 5>
```

### 5. Tunnel token (secret: never paste it into chat or commit it)

1. Go to https://one.dash.cloudflare.com, then **Networks → Tunnels**. Newer dashboards label this **Networks → Connectors → Cloudflare Tunnels**.
2. Open tunnel **`triagent-pc`**, then **Configure** (or **Edit**).
3. Under *Install and run a connector*, pick **Docker**. The command shown ends in `--token eyJ...`.
4. Copy **only the long string after `--token`** into the `CLOUDFLARE_TUNNEL_TOKEN=` line in Notepad, and save.
5. Don't run the command Cloudflare shows. Compose starts the connector for you.

### 6. Start PostgreSQL, FastAPI and the tunnel

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build
```

The first build takes 3–10 minutes. Then check the stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml ps
```

`db` should show `healthy`, and `backend` and `tunnel` `running`. Check the tunnel log:

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml logs --tail 20 tunnel
```

Expect `Registered tunnel connection` (four times).

### 7. Schema check, then data

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml exec backend python -m triagent.cli migrate
```

To load real end-of-day prices, labelled **DELAYED** (unofficial Yahoo feed):

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml exec backend python -m triagent.cli ingest --source yahoo --symbol "NIFTY 50"
```

Or load **SIMULATED** data, plus one risk-approved decision for a paper trade. This replaces the NIFTY 50 dataset:

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml exec backend python -m triagent.cli demo-setup
```

### 8. Use it

Open https://markets.webloom.network and log in with the emailed PIN. Opening https://markets.webloom.network/api/system in the same browser should show JSON with `"mode":"PAPER"` and `"paper_only_lock":true`.

**Availability:** the backend is offline whenever the PC is off, asleep or disconnected. The dashboard then
shows "Backend unreachable". To stop it sleeping: Settings → System → Power → Screen and sleep → "Never" while
you use it. Data persists in the Docker volume `dbdata` on your PC. To back it up:

```powershell
docker compose exec db pg_dump -U triagent triagent > backup.sql
```

## Safety locks in this deployment

* `TRIAGENT_PAPER_ONLY=true` and `LIVE_TRADING_ENABLED=false` are hard-coded in `docker-compose.yml`. LIVE mode
  makes the server refuse to start, and no live broker object is ever constructed (tested).
* No broker credentials are configured. Every order goes to the paper broker after an explicit `CONFIRM TRADE`.

## Teardown (reverse order)

Delete the DNS records `markets` and `markets-api`, the Access apps `TRI-AGENT dashboard` and `TRI-AGENT API`,
the service token `triagent-pages-gateway`, the tunnel `triagent-pc` and the Pages project `triagent-markets`.
Revoke the deployment API token.
