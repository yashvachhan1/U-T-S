# Agent 03 — US Markets & Fed

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min (09:15-15:30), har 10 min (18:00-01:30 IST) |
| **Calls/day** | ~70 |
| **Sources** | 8 |

---

## Kaam

US ka asar -- jo sabse bada hai. Aur US **band nahi hota** jis tarah se matter karta hai: CME futures 23 ghante chalte hain.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | S&P 500 futures | India ke hours me bhi LIVE | real-time |
| 2 | Nasdaq futures | Tech sentiment | real-time |
| 3 | MASPTOP50 ETF | S&P 500 proxy, Upstox se | real-time |
| 4 | MON100 ETF | Nasdaq 100 proxy, Upstox se | real-time |
| 5 | FRED DGS10 | US 10-year yield | daily |
| 6 | FRED DTWEXBGS | Dollar index | daily |
| 7 | Fed statements/speeches | federalreserve.gov | event |
| 8 | US economic calendar | CPI, jobs, GDP | scheduled |

---

## Kyun ye agent zaroori hai

**US futures India ke poore session me chalu rehte hain** -- sirf 02:30-03:30 IST ek ghanta break. Matlab ye intraday live signal hai, sirf overnight nahi.\n\nBade US events IST me: CPI/Jobs 18:00, market open 19:00, Fed decision 23:30, press conf 00:00, big tech earnings 01:35. **Sab India band hone ke baad -- yahin agle din ka gap banta hai.**

---

## Output schema

```json
{
  "agent": "us_markets",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "futures_change_pct": -0.8, "us10y": 4.32, "dxy_direction": "up", "gap_bias": "negative"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Delayed free source (15 min) se bhi kaam chal jaata hai BTST ke liye. Intraday ke liye stale flag karo.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Fed statements official site se hi. Fake Fed news market move karti hai -- source verification zaroori.

---

## Kaise measure hoga

Overnight gap prediction ka hit rate. Fed event ke baad ka move.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
