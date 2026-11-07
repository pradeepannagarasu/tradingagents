# Windows setup

## Option A — native (PowerShell)

1. Install **Python 3.11+** (python.org, tick "Add to PATH") and **Node.js 20+** (nodejs.org). Install Git for Windows.
2. Clone the repo, then:

```powershell
cd tradingagents\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q
```

3. Start the API. The demo uses a SIMULATED series until you load real data:

```powershell
$env:TRIAGENT_DEMO="true"; python -m uvicorn triagent.api.main:app --port 8000
```

4. In a second terminal:

```powershell
cd tradingagents\frontend
npm install
npm run dev
```

Then open http://localhost:5173.

5. Load real data. Either save the NIFTY 50 history CSV you downloaded from niftyindices.com to
`backend\data\import\nifty50.csv` and click **Import CSV** on the Data page, or run:

```powershell
python -m triagent.cli ingest --source yahoo --symbol "NIFTY 50"
```

If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Option B — Docker Desktop (WSL2 backend)

```powershell
copy .env.example .env    # set POSTGRES_PASSWORD and RISK_SIGNING_KEY
docker compose up --build
```

The dashboard is then at http://localhost:8080 and the API docs at http://localhost:8000/docs.

## Keeping the machine awake for paper trading

Use Power settings → "Never sleep" while the market is open, or run on a small Linux VPS instead. A VPS also
gives you the static IP that live order placement requires.
