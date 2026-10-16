# Agent 15 — Post-Trade Review

| | |
|---|---|
| **Layer** | Review |
| **Model** | strong |
| **Frequency** | Roz 16:00 IST |
| **Calls/day** | 1 |
| **Sources** | 4 |

---

## Kaam

Aaj kya hua aur kyun -- insaan ke padhne ke liye, aur system ke seekhne ke liye.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | Audit log | Har decision + order | daily |
| 2 | Agent outputs | Us waqt kisne kya bola tha | daily |
| 3 | Actual P&L | Kya nikla | daily |
| 4 | Market data | Context | daily |

---

## Kyun ye agent zaroori hai

Har trade ka **reason** record hota hai audit log me. Ye agent usse padh kar daily report banata hai: kaunsa agent sahi tha, kaunsa galat, kya pattern dikha.\n\nYe **offline** hai -- koi trade decision nahi leta. Par iske output se hi pata chalta hai ki kaunsa agent value add kar raha hai aur kaunsa nikalna chahiye.

---

## Output schema

```json
{
  "agent": "post_trade_review",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "trades_today": 3, "agents_correct": ["us_markets"], "agents_wrong": ["crypto_risk"], "lesson": "..."
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Fail ho to kal chal jayega -- critical path me nahi hai.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Internal data only.

---

## Kaise measure hoga

Khud measure nahi hota -- ye baaki ko measure karta hai.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
