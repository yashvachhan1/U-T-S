# Agent 05 — Commodity

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, 09:00-23:30 IST |
| **Calls/day** | ~58 |
| **Sources** | 5 |

---

## Kaam

Crude, gold, silver, metals -- aur unka India par asar.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | CRUDEOIL futures | NSE_COM, Upstox se, 23:30 tak | real-time |
| 2 | GOLD / SILVER futures | NSE_COM | real-time |
| 3 | GOLDBEES / SILVERBEES | NSE_EQ ETFs, equity hours | real-time |
| 4 | FRED DCOILBRENTEU | Brent benchmark | daily |
| 5 | OPEC / energy news | Supply decisions | event |

---

## Kyun ye agent zaroori hai

**Crude India ka #1 macro driver hai** -- 85% import hota hai, dollar me pay karna padta hai.\n\nCommodity segment **23:30 tak chalta hai** -- equity band hone ke baad bhi. Matlab 15:30-23:30 ka crude move agle din ke gap ka seedha hint hai. BTST ke liye ye sona hai.\n\nGold risk-off hedge hai; gold/silver ratio risk appetite batata hai.

---

## Output schema

```json
{
  "agent": "commodity",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "crude_change_pct": 2.1, "gold_change_pct": -0.3, "stress_index": 0.7, "sectors_hit": ["OMC","AVIATION","PAINTS"]
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

NSE_COM candle support confirm nahi hai -- `tools/check_market_data.py` se verify karo. Fail ho to FRED daily + ETF proxies se kaam chalega.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Price sanity check -- crude 10% move ek candle me = data error suspect, verify before acting.

---

## Kaise measure hoga

OMC/aviation stocks par crude move ka predicted vs actual asar.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
