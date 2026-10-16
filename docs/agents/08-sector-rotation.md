# Agent 08 — Sector Rotation

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, market hours |
| **Calls/day** | ~25 |
| **Sources** | 4 |

---

## Kaam

Paisa kis sector aur kis **factor** me ja raha hai.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | NSE sector indices | NIFTY IT, BANK, AUTO, PHARMA, FMCG... | real-time |
| 2 | MOVALUE ETF | Value factor -- tradeable | real-time |
| 3 | MOQUALITY ETF | Quality factor | real-time |
| 4 | MOLOWVOL ETF | Low volatility factor | real-time |

---

## Kyun ye agent zaroori hai

**Shortcut mila hai:** value / quality / low-vol academic factors NSE par **tradeable ETF** hain. Factor exposure compute karne ki zarurat nahi -- in teeno ka relative performance dekho, pata chal jayega ki market abhi kis factor ko favour kar raha hai.\n\nSector relative strength + factor rotation = 'aaj kya kaam kar raha hai' ka saaf jawab.

---

## Output schema

```json
{
  "agent": "sector_rotation",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "leading_sectors": ["IT","PHARMA"], "lagging_sectors": ["OMC"], "factor_leader": "quality"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

ETF illiquid ho sakte hain -- volume check karo, low volume par signal weak.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Internal data only, koi external text nahi -- injection risk kam.

---

## Kaise measure hoga

Leading sector ka forward return vs lagging sector.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
