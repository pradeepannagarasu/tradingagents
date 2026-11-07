# Operating cost estimate (INR / month, 2026-11)

Prices change. These figures are estimates for budgeting, not quotes. Anything not checked against a live
pricing page in this build is marked "approx.".

| Item | Minimal (research + paper) | Recommended (paper with real data) | Live-ready |
|---|---|---|---|
| Market data | ₹0 (manual NSE CSV downloads; Yahoo for personal use) | ₹500 Kite Connect data tier (verified on zerodha.com) | ₹500 + licensed vendor for PIT constituents/F&O history (quote-based; typically ₹ thousands–tens of thousands) |
| Broker execution API | ₹0 | ₹0 | ₹0 (Kite Personal plan, verified) + brokerage per trade |
| Server | ₹0 (own PC) | ₹800–2,500 small VPS (approx.) | Same VPS; a static IP is required for API orders |
| Database | ₹0 (SQLite / local Postgres) | ₹0 (Postgres in Docker on the VPS) | ₹0–2,000 managed Postgres (approx.) |
| LLM narratives (optional) | ₹0 (`provider = none`) | ~3 calls × ~4k tokens per decision. At 20 decisions/day that's ~2.5M tokens/month; check the provider's current price per million tokens | Same |
| Notifications | ₹0 (Telegram bot, SMTP) | ₹0 | ₹0 |
| **Total** | **₹0** | **≈ ₹1,300–3,000 + LLM** | **≈ ₹2,000–5,000 + data licence + LLM** |

Trading costs (brokerage, STT, exchange, SEBI, stamp, GST, DP, slippage) are modelled per trade from
`config/costs_in_nse.toml` and appear in every backtest, risk assessment and ticket.
