# Broker API comparison (for selection — no broker selected yet)

Only the Zerodha details below were checked against a web source in this build (2026-11-07). The other rows come
from general knowledge and are marked **VERIFY**. Broker pricing and rules change often, so re-check each
broker's developer page before choosing.

| | Zerodha Kite Connect | Upstox API | DhanHQ | Angel One SmartAPI | FYERS API |
|---|---|---|---|---|---|
| Pricing | Personal plan **free** (orders, portfolio, no market data); Connect ₹500/month adds WebSocket and historical candles ([zerodha.com/products/api](https://zerodha.com/products/api/)) | Free tier exists (**VERIFY**) | Trading API free; data API conditions apply (**VERIFY**) | Free (**VERIFY**) | Free (**VERIFY**) |
| Auth | API key + secret → daily request token → access token (expires daily) | OAuth2, daily token | Access token | API key + TOTP | OAuth2 + daily token |
| Market data | WebSocket ticks, historical candles (paid tier) | WebSocket, historical | WebSocket, historical | WebSocket, historical | WebSocket, historical |
| Static IP for orders | **Required since 1 Apr 2025** ([FAQ](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs)) | Expected under the SEBI framework (**VERIFY**) | Same | Same | Same |
| Adapter in this repo | `execution/brokers/zerodha.py`: skeleton, unit-tested with mocks, **never called against the live API** | — | — | — | — |

## Recommendation

For paper trading and research, Kite Connect's ₹500/month data tier is enough. It gives historical and intraday
candles plus streaming, which closes the biggest data gap (intraday and constituent history). A Personal-plan
key is only needed once live execution is authorised. Make the final choice only after asking each broker how
they classify a human-confirmed workflow under the SEBI framework (see `REGULATORY_CHECKLIST.md`).

## Adding a broker

Implement `execution/broker_base.BrokerAdapter` (place, status, find by idempotency key, cancel, modify, open
orders, positions). Map the broker's statuses to `OrderStatus`, including partial fills. Raise `AuthExpired` on
expired sessions. Always wrap the adapter in `LiveGuard`. The engine, risk service and UI stay unchanged.
