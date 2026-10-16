# Agent 11 — Chief Strategist

| | |
|---|---|
| **Layer** | Synthesis |
| **Model** | strong |
| **Frequency** | Har 30 min + pre-open + post-close |
| **Calls/day** | ~20 |
| **Sources** | 4 |

---

## Kaam

10 analysts + ML model + rule signals ko milakar **aaj ka stance** nikalna. Ye system ka 'dimaag' hai.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | 10 analyst agents | Saare structured outputs | 15 min |
| 2 | ML model scores | Numerical probabilities | real-time |
| 3 | Rule signals | Deterministic signals | real-time |
| 4 | Current positions | Kya already hold kiya hua hai | real-time |

---

## Kyun ye agent zaroori hai

Alag-alag agents aksar **ulta** bolenge -- Asia bearish, US futures bullish, crude stress badha, par sector rotation IT me strength dikha raha hai. Inhe weigh karna asli reasoning ka kaam hai, aur yahi LLM ki taakat hai.\n\nOutput ek **stance** hai, trade nahi: regime, risk budget, sector allow/block list, event blackouts.

---

## Output schema

```json
{
  "agent": "chief_strategist",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "stance": "risk_off", "risk_budget": "reduced", "allowed_sectors": ["IT","PHARMA"], "blocked_sectors": ["OMC"], "max_exposure_pct": 40
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Agar 3 se kam analysts available hon to stance 'insufficient_data' -- system neutral/minimum risk par chalta hai.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Input saare **already structured** hain (analysts se) -- raw text yahan nahi aata. Ye injection ke khilaf ek aur layer hai.

---

## Kaise measure hoga

Stance ka forward return. Risk-off bole us din market sach me gira?

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
