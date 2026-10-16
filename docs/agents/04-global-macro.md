# Agent 04 — Global Macro

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 30 min, 24 ghante |
| **Calls/day** | ~48 |
| **Sources** | 5 |

---

## Kaam

US ke alawa global factors -- ECB, BoJ, geopolitics, trade policy, war.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | GPR Index | Geopolitical risk, country-wise bhi | monthly |
| 2 | India EPU Index | Policy uncertainty, 7 Indian newspapers se | monthly |
| 3 | ECB / BoJ announcements | Rate decisions, QE | event |
| 4 | Reuters world RSS | Geopolitics, conflicts | 1-5 min |
| 5 | Trade policy news | Tariffs, sanctions, agreements | varies |

---

## Kyun ye agent zaroori hai

GPR aur EPU **ready-made indices** hain -- banane nahi padte. GPR Fed economists ka hai, American Economic Review me published. India EPU 7 Indian newspapers se banta hai. Dono free download.\n\nGeopolitical shock crude aur risk appetite dono hilata hai -- isliye ye agent commodity aur crypto agents ke saath cross-check hota hai.

---

## Output schema

```json
{
  "agent": "global_macro",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "gpr_level": 112.4, "epu_india": 89.1, "regime": "elevated_risk"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Monthly indices stale ho sakte hain -- last value carry forward, par age flag karo.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Index files official sites se. CSV parse karte waqt schema validate.

---

## Kaise measure hoga

High GPR periods me hamara drawdown kam hua ya nahi.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
