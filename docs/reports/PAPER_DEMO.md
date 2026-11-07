# Paper-trading demonstration

Output of `python -m triagent.cli paper-demo` on 2026-11-07 (SIMULATED data, PostgreSQL 16 backend):

```
Decision 684d37c1b333440ea4f0 (seed 0, data as of 2026-11-06): BUY / LONG NIFTY-FUT qty 65, max loss ₹6,365
Ticket 09133251a7f345f8 PENDING_APPROVAL
After CONFIRM TRADE: FILLED PAPER-000001
Paper positions: [{'symbol': 'NIFTY-FUT', 'qty': 65, 'avg_price': 4687.13}]
Audit chain valid: (True, 5)
```

What happened:

1. The demo loaded a SIMULATED NIFTY 50 series dated up to the last business day, so paper trading uses
   *current* data. Historical decisions are refused as orders because their data is stale.
2. BULL, BEAR, the debate and JUDGE ran, then the risk engine. The risk engine sized one NIFTY futures lot
   (65 units) with a maximum loss at the stop of ₹6,365, below the 1% (₹10,000) budget, and issued a signed
   approval token.
3. An order ticket was created (PENDING_APPROVAL). It was submitted only after the exact phrase
   `CONFIRM TRADE` was given together with the hash of the ticket that was displayed.
4. The paper broker filled it (`PAPER-000001`), and the position appears in the paper book.
5. The audit chain (data ingest → decision → ticket → confirm → broker update) verified intact.

Seen across the historical scans: with ₹10 lakh capital and 1% risk, **many NIFTY-futures signals are rejected**,
because one 65-unit lot × a 2-ATR stop exceeds the risk budget. This is the risk engine working as designed.
To trade smaller, use an ETF proxy or a cash equity, or deliberately change capital/risk limits in `config/`.
