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

| # | Task | Kyun |
|---|---|---|
| 1 | **MARKET → LIMIT orders** | 1 April 2026 se Upstox API par MARKET band hai |
| 2 | Market Price Protection handle karo | Default on hai ab |
| 3 | Order rate limiter (<10/sec) | SEBI requirement |
| 4 | Algo tagging (`X-Algo-Name`) | Exchange mandate |
| 5 | Idempotency keys | Duplicate order se bacho |
| 6 | Static IP / VPS setup | API access ke liye zaroori |
| 7 | SSH tunnel access (public bind nahi) | [05-security.md](05-security.md) |

**Output:** live mode me order sach me lag sakta hai, aur surakshit hai.

---

## Phase 2 — Data collectors · 1–2 hafte

> **Ye sabse jaldi shuru hona chahiye** — data jama hone me time lagta hai, aur
> ML Phase 4 tak data ke bina nahi ho sakti.

| # | Collector | Source |
|---|---|---|
| 1 | `feed` | **Upstox WebSocket** (polling replace karega) |
| 2 | `crypto` | Binance — 24×7 |
| 3 | `fred` | FRED API — crude, DXY, US10Y, India EPU |
| 4 | `global_markets` | US futures, Asian markets, NSE ETF proxies |
| 5 | `news` | NSE/BSE filings, RBI, SEBI, PIB + RSS |
| 6 | `altdata` | Vidyut Pravah, VAHAN |
| 7 | `uncertainty` | GPR, EPU (monthly) |

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
