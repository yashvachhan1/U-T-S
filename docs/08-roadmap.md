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

## Phase 5 — Agent layer · 🟡 12 analysts + synthesis pair ✅, review pair baaki

| Step | Status |
|---|---|
| 1 | ✅ `agents/base.py` — prompt fencing, schema validation, graceful degrade |
| 2 | ✅ `agents/llm.py` — Groq via httpx, key pool, model routing, stub client |
| 3 | ✅ **Saare 12 analyst agents** (13 instances — index agent do baar chalta hai) |
| 4 | ✅ **Chief Strategist + Red Team** |
| 5 | ✅ `store/agent_outputs.py` — backtest replay ke liye |
| 6 | ✅ Per-agent scorecard (`/agents/status`) |

> Gate ke hisaab se **ek agent poora** banaya, baarah aadhe-adhoore nahi.
> Jo yahan kaam kar gaya wo baaki sab reuse karenge.

### Teen rules — ab enforce ho chuke hain

| Rule | Kaise |
|---|---|
| **Agent order place nahi karta** | Sirf structured fields deta hai |
| **Agent risk GHATA sakta hai, BADHA nahi** | `size_multiplier()` 1.0 pe capped — **100 bullish agents bhi 1.0 hi dete hain** |
| **Har output store hota hai** | Backtest `as_of()` se replay karta hai, LLM call nahi |

### Prompt injection — defence structural hai, prompt-based nahi

Untrusted text fence ke andar jaata hai aur khud fence band nahi kar sakta.
Par asli guarantee **wo nahi** hai — asli guarantee ye hai ki chahe model
`confidence: 0.99, risk_flag: false` return kar de, **size multiplier 1.0 se
upar jaa hi nahi sakta**.

> Successful injection ka worst case: **trade nahi hua**. Paisa nahi gaya.

Schema bhi sakht hai: unknown fields **reject**, unknown sectors **drop**,
aur non-ticker text (`"BUY NOW!!! <script>"`) ko sanitise karke ticker jaisa
banane ke bajaye **poora drop** kiya jaata hai — kyunki sanitise karne par wo
`BUYNOWSCRIPT` ban jaata hai, jo dikhta ticker jaisa hai par hai nahi.

### Agent fail ho to kya?

Neutral view, system chalta rehta hai. **Koi feed timeout trading nahi rokta**
— bas wo ek opinion chala jaata hai aur system thoda cautious ho jaata hai.

Aur 30 min se purana output **current nahi maana jaata** — do ghante purani
raay aaj ki raay nahi hoti.

### Chautha rule — model se number mat maango

Analysts banate waqt ek rule aur add hua:

> **Model se judgement maango, number nahi.** Jo number hamare paas pehle se
> hai wo Python compute karta hai aur prompt me chala jaata hai.

Kyun: 50 constituents me advancers ginna arithmetic hai. Model galat gine to
**chup-chaap** galat ginega aur healthy dikhta rahega. Isliye breadth,
industry median, advance ratio — sab Python me. Model sirf batata hai ki
iska **matlab** kya hai.

Isi wajah se Nifty 500 ka industry performance **median** se nikalta hai,
mean se nahi — ek stock limit-up ho jaye to mean puri industry ko leader bana
dega, aur agent stock-story ko industry-move samajh lega.

### Schema me do safety rules hard-coded hain

| Agent | Rule | Kyun |
|---|---|---|
| Crypto Risk | **bullish view → neutral** | BTC-equity correlation calm market me ~0, crash me 1.0 ki taraf. Signal sirf downside me hai. Prompt pe bharosa karna matlab us din bharosa karna jab number dramatic ho. |
| Power & Policy | `historical_n < 6` → confidence 0.35 pe cap | 2 precedent se nikala confident base rate, noise se nikala confident base rate hai. |

### Agent 11 & 12 ke liye naya reference data

`collectors/constituents.py` — NSE ki apni constituent CSVs
(`ind_nifty50list.csv`, `ind_niftybanklist.csv`, `ind_nifty500list.csv`).

Teen cheezein ismein se free milti hain:

| Kya | Kaise |
|---|---|
| Industry mapping | CSV me **Industry column** hai — exchange ka apna, hamein banana nahi pada |
| Live subscription | CSV me **ISIN** hai, aur Upstox equity key `NSE_EQ|<isin>` hai — naam se search karne ki zarurat nahi |
| Breadth | Weightage publish nahi hota, par breadth ko weight chahiye hi nahi |

Verified: Nifty 50 → 50 rows, Bank Nifty → 14, Nifty 500 → 501, 20 industries.

### Expired contract wala bug

Agents wire karte waqt mila: `instruments.search("USDINR")` **expire ho chuka
contract** pehle return kar raha tha, kyunki sort key me expiry ascending thi
aur purana expiry hamesha chhota hota hai.

Nateeja kya hota: currency agent ek aise key ko subscribe karta jo kabhi quote
nahi karti — **chup-chaap andha**, aur status healthy. Fix: expired contracts
ab last me sort hote hain (hataye nahi gaye — historical candles purani key se
hi aati hain).

### Subscription gap ab dikhta hai

`GET /api/agents/instruments` batata hai ki agents ko **532 instruments**
chahiye aur abhi kitne mil rahe hain. Ye sabse chup failure mode tha:
unsubscribed agent har line par "not subscribed" padhta hai, neutral return
karta hai, aur hamesha healthy dikhta hai.

### Synthesis pair — stance banao, fir us par hamla karo

Order fix hai aur yahi iska pura point hai: Chief Strategist analysts se
**stance** banata hai, fir Red Team **usi stance** par hamla karta hai.
Challenge karne wala kabhi likhne wala nahi ho sakta — LLM se apni hi
reasoning review karwao to wo haan hi bolega.

| Agent | Model | Kaam | Failure par |
|---|---|---|---|
| Chief Strategist | strong | 12 analysts → stance (regime, risk budget, sector allow/block, exposure cap) | `insufficient_data` |
| Red Team | strong | Stance ke **khilaf** argue karna | **0.5x size** — 1.0x nahi |

#### Agreement mehnga answer hai

Red Team ka pura design is ek baat par tika hai: LLM naturally agreeable hai,
aur **sabse zyada** tab jab plan accha likha ho — jo Chief Strategist ka
output hamesha hota hai. Isliye haan bolna structurally mushkil banaya gaya:

| | |
|---|---|
| `agreement` default **False** | Approval bolna padta hai, maan nahi liya jaata |
| Objections + agreement ek saath nahi | Schema contradiction ko objections ki taraf resolve karta hai |
| Red Team chala hi nahi → **0.5** | Full size ka matlab hai challenge jhela. Jo challenge hua hi nahi, wo jhela nahi gaya |
| `suggested_size_multiplier` 0.0–1.0 clamp | Red Team risk ghata sakta hai, badha nahi |

#### Ek agent ke andar caution **ceiling** hai, product nahi

Pehla version 0.05 multiplier de raha tha. Wajah: `risk_off` stance,
`bearish` view aur `risk_flag` — **ek hi judgement teen tarike se**, aur
teeno multiply ho rahe the. 0.125 nikalta hai jo precision jaisa dikhta hai
par hai nahi.

> **Ek agent ke andar:** sabse tight wala jeetta hai (`min`).
> **Alag agents ke beech:** multiply hota hai — wo sach me alag raayein hain.

#### Final multiplier ka hisaab

| Haalat | Multiplier |
|---|---|
| Calm stance + Red Team agree | 1.0 |
| Stance + objection | 0.5 pe **cap** (dobara punish nahi) |
| Red Team chala nahi | 0.5 |
| Stance nahi bana | 0.5 |
| Dono fail | 0.25 |

Disagreement **ceiling** hai, ek aur factor nahi — Red Team ne pehle hi 0.3
maanga hai to 0.15 nahi hoga, kyunki 0.3 maanga hi objection ki wajah se tha.

#### Do defences jo prompt pe nahi tike

1. **3 se kam analysts → model call hi nahi hota.** Synthesis ka matlab hai
   disagreement tolna; do raay me tolne ko kuch hai hi nahi.
2. **`analysts_used` model se nahi liya jaata** — store se jo mila wahi
   likha jaata hai. Warna minimum-analysts rule ko wahi number enforce karta
   jo model khud bhejta hai, matlab rule hai hi nahi.

#### Raw text yahan tak aata hi nahi

Synthesis ke saare input **pehle hi schema se guzar chuke** hain. Ek hostile
filing yahan pahunchte-pahunchte ek bounded enum aur ek float ban chuki hoti
hai. Ye doosri deewar hai, aur yahi tab bhi khadi rehti hai jab pehli toot jaye.

### Agents ab live feed se jude hain

Pehle agents instruments **declare** karte the aur koi unhe subscribe nahi
karta tha. Natija: har agent har line par "not subscribed" padhta, neutral
return karta, aur **healthy dikhta rehta**. Trading system ka sabse chup
failure mode yahi hai.

`engine/agent_feed.py` ab 532 instruments subscribe karta hai:

| Mode | Kitne | Kya | Kyun |
|---|---|---|---|
| `full` | 31 | Indices, sector indices, India VIX, factor ETFs, crude/gold/silver futures, USDINR, US/Asia ETF proxies | Depth aur volume chahiye |
| `ltpc` | 501 | Nifty 500 constituents | Breadth ko sirf **price + previous close** chahiye |

500 stocks par full depth maangna matlab poora feed budget un fields par
kharch karna jo koi padhta hi nahi. `ltpc` frame me `ltp` aur `cp` dono
aate hain — `change_pct` nikal aata hai, aur advance/decline ko bas yahi
chahiye. (Test isi baat ko end-to-end verify karta hai.)

#### Budget khatam ho to kya

Mixed-mode subscription ka cap **1500** keys hai. 200 reserve rakhe gaye
hain — user ka chart, order path ka pricing. Agar universe kabhi 1300 se
upar jaye to **girne ka order fix hai**:

```
1. 31 headline instruments   (30 keys, 11 agents ko serve karte hain)
2. Bank Nifty ke 15          (poora agent chal jaata hai)
3. Nifty 50 ke 50
4. Nifty 500 ka tail         ← yahi katega
```

Exception nahi aati, count report hota hai. Aadha feed kaam ka hai; startup
par crash nahi.

#### Ek key, ek mode

Nifty 50 ko index agent `ltpc` me maangta hai aur sector rotation `full` me.
Feed ek key ka ek hi mode rakh sakta hai, isliye **richer mode jeetta hai** —
warna sector rotation ka volume chup-chaap chala jaata.

#### Kab subscribe hota hai

| Kab | Kya |
|---|---|
| App startup | Plan register ho jaata hai, socket se pehle — feed connect par **replay** kar deta hai |
| `POST /api/feed/start` | Constituent lists refresh (7 din se purani ho to) + subscribe |
| `GET /api/agents/instruments` | Kitne chahiye vs kitne subscribed vs kitne **tick kar rahe hain** |

Teesri column alag hai aur yahi dekhne layak hai: subscribe ho jaana aur
price aana do alag baatein hain, aur open ke baad kaafi der tak alag rehti hain.

#### Ek flaky test theek kiya

`test_updates_are_fast_enough_for_the_feed` kabhi-kabhi fail ho raha tha —
33,000/sec, jabki akele chalane par 177,000/sec. Wo code ki speed nahi,
machine ka sabse bura moment naap raha tha. Ab 5 chhote rounds me se
**best** liya jaata hai: jo code sach me slow hai wo har round me fail
karega, par CPU kisi aur ne le liya to build nahi girega.

**Output:** 12 analysts + synthesis pair live feed se jude hue, 424 test
pass. Review pair (Post-Trade Review + Risk Auditor) Phase 7 ke saath
aayega — unhe chalane ke liye actual trades chahiye.

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
