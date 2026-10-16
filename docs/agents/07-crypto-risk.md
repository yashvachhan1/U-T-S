# Agent 07 — Crypto / Risk Appetite

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, 24x7 |
| **Calls/day** | ~96 |
| **Sources** | 3 |

---

## Kaam

BTC ko global risk appetite ka proxy maan kar padhna. Price predict karna nahi -- **risk-off warning** dena.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | Binance public API | BTC/ETH spot + klines, no auth | real-time |
| 2 | CoinGecko | Backup, broader coverage | ~1 min |
| 3 | Crypto news RSS | Regulatory, hacks, ETF flows | varies |

---

## Kyun ye agent zaroori hai

BTC **24x7 chalta hai** -- jab India band hai tab bhi global risk sentiment batata rehta hai.\n\n**Par asymmetric use karo:** BTC-equity correlation average sirf ~0.30 hai aur calm market me lagbhag zero. Crisis me 1.0 ki taraf bhagta hai -- sab risk assets ek saath girte hain.\n\nMatlab: BTC raat ko 5% gira = aaj risk kam karo. BTC 5% chadha = **koi signal nahi**.

---

## Output schema

```json
{
  "agent": "crypto_risk",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "btc_change_24h": -5.2, "risk_appetite": "deteriorating", "alert_only_on_downside": true
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Binance down -> CoinGecko. Dono down -> neutral.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Public API, koi key nahi. Rate limit respect karo.

---

## Kaise measure hoga

BTC drawdown ke baad Nifty ka next-day behaviour -- downside hit rate only.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
