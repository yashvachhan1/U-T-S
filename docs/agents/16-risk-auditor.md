# Agent 16 — Risk Auditor

| | |
|---|---|
| **Layer** | Review |
| **Model** | strong |
| **Frequency** | Hafte me ek baar |
| **Calls/day** | 0.2 |
| **Sources** | 4 |

---

## Kaam

Risk configuration khud khatarnak to nahi ho gayi -- ye check karna.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | Risk config | Current limits | weekly |
| 2 | Last week trades | Actual risk liya gaya | weekly |
| 3 | Drawdown history | Pattern | weekly |
| 4 | Position concentration | Chhupa hua correlation | weekly |

---

## Kyun ye agent zaroori hai

Risk limits dheere-dheere khisak jaate hain -- 'ek baar badha lete hain' se shuru hota hai. Ye agent hafte me ek baar poochta hai: kya hum sach me utna risk le rahe hain jitna socha tha?\n\n**Chhupa hua correlation** sabse khatarnak hai -- IT ke 3 alag stocks lagte hain 3 positions, par sach me ek hi bet hai. Ye agent wo pakadta hai.

---

## Output schema

```json
{
  "agent": "risk_auditor",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "config_drift": true, "hidden_correlation": ["TCS","INFY","WIPRO"], "effective_positions": 2.1, "warnings": ["..."]
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Fail ho to agle hafte -- critical path me nahi.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Config **padh** sakta hai, **badal** nahi sakta. Sirf warning deta hai.

---

## Kaise measure hoga

Warnings jo ignore ki gayin -- unse baad me nuksan hua?

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
