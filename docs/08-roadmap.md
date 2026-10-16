# 08 — Roadmap

---

## Phase 0 ✅ — ho chuka hai

- Upstox OAuth + REST client
- Instrument master (equity, index, F&O, **currency, commodity**) + search
- Live charts, watchlist, manual orders (paper + live)
- Backtester (event-driven, costs + slippage ke saath)
- 5 built-in rule strategies
- Rule-based bot runner with safety rails
- React dashboard (light + dark)
- IST clock fix — machine ke timezone par bharosa nahi

---

## Phase 1 — Compliance & plumbing · 1–2 din

🔴 **Ye pehle karna hai, warna live trading kaam hi nahi karegi.**

### Code — ✅ ho gaya

| # | Task | Kahan |
|---|---|---|
| 1 | ✅ **MARKET → LIMIT** (marketable limit, 0.3% band) | `upstox/orders.py` |
| 2 | ✅ Tick-size rounding (BUY up, SELL down) | `upstox/orders.py` |
| 3 | ✅ Order rate limiter — peak 8/sec, SEBI limit 10 | `ratelimit.py` |
| 4 | ✅ Daily order cap (200) | `ratelimit.py` |
| 5 | ✅ Algo tagging (`X-Algo-Name`) | `upstox/orders.py` |
| 6 | ✅ Idempotency keys + duplicate detection | `upstox/orders.py` |
| 7 | ✅ Fat-finger guard (max order value) | `upstox/orders.py` |
| 8 | ✅ Price deviation guard (stale data) | `upstox/orders.py` |
| 9 | ✅ Live mode needs a reference price | `upstox/orders.py` |
| 10 | ✅ Config absolute ceilings + startup validation | `config.py`, `main.py` |

25 tests: `.venv\Scripts\python.exe -m pytest tests -q`

### Deployment — ⬜ aapko karna hai

| # | Task | Kyun |
|---|---|---|
| 11 | Static IP / VPS setup | API access ke liye zaroori |
| 12 | SSH tunnel access (public bind nahi) | [05-security.md](05-security.md) |

**Output:** live mode me order sach me lag sakta hai, aur surakshit hai.

---

## Phase 2 — Data collectors · 1–2 hafte

> **Ye sabse jaldi shuru hona chahiye** — data jama hone me time lagta hai, aur
> ML Phase 4 tak data ke bina nahi ho sakti.

| # | Collector | Source | Status |
|---|---|---|---|
| 1 | `feed` | **Upstox WebSocket** | ✅ **ho gaya** |
| 2 | `crypto` | Binance — 24×7 | ✅ **ho gaya** |
| 3 | `fred` | FRED API — crude, DXY, US10Y, India EPU | ✅ **ho gaya** |
| 4 | `global_markets` | US futures, Asian markets, NSE ETF proxies | ⬜ |
| 5 | `news` | NSE/BSE filings, RBI, SEBI, PIB + RSS | ⬜ |
| 6 | `altdata` | Vidyut Pravah, VAHAN | ⬜ |
| 7 | `uncertainty` | GPR, EPU (monthly) | ⬜ |

### `crypto` + `fred` — ✅ kya bana

| Cheez | Kahan |
|---|---|
| Collector base: retry, backoff, rate limit, run tracking | `collectors/base.py` |
| BTC/ETH 24×7, **asymmetric** risk signal (sirf downside) | `collectors/crypto.py` |
| FRED — 8 series ek key se (crude, DXY, US10Y, VIX, India EPU) | `collectors/fred.py` |
| Missing observations (`.`) drop hote hain, zero nahi bante | `collectors/fred.py` |
| **`fetched_at`** har row par — point-in-time backtest ke liye | `store/macro.py` |
| `as_of()` — us din jo pata tha wahi dikhta hai | `store/macro.py` |
| Crude × rupee composite stress index | `collectors/fred.py` |
| API: status, start/stop, run-once, macro snapshot | `routers/api.py` |

> FRED ke liye free key chahiye: https://fredaccount.stlouisfed.org/apikeys
> `.env` me `FRED_API_KEY` daalein. Key na ho to collector skip hota hai,
> baaki sab chalta rehta hai.

### `feed` — ✅ kya bana

| Cheez | Kahan |
|---|---|
| V3 WebSocket client, auto-reconnect with jittered backoff | `upstox/feed.py` |
| Subscription state + per-mode key limits (5000/3000/2000/50) | `upstox/feed.py` |
| Resubscribe on reconnect (server bhool jaata hai) | `upstox/feed.py` |
| Official protobuf schema, compiled | `upstox/proto/` |
| Tick decoder — 4 union shapes → ek flat Tick | `upstox/ticks.py` |
| Derived: spread, mid, book imbalance, change % | `upstox/ticks.py` |
| Parquet writer, background flush, bounded buffer | `store/ticks.py` |
| Latest-price cache (order path isi se padhta hai) | `engine/feed_service.py` |
| API: status, start, stop, subscribe, snapshot | `routers/api.py` |

Saath me: `store/` layer — **PostgreSQL** transactional data ke liye
(SQLite se migrate), **Parquet** ticks ke liye. Detail:
[11-database.md](11-database.md)

**Output:** har din data jama ho raha hai, chahe aage kuch bhi ban raha ho.

---

## Phase 3 — Feature store · 1 hafta

- Feature registry + versioning
- **Point-in-time correctness** — backtest me sirf wahi data dikhe jo us waqt tha
- Incremental O(1) feature updates
- Microstructure features (OFI, book imbalance) — WebSocket ke baad

**Output:** backtest jhooth nahi bolega.

---

## Phase 4 — ML models · 2–3 hafte

- LightGBM baseline
- **Walk-forward validation** — random split kabhi nahi
- Probability calibration
- Benchmark: buy & hold **aur** maujooda rule strategies
- Feature importance + stability check

🔴 **Gate:** agar model buy & hold ko nahi harata, aage mat badho. Strategy
badlo ya features badlo.

---

## Phase 5 — Agent layer · 2 hafte

| Step | Kya |
|---|---|
| 1 | `agents/base.py` — prompt, schema validation, retry, cache |
| 2 | `agents/llm.py` — Groq client, key pool, model routing |
| 3 | 10 analyst agents |
| 4 | Chief Strategist + Red Team |
| 5 | Agent output storage (backtest replay ke liye) |
| 6 | Per-agent scorecard |

**Output:** har agent ka output save ho raha hai aur measure ho raha hai.

---

## Phase 6 — Decision + Risk engine · 1–2 hafte

- Decision engine: ML + agents + rules → trade proposal
- Risk engine: saare limits, kill switches
- Execution: LIMIT orders, idempotency, retry
- Reconciliation loop
- Audit log

🔴 **Gate:** kill switches test kiye bina aage nahi.

---

## Phase 7 — Paper forward-test · 2–3 mahine

> **Ye skip karne ka mann karega. Mat karna.**

| Kya check karna hai | Pass criteria |
|---|---|
| Paper results vs backtest | Bahut alag? → strategy live me kaam nahi karegi |
| Har agent ka value-add | Jo add nahi karta, nikal do |
| Drawdown | Backtest se zyada to nahi? |
| Execution | Order fill ho rahe hain, reject nahi? |
| Uptime | System roz chal raha hai bina crash ke? |

**Output:** aapko pata hai ki system live me kaisa behave karega.

---

## Phase 8 — Live, dheere · ongoing

```
1. Sabse chhoti capital se shuru
2. Sirf SWING strategy (sabse aasaan, latency ki zarurat nahi)
3. 1 mahina chalao, review karo
4. Theek chale to BTST add karo
5. Phir intraday (WebSocket + VPS ready hone par)
6. Options sabse aakhir me — spreads only
```

---

## Build order — aasaan se mushkil

| Order | Strategy | Latency chahiye | Kyun pehle |
|---|---|---|---|
| 1 | **Swing** | Koi nahi | Kam data, kam cost, sabse aasaan |
| 2 | **BTST** | Koi nahi | Cross-asset data yahan lagega |
| 3 | **Intraday** | 100 ms | WebSocket + VPS chahiye |
| 4 | **Options** | varies | Greeks module + spreads |

---

## Timeline

| Phase | Time | Cumulative |
|---|---|---|
| 1 — Compliance | 2 din | 2 din |
| 2 — Collectors | 2 hafte | ~2.5 hafte |
| 3 — Features | 1 hafta | ~3.5 hafte |
| 4 — ML | 3 hafte | ~6.5 hafte |
| 5 — Agents | 2 hafte | ~8.5 hafte |
| 6 — Decision+Risk | 2 hafte | ~10.5 hafte |
| 7 — **Paper test** | **2–3 mahine** | **~5–6 mahine** |
| 8 — Live | — | — |

**Live trading tak ~5–6 mahine.** Jaldi karne ki koshish ka matlab hai Phase 7
kaatna — aur wahi sabse mehngi galti hogi.

---

## Abhi next

**Phase 1** (compliance — 2 din) aur **Phase 2** (collectors) saath me shuru ho
sakte hain. Phase 2 jitna jaldi shuru ho, utna data jama hoga.

Sabse pehla concrete step: `tools/check_market_data.py` chalao (login karke) —
usse pata chalega ki commodity/currency data market band hone ke baad bhi aata
hai ya nahi. Us jawab se collector ka design tay hoga.
