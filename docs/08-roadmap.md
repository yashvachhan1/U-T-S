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
| 4 | `global_markets` | US futures, Asian markets, NSE ETF proxies | ✅ **ho gaya** |
| 5 | `news` | NSE filings, SEBI, PIB + RSS | ✅ **ho gaya** |
| 6 | `altdata` | Vidyut Pravah ✅, VAHAN ❌ | 🟡 **aadha** |
| 7 | `uncertainty` | GPR, EPU (monthly) | ⬜ |

### `altdata` — 🟡 aadha bana

| Cheez | Status |
|---|---|
| **Vidyut Pravah** — live all-India power demand (GW) | ✅ chal raha hai |
| Day-over-day change + activity classification | ✅ |
| VAHAN daily registrations | ❌ browser automation chahiye |

**Vidyut Pravah kaise solve hua:** site HTML me khaali placeholders (`-`)
rakhti hai aur AJAX se bharti hai, to scraping se dash milte hain. Asli data
page ke apne JavaScript me mile do POST endpoints se aata hai —
`BindTopStatisticsFromJS` aur `BindCurrentDateTimeForJson`. Dono ko **body
chahiye**, warna 411 Length Required. Values HTML spans me wrapped aati hain.

> **Kyun valuable hai:** bijli ki demand industrial activity batati hai, aur
> ye **abhi** milti hai. Usi period ka IIP ~6 hafte baad aata hai — tab tak
> market move kar chuka hota hai.

**VAHAN kyun nahi bana (honest reason):** uska dashboard PrimeFaces app hai jo
data ~15 chained JSF AJAX POSTs se load karta hai. Har request par ViewState
badalta hai, aur component IDs (`j_idt666` jaise) generated hain jo deployment
ke saath badal jaate hain. HTTP client se replicate karna possible hai par
**silently aur aksar toot jayega**. Iske liye asli browser automation chahiye
— wo baaki collectors se alag kism ki dependency hai. Isliye guessed code
ship karne ke bajaye ye gap document kiya hai.

### `news` — ✅ kya bana

| Cheez | Kahan |
|---|---|
| NSE corporate announcements (JSON, **symbol ke saath**) | `collectors/news.py` |
| SEBI + PIB + 3 RSS feeds | `collectors/news.py` |
| RSS 2.0 **aur** Atom dono parse hote hain | `collectors/news.py` |
| Material filing detection (board meeting, dividend, merger...) | `collectors/news.py` |
| Content-hash dedup — ek story paanch baar nahi ginegi | `store/events.py` |
| Untrusted text: control chars strip, length cap | `store/events.py` |
| **Blackout symbols** — jin par fresh filing hai | `collectors/news.py` |
| API: `/news/recent`, `/news/blackout`, `/news/symbol/{sym}` | `routers/api.py` |

**Source status** (16 Oct 2026 par probe kiya):

| Source | Status |
|---|---|
| NSE announcements | ✅ JSON, symbol ke saath — sabse valuable |
| **RBI** — press, notifications, **speeches**, publications | ✅ **4 RSS feeds** |
| SEBI, PIB | ✅ RSS |
| Moneycontrol, ET, Business Standard | ✅ RSS |
| BSE announcements | 🟡 best-effort — `Referer` chahiye, phir bhi rate-limit karta hai |

**RBI kaise solve hua:** unke documented URLs (`Scripts/Rss.aspx?Id=N`) HTML
dete hain. Asli feeds `rbi.org.in/<name>_rss.xml` par hain —
`pressreleases_rss.xml`, `notifications_rss.xml`, `speeches_rss.xml`,
`Publication_rss.xml`. Speeches isliye liye kyunki research me central-bank
tone ka measurable asar milta hai, aur **India ke liye significant**.

**BSE kaise aadha solve hua:** bina `Referer: bseindia.com` ke **turant 403**
aata hai — wo header hi asli fix hai. Uske saath 200 milta hai, par BSE phir
bhi aggressively rate-limit karta hai. Isliye best-effort rakha: fail ho to
log karke aage badh jaata hai. NSE waise bhi zyadatar filings cover karta hai;
BSE sirf BSE-only listings add karta.

> **News entry signal nahi hai.** Headline padhne tak move ho chuka hota hai.
> Iska kaam **blackout aur exit** hai: "is stock par fresh news hai, naya
> position mat lo." Isliye blackout deliberately conservative hai — har filing
> ginti hai, sirf material nahi.

### `global_markets` — ✅ kya bana

| Cheez | Kahan |
|---|---|
| World market sessions IST me, **US + Australia DST** calculate hota hai | `markets.py` |
| `overlaps_india()` — kaunsa market live signal hai, kaunsa sirf gap | `markets.py` |
| Yahoo Finance se 10 markets (US futures, Asia, India) | `collectors/global_markets.py` |
| Har quote par `live` ya `last_close` tag | `collectors/global_markets.py` |
| Asia breadth read — ek market nahi, **paanch** milkar signal | `collectors/global_markets.py` |
| US futures gap bias | `collectors/global_markets.py` |
| NSE ETF proxies (MASPTOP50, MON100, HNGSNGBEES...) feed se | `collectors/global_markets.py` |
| API: `/markets/sessions`, `/markets/global` | `routers/api.py` |

> **Ek bug pakda:** Yahoo ka `chartPreviousClose` *range se pehle* ka close
> hai, to `range=5d` par wo 6-din ka move deta hai. Usse "daily change" maan
> lene se KOSPI ka +2.05% session −14.46% dikh raha tha, aur Asia read
> "broadly positive" ki jagah "risk_off" bol raha tha. Ab change close-series
> se aata hai, aur multi-day move alag field me.

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

## Phase 3 — Feature store · ✅ ho gaya

| Cheez | Kahan |
|---|---|
| O(1) streaming primitives (EMA, SMA, RSI, ATR, z-score, rolling extreme) | `features/incremental.py` |
| **OFI** (Cont-Kukanov-Stoikov), book imbalance, weighted mid | `features/microstructure.py` |
| VWAP, trade sign (Lee-Ready), spread tracker, opening range | `features/microstructure.py` |
| Feature registry — version, horizon, evidence, budget | `features/registry.py` |
| Per-instrument state, session reset, stale rejection | `features/engine.py` |
| API: `/features/catalogue`, `/features/status`, `/features/{key}` | `routers/api.py` |

**23 features** registered — 7 tick, 7 intraday, 4 daily, 5 slow.

### Teen gates — teeno pass

| Gate | Target | Mila |
|---|---|---|
| **Incremental == batch** | Exact match | ✅ EMA, SMA, RSI, ATR — sab `engine/indicators.py` se match |
| **Point-in-time** | Future data leak nahi | ✅ Purana timestamp **reject** hota hai, blend nahi |
| **Speed** | 40,000 updates/sec | ✅ **177,000/sec** (5.6 µs per update) |

> **Incremental == batch kyun sabse important hai:** agar streaming primitive
> aur batch function alag jawab dein, to backtest aur live bot **alag
> strategy** chala rahe hain — aur kuch toota hua nahi dikhega.

> **Unwarmed feature `None` hota hai, zero nahi.** Aadha bhara 252-din ka
> momentum window chhota number nahi hai, **galat** number hai.

**Output:** backtest jhooth nahi bolega.

---

## Phase 4 — ML pipeline · ✅ ho gaya

| Cheez | Kahan |
|---|---|
| **Cost-aware labels** — move cost se bada ho tabhi signal | `ml/labels.py` |
| Triple-barrier labels (tie me stop jeetta hai) | `ml/labels.py` |
| Overlapping labels ka sample weighting | `ml/labels.py` |
| **Purged walk-forward + embargo** | `ml/validation.py` |
| Leak detector (`check_no_leakage`) | `ml/validation.py` |
| LightGBM + **isotonic calibration** | `ml/model.py` |
| Feature importance stability across folds | `ml/model.py` |
| 6-check gate + noise check | `ml/evaluate.py` |

### Pipeline validate kiya — yahi asli proof hai

Same synthetic data, same volatility, sirf signal ka farak:

| | AUC | Edge over baseline | Gate |
|---|---|---|---|
| **Real signal** | 0.744 | +0.183 | ✅ **PASS** |
| **Pure noise** | 0.502 | −0.011 | ❌ **FAIL** |

> Pipeline asli signal dhoondh leta hai, **aur noise me kuch nahi dhoondhta**.
> Agar noise par bhi edge dikhta, to matlab validation leak kar rahi hai aur
> baaki saare numbers fiction hote.

### Teen leaks jo handle kiye

| Leak | Fix |
|---|---|
| Random split (future se seekhna) | Walk-forward — hamesha aage |
| **Purging** — training row ka outcome test window me pada ho | Aise rows training se drop |
| **Embargo** — rolling-window features test ke saath share karte hain | Test se pehle gap |

### Gate ke 6 checks

`beats_baseline` · `auc_above_chance` · `consistent_across_folds` ·
`profitable_after_costs` · **`beats_buy_and_hold`** · `enough_trades`

Ek bhi fail = model use nahi hoga.

🔴 **Gate wahi hai:** buy & hold ko nahi harata to aage mat badho.

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
