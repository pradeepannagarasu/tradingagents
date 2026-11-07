# Regulatory checklist (India) — LIVE execution remains disabled

Compiled **2026-11-07**. Several items below come from secondary summaries that I could not verify against the
primary text. Those are marked **VERIFY**. This is not legal advice. Before any live use, confirm every item with
your broker's compliance team and the official circulars.

## 1. SEBI retail algorithmic-trading framework

| Item | Status / date | Source |
|---|---|---|
| SEBI circular "Safer participation of retail investors in Algorithmic trading" | Issued 4 Feb 2025 (SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013) | sebi.gov.in → Legal → Circulars (**VERIFY** circular number) |
| Original effective date | 1 Aug 2025, extended to 1 Oct 2025 | NSE circular summarised at ricago.com (secondary) |
| Glide path | SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132 (30 Sep 2025): brokers to register API algo products by 31 Oct 2025 and finish registering by 30 Nov 2025; mock session by 3 Jan 2026; brokers missing milestones barred from onboarding new API-algo clients from 5 Jan 2026 | https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf |
| Full applicability | All brokers from **1 Apr 2026** | Secondary sources (CAalley, Business Standard), **VERIFY** |
| Algo ID tagging | Every algo order carries an exchange-issued Algo ID, traceable as client → algo ID → static IP → API key | NSE implementation standards, **VERIFY** current version |
| Static IP whitelisting | Required for API order placement | NSE standards; Zerodha: required for Kite order placement from 1 Apr 2025 (https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs) |
| Order-rate threshold | Above an exchange-set orders-per-second threshold, the strategy must be registered | **VERIFY** current threshold in the NSE circular |
| "White-box" vs "black-box" algos | Black-box (logic not disclosed) algos need a research-analyst registration for the provider | SEBI Feb 2025 circular, **VERIFY** |
| Daily API session logout | Sessions logged out before each trading day | Secondary (Algobulls), **VERIFY** |

**What this means for this platform.** A self-built strategy run through your own broker API is still
algorithmic trading under this framework. Using a retail API does **not** by itself make a strategy compliant.
The current design requires a human CONFIRM TRADE for every order. Whether a human-confirmed, decision-support
workflow counts as an "algo" for your broker is the **broker's determination**. Ask them in writing before
enabling LIVE.

## 2. Pre-live checklist (all must be ✅ before `LIVE_TRADING_ENABLED=true`)

- [ ] Broker confirms in writing how this workflow is classified (manual or algo) and what registration is needed
- [ ] Algo/strategy registered with the exchange through the broker, if required; Algo ID available to tag orders
- [ ] Static IP configured with the broker (a VPS gives you one) and API key bound to it
- [ ] Order rate stays below the exchange threshold (the platform submits one human-confirmed order at a time)
- [ ] Two-factor login and daily token refresh procedure documented (Kite access tokens expire daily)
- [ ] Data licences: real-time data display and redistribution rights confirmed (no redistribution of NSE data)
- [ ] Paper-trading track record reviewed (Phase 7), including risk-engine rejection logs
- [ ] Transaction-cost table in `config/costs_in_nse.toml` verified against current Finance Act / NSE circulars
      (2026-04-01 F&O STT increase: 0.05% futures, 0.15% options. Secondary sources disagree on the effective date. **VERIFY**)
- [ ] NIFTY lot size and weekly-expiry weekday verified (config: lot 65 from 30 Dec 2025; Tuesday expiry from Sep 2025, **VERIFY**)
- [ ] Tax treatment (speculative/non-speculative business income for F&O and intraday) reviewed with a CA
- [ ] Emergency stop drill performed (STOP ALL TRADING → cancel → verify at broker)

## 3. Product controls already enforced in code

* LIVE needs three independent switches: the config flag, the `LIVE_TRADING_ENABLED` env flag and the dashboard's "live disabled" flag (default disabled).
* Human approval of the exact ticket, hash-bound, by typing `CONFIRM TRADE`.
* A risk token signed with HMAC and expiring after 15 minutes, bound to decision, instrument, direction, quantity and stop.
* No LIVE orders on simulated data, without a live quote, or on stale decisions (more than 4 days old).
* Hash-chained, append-only audit trail of every ticket, confirmation, broker update, rejection and emergency action.
* Automatic (unconfirmed) execution is **not implemented**, and should stay that way until §2 is complete and verified.
