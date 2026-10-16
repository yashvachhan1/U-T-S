# 02 — Data Sources

Har source: kahan se, kitni jaldi, kitna kharcha, aur kaun use karega.

Legend — **Cost:** 🆓 free · 💰 paid · **Latency:** real-time / minutes / EOD / monthly

---

## 1. Upstox (primary broker feed)

Ek hi connection se ye sab milta hai. Instrument master me ab currency aur
commodity futures bhi hain (`backend/app/upstox/instruments.py`).

| Segment | Kya | Timing (IST) | Use |
|---|---|---|---|
| `NSE_EQ` | Stocks + **saare ETFs** | 09:15–15:30 | Core |
| `NSE_INDEX` | NIFTY, BANKNIFTY, **INDIA VIX** | 09:15–15:30 | Regime |
| `NSE_FO` | Index + stock F&O | 09:15–15:30 | OI, IV, GEX |
| `NCD_FO` | **USDINR, EURINR, GBPINR, JPYINR** futures | 09:00–**17:00** | Currency |
| `NSE_COM` | **CRUDEOIL, GOLD, SILVER, NATURALGAS** futures | 09:00–**23:30** | Commodity |
| `BSE_EQ` | BSE stocks | 09:15–15:30 | Cross-check |

### ETF proxies — foreign markets, Upstox se hi

| ETF | Track karta hai | instrument_key |
|---|---|---|
| `MASPTOP50` | **S&P 500 Top 50** | `NSE_EQ\|INF769K01HP3` |
| `MON100` | **Nasdaq 100** | `NSE_EQ\|INF247L01AP3` |
| `MAFANG` | US big tech (FANG+) | `NSE_EQ\|INF769K01HF4` |
| `HNGSNGBEES` | **Hang Seng** | `NSE_EQ\|INF204KB19I1` |
| `MAHKTECH` | Hang Seng Tech | `NSE_EQ\|INF769K01HS7` |
| `GOLDBEES` | Gold | `NSE_EQ\|INF204KB17I5` |
| `SILVERBEES` | Silver | `NSE_EQ\|INF204KC1402` |
| `MOVALUE` | Value factor | `NSE_EQ\|INF247L01BE5` |
| `MOQUALITY` | Quality factor | `NSE_EQ\|INF247L01BH8` |
| `MOLOWVOL` | Low-vol factor | `NSE_EQ\|INF247L01BL0` |

⚠️ ETF NAV se premium/discount par trade karte hain. Wo gap khud ek sentiment
signal hai, par exact index level ke liye external source behtar hai.

### ⚠️ Confirm karna baaki

Upstox docs saaf nahi batate ki **candle endpoints** `NSE_COM` / `NCD_FO` par
chalte hain ya nahi. Market feed ke segment codes me dono listed hain, to live
feed milna chahiye.

**Test:** `.venv\Scripts\python.exe tools\check_market_data.py`
Market khulne par aur band hone ke baad (jaise 16:30 aur 21:00) dono baar chalao.

---

## 2. FRED (St. Louis Fed) — 🆓, ek API key

**Sabse bada time-saver.** Ek free key se saara global macro.

| Series ID | Kya |
|---|---|
| `INDEPUINDXM` | **India Economic Policy Uncertainty** |
| `WUIIND` | World Uncertainty Index — India |
| `DGS10` | US 10-year treasury yield |
| `DTWEXBGS` | Dollar index (broad) |
| `DCOILBRENTEU` | Brent crude |
| `VIXCLS` | US VIX |
| `FEDFUNDS` | Fed funds rate |
| `CPIAUCSL` | US CPI |

**Latency:** daily series T+1, monthly series release ke din
**Link:** https://fred.stlouisfed.org · API key free, registration lagta hai

---

## 3. Uncertainty / geopolitical indices — 🆓

| Index | Source | Frequency |
|---|---|---|
| **GPR** (Geopolitical Risk) | [matteoiacoviello.com/gpr.htm](https://www.matteoiacoviello.com/gpr.htm) | Monthly, country-wise bhi |
| **India EPU** | [policyuncertainty.com/india_monthly.html](https://www.policyuncertainty.com/india_monthly.html) | Monthly |
| **India Google Uncertainty** | [policyuncertainty.com/india_gui.html](https://www.policyuncertainty.com/india_gui.html) | Monthly, RBI economists |

Excel/CSV download. Monthly hai, to collector mahine me ek baar chalega.

---

## 4. Crypto — 🆓, 24×7

| Source | Kya | Latency |
|---|---|---|
| Binance public API | BTC, ETH spot + klines | Real-time, no auth |
| CoinGecko | Backup, broader coverage | ~1 min |

**Kyun:** 24×7 chalta hai, to **India band hone ke baad** global risk appetite
ka live signal deta hai. BTC-equity correlation average ~0.30, par crisis me
1.0 ki taraf jaata hai — isliye **risk-off warning** ki tarah use karo,
predictor ki tarah nahi.

---

## 5. Asian & US markets — 🟡

| Market | IST timing | India ke saath overlap |
|---|---|---|
| ASX (Australia) | 04:30–10:30 | 09:15–10:30 |
| **Nikkei** (Japan) | 05:30–11:30 | 09:15–11:30 |
| **KOSPI** (Korea) | 05:30–12:00 | 09:15–12:00 |
| Taiwan | 06:30–11:00 | 09:15–11:00 |
| **Hang Seng** (HK) | 07:00–13:30 | 09:15–**13:30** |
| **Shanghai** | 07:00–12:30 | 09:15–12:30 |
| **US futures (CME)** | **23 ghante** | **poora session** |
| US cash | 19:00–01:30 | — |

### US — "band" nahi hota

| India ka time | US futures |
|---|---|
| 09:15 IST (open) | ✅ chalu |
| 12:00 IST | ✅ chalu |
| 15:30 IST (close) | ✅ chalu |

Sirf 02:30–03:30 IST ek ghanta maintenance break.

### Bade US events — IST me

| Event | IST |
|---|---|
| CPI / Jobs data | 18:00 |
| US market open | 19:00 |
| Fed decision | 23:30 |
| Fed press conference | 00:00 |
| US close | 01:30 |
| Big tech earnings | 01:35 |

Sab India band hone ke baad → **agle din ka gap yahin banta hai** → BTST window.

**Source options:** Yahoo Finance (🆓, ~15 min delayed) · paid feed (💰, real-time)
Shuruaat me NSE ETF proxies + delayed Yahoo kaafi hai.

---

## 6. India alternative data — 🆓

| Source | Kya | Frequency | Link |
|---|---|---|---|
| **Vidyut Pravah** | All-India power demand (GW), time-block wise, prev-day/year comparison | **Real-time** | [vidyutpravah.in](https://vidyutpravah.in/FAQ) |
| **VAHAN** | Vehicle registrations, state-wise (retail demand) | **Daily** | [vahan.parivahan.gov.in](https://vahan.parivahan.gov.in/nrservices/) |
| GST collections | Consumption | Monthly | gst.gov.in |
| UPI (NPCI) | Digital payments | Monthly | npci.org.in |
| E-way bills | Goods movement | Monthly | ewaybillgst.gov.in |
| IMD | Rainfall / monsoon | Daily | mausam.imd.gov.in |

Ye scraping se aayenge — official API nahi hai. **Polite scraping:** rate limit
rakho, cache karo, User-Agent set karo, robots.txt respect karo.

---

## 7. News & primary sources

> **Speed edge yahin hai.** Media ko 5–30 minute lagte hain. Hum seedha source
> se lenge.

| Tier | Source | Latency |
|---|---|---|
| **Primary** | NSE/BSE corporate announcements | 1–2 min poll |
| **Primary** | RBI website (speeches, circulars) | 1–2 min poll |
| **Primary** | SEBI circulars | 5 min poll |
| **Primary** | PIB press releases | 2 min poll |
| **Primary** | Company investor-relations pages | 5 min poll |
| Secondary | RSS: Moneycontrol, ET, Reuters, Business Standard | 1–5 min |
| Secondary | Verified official accounts (leaders, ministries) | varies |

### Disclosed insider activity — 🆓, underused

SEBI ke rules me promoters ko disclose karna **padta** hai:

| Kya | Kahan |
|---|---|
| Promoter buy/sell | NSE/BSE insider trading disclosures |
| Pledge changes | Exchange filings |
| Bulk / block deals | Exchange EOD files |
| SAST disclosures | Exchange filings |

Ye seedha "bade log kya kar rahe hain" ka data hai — aur **poori tarah public**.

---

## 8. LLM inference — Groq

| | |
|---|---|
| **Kya** | Fast inference for open models |
| **Speed** | ~280 tok/s (bade model) se ~560 tok/s (chhote model) |
| **Free tier** | ~30 RPM, daily limits kam |
| **Paid tier** | ~1000 RPM, 300K TPM |

⚠️ Llama 3.3 70B aur 3.1 8B **August 2026 me deprecate** ho chuke hain — ab
GPT-OSS 120B / GPT-OSS 20B / Qwen3.6 27B. Console me current list confirm karo.

Key ka hisaab: [04-agents.md](04-agents.md)

---

## Collector schedule

| Collector | Kab chalega | Kya laata hai |
|---|---|---|
| `feed` | 09:00–23:30 continuous | Upstox WebSocket ticks |
| `news` | 24×7, har 1–2 min | Primary sources + RSS |
| `crypto` | 24×7, har 1 min | BTC |
| `global` | 24×5 | US futures, Asian markets |
| `macro_daily` | Roz 08:00 IST | FRED, FII/DII, EOD files |
| `altdata` | Roz 08:00 + 20:00 | Vidyut Pravah, VAHAN |
| `uncertainty` | Mahine me 1 baar | GPR, EPU indices |

---

## Cost summary

| | |
|---|---|
| Upstox API | 🆓 (account hona chahiye) |
| FRED | 🆓 |
| GPR / EPU | 🆓 |
| Binance / CoinGecko | 🆓 |
| India alt data | 🆓 (scraping) |
| Yahoo Finance (delayed) | 🆓 |
| Groq free tier | 🆓 |
| **Groq paid** | 💰 production me zaroori |
| Real-time global feed | 💰 optional, baad me |
| **VPS (static IP ke liye)** | 💰 **zaroori** — [05-security.md](05-security.md) dekho |
