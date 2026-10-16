# 01 — Research: Market kin factors par chalta hai

Evidence rating: ★★★ strong (academic + replicated) · ★★☆ medium · ★☆☆ weak / mostly folklore

> **Sabse zaroori baat pehle:** zyada factor = behtar nahi. 100 features aur
> 2 saal data = guaranteed overfitting. Rule of thumb: har feature ke liye
> **1000+ independent observations** chahiye. Daily strategy me 2 saal = ~500
> din = **5–10 features max**. 5-minute intraday me ~25,000 bars = 20–30
> features theek hain.

---

## A. Stock-picking factors (horizon: hafte–mahine)

| Factor | Kya hai | Evidence | Data |
|---|---|---|---|
| **Momentum** | 12-1 month winners aage bhi chalte hain | ★★★ | ✅ |
| **Quality** | High ROE, low debt, stable earnings | ★★★ | 🟡 |
| **Value** | Sasta P/B, P/E, EV/EBITDA | ★★☆ lambe drawdown | 🟡 |
| **Low volatility** | Kam volatile stocks zyada return — theory ke ulta | ★★☆ puzzle | ✅ |
| **Size** | Small-cap premium | ★★☆ kamzor pad gaya | ✅ |
| **Short-term reversal** | 1-week losers bounce | ★★☆ | ✅ |
| **Investment / asset growth** | Kam capex wali companies behtar | ★★☆ | 🟡 |

**Shortcut:** value / quality / low-vol NSE par **tradeable ETF** hain —
`MOVALUE`, `MOQUALITY`, `MOLOWVOL`. Factor rotation compute karne ki zarurat
nahi, in teeno ka relative performance dekh lo.

⚠️ Har factor **2–3 saal lagataar loss** de sakta hai. Single factor par kabhi
mat jao.

---

## B. Macro drivers — India specific (horizon: din–mahine)

| Driver | Kyun | Impact | Data |
|---|---|---|---|
| **Crude oil** | India 85% import karta hai — #1 driver | ★★★ | ✅ `NSE_COM` |
| **USDINR** | Import cost + IT/pharma earnings | ★★★ | ✅ `NCD_FO` |
| **FII flows** | Bada selling index gira deta hai | ★★★ | 🟡 EOD only |
| **DII flows** | FII selling ko cushion karta hai | ★★☆ | 🟡 EOD |
| **RBI policy** | Rate, liquidity, tone — banks par sabse zyada | ★★★ | 🟡 |
| **US 10Y + DXY** | Yield ↑ → EM se paisa nikalta hai | ★★★ | ✅ FRED |
| **US futures** | India ke hours me bhi LIVE | ★★★ | 🟡 |
| **Asian markets** | India se pehle khulte hain, overlap karte hain | ★★★ | 🟡 |
| **CPI inflation** | Rate expectations | ★★☆ | ✅ FRED |
| **Monsoon** | Rural demand → FMCG, auto, agri | ★★☆ | 🟡 IMD |

### USDINR ka asli kaam: sector rotation, index direction nahi

| Rupee kamzor (USDINR ↑) | Fayda | Nuksan |
|---|---|---|
| IT (TCS, Infy, Wipro) | ✅ dollar earnings | |
| Pharma (Sun, Dr Reddy's) | ✅ export heavy | |
| Oil marketing (BPCL, HPCL, IOC) | | ❌ crude dollar me |
| Airlines (IndiGo) | | ❌ fuel + lease dollar me |
| Paints / chemicals | | ❌ crude derivatives |

### Crude × Rupee = composite, alag features nahi

```
crude ↑  AND  rupee ↓   →  twin deficit stress  →  FII outflow
```

---

## C. Market internals (horizon: intraday–din)

| Factor | Kya batata hai | Data |
|---|---|---|
| **India VIX** | Fear + regime. **Sabse important single filter** | ✅ |
| **Advance/decline breadth** | Rally broad hai ya 5 stocks par tiki | ✅ compute |
| **Sector relative strength** | Paisa kahan ja raha hai | ✅ compute |
| **Delivery %** | Real buying vs speculation | 🟡 EOD |
| **Market regime** | Trending / choppy / high-vol | ✅ derive |

---

## D. Derivatives positioning (horizon: intraday–weekly)

| Factor | Kya batata hai | Evidence | Data |
|---|---|---|---|
| **OI change + price** | Long buildup / short covering | ★★★ | ✅ 3-min |
| **IV + skew** | Market kis taraf dara hua hai | ★★★ | ✅ compute |
| **Gamma Exposure (GEX)** | Dealer hedging flows | ★★☆ | ✅ compute |
| **Futures basis** | Premium/discount = positioning | ★★☆ | ✅ |
| **Rollover %** | Expiry par conviction | ★★☆ | ✅ |
| **PCR** | Sentiment extreme | ★☆☆ shor zyada | ✅ |
| **Max pain** | "Expiry yahan hogi" theory | ★☆☆ debatable | ✅ |

### GEX par India ka structural edge

- **Positive gamma** → dealers dips par buy, rallies par sell → range compress
- **Negative gamma** → dealers move **amplify** karte hain

US me GEX ki sabse badi kami: open interest **end-of-day** update hota hai, to
intraday positioning stale rehti hai. **NSE har 3 minute me OI deta hai** — yahan
GEX kaafi fresher hai.

⚠️ GEX ka research mostly vendors ka hai. Positioning **inferred** hoti hai,
observed nahi. Isliye **filter** ki tarah use karo ("aaj range-bound ya trendy"),
signal ki tarah nahi.

---

## E. Microstructure (horizon: second–minute)

Intraday ka asli alpha — aur yahan academic evidence sabse saaf hai.

| Factor | Kya hai | Evidence |
|---|---|---|
| **Order Flow Imbalance (OFI)** | Bid vs ask queue ka net change | ★★★ |
| **Order book imbalance** | 5-level depth ka pressure | ★★★ |
| **Bid-ask spread** | Liquidity + cost | ★★★ |
| **VWAP deviation** | Price fair value se kitna door | ★★☆ |
| **Trade sign / tick direction** | Aggressive buyer ya seller | ★★☆ |
| **Opening range (first 15 min)** | Din ka tone | ★★☆ |

**Ye sab WebSocket feed ke bina ban hi nahi sakte.** Polling se OFI impossible hai.

---

## F. Event-driven (kam frequency, zyada predictable)

| Event | Kyun |
|---|---|
| **Earnings surprise** | Post-earnings drift — documented |
| **Index inclusion/exclusion** | Forced buying/selling, date pehle se pata |
| **Block / bulk deals** | Smart money ka footprint |
| **Promoter pledge / shareholding** | Red flag ya confidence |
| **Buyback / dividend / split** | Known supply-demand shift |
| **Credit rating change** | Debt-heavy stocks par bada |
| **Regulatory approval** (CDSCO pharma) | Binary event |

---

## G. Uncertainty & geopolitics (ready-made indices)

| Index | Kya | Source |
|---|---|---|
| **GPR** (Geopolitical Risk) | Newspaper articles se, country-specific bhi | Caldara & Iacoviello, free |
| **India EPU** | 7 Indian newspapers se policy uncertainty | policyuncertainty.com / FRED |
| **India Google Uncertainty** | Google search data se — **RBI economists ne banaya** | policyuncertainty.com |
| **World Uncertainty Index** | IMF-linked | FRED `WUIIND` |

Ye indices **banane nahi padenge** — ready mile hue hain.

---

## H. Central bank communication

Research me saaf mila:

- Fed chair ki speech ka **emotional tone** (gussa, darr, bharosa) abnormal
  volume, returns aur volatility paida karta hai — policy action se **alag**,
  uske upar
- Emerging markets study me **India ke liye significant effect** mila
- RBI par specifically paper hai: *"Words that Move Markets"*

### ⚠️ Do critical caveats

**1. Sirf "surprise" matter karta hai.**
Agar market already expect kar raha hai ki rate cut hoga, aur rate cut ho jaye —
kuch nahi hota. Isliye har event feature me:

```
expected   ← consensus / survey / futures pricing
actual     ← jo hua
surprise = actual − expected      ← SIRF YE trade karo
```

**2. Reverse causality.** Research me mila ki achhe stock returns ke **baad**
central bank ka tone positive hota hai. Correlation dekhkar causation mat maan lena.

---

## I. Alternative data — India (underused, free)

| Source | Kya batata hai | Frequency | Kyun mast |
|---|---|---|---|
| **Vidyut Pravah** | All-India power demand (GW), time-block wise | **Real-time** | Industrial activity ka live proxy. IIP 6 hafte baad aata hai |
| **VAHAN** | Vehicle registrations, state-wise | **Daily** | **Retail** demand — company ke monthly wholesale number se pehle |
| GST collections | Consumption + compliance | Monthly | |
| UPI (NPCI) | Digital consumption | Monthly | |
| E-way bills | Goods movement | Monthly | |
| FASTag toll | Logistics | Daily | |
| IMD rainfall | Monsoon → rural demand | Daily | |

### VAHAN ka asli fayda

| | Kya count hota hai |
|---|---|
| Company ka monthly number | **Wholesale** — dealer ko kitni bheji |
| VAHAN | **Retail** — customer ne actually register karwayi |

Companies dealer ko stock bhej sakti hain, par RTO registration jhooth nahi
bolta. Auto analysts isi se results se pehle estimate karte hain.

⚠️ Telangana aur Lakshadweep VAHAN 4 par nahi hain. Current month ka data
partial hota hai — elapsed days ke hisaab se adjust karo.

---

## J. Calendar / seasonality

Month-end flows, results season, expiry week — ★☆☆. **Zyadatar data mining.**
Context ke liye rakho, signal ke liye nahi.

---

## Shortlist — yahin se shuru karna

| Horizon | Features | Count |
|---|---|---|
| **Intraday (5m)** | OFI, book imbalance, spread, VWAP deviation, opening range, VIX, OI change, GEX regime, sector RS, index trend | ~20 |
| **Swing (daily)** | Momentum, quality, low-vol, VIX regime, crude, USDINR, FII flow, breadth, event blackout | ~9 |

Baaki saare factors **context** banayenge ("aaj trade allowed hai ya nahi"),
**signal** nahi.

---

## Sources

- [MSCI: Foundations of Factor Investing](https://www.msci.com/documents/1296102/1336482/Foundations_of_Factor_Investing.pdf/004e02ad-6f98-4730-90e0-ea14515ff3dc)
- [Factor momentum (Wharton)](https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2018/03/Juhani.pdf)
- [Nifty 50 ke drivers](https://www.bajajamc.com/knowledge-centre/factors-affecting-nifty-50-movement)
- [India ka oil exposure](https://www.ebc.com/forex/nifty-6-month-low-brent-100-india-oil-exposure)
- [Order Flow Imbalance signal](https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html)
- [Market impact aur OFI (arXiv)](https://arxiv.org/pdf/2004.08290)
- [Measuring Geopolitical Risk (Fed)](https://www.federalreserve.gov/econres/ifdp/files/ifdp1222r1.pdf)
- [Central bank emotions aur stock market](https://www.zu.ac.ae/main/en/colleges/colleges/__college_of_interdisciplinary_studies/_papers/central-bank-sentiment-and-us-stock-market.pdf)
- [RBI communications ka asar](https://arxiv.org/pdf/2411.04808)
- [GEX explained](https://spotgamma.com/what-is-gex-gamma-exposure/)
- [Max pain really works?](https://www.truedata.in/blog/max-pain-theory-does-it-really-work)
- [SEBI FY26 F&O loss data](https://www.finnovate.in/learn/blog/sebi-fno-trader-losses-fy26-individual-derivatives)
