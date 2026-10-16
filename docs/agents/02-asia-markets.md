# Agent 02 — Asia Markets

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, 05:30-13:30 IST |
| **Calls/day** | ~32 |
| **Sources** | 7 |

---

## Kaam

Asian markets ka mood -- jo India se **pehle khulte hain** aur hamare session ke saath chalte hain.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | Nikkei (Japan) | 05:30-11:30 IST | external |
| 2 | KOSPI (Korea) | 05:30-12:00 IST -- semis/IT ka lead | external |
| 3 | Hang Seng (HK) | 07:00-13:30 IST | HNGSNGBEES proxy + external |
| 4 | Shanghai | 07:00-12:30 IST -- metals, growth | external |
| 5 | Taiwan | 06:30-11:00 IST -- semiconductors | external |
| 6 | China/Korea news | Policy, stimulus, trade | RSS |
| 7 | MAHKTECH ETF | Hang Seng Tech, Upstox se live | real-time |

---

## Kyun ye agent zaroori hai

Ye US se **zyada kaam ke** hain intraday ke liye -- US cash market hamare time par band hota hai, Asia **live** chal raha hota hai. Nikkei/KOSPI India ke open se 3.5 ghante pehle khulte hain, to pre-open read mil jaata hai. Korea/Taiwan ka semiconductor move hamare IT sector ka lead indicator hai.

---

## Output schema

```json
{
  "agent": "asia_markets",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "markets": {"nikkei": -1.2, "hangseng": 0.4}, "pre_open_bias": "negative"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

External source fail ho to NSE ETF proxies (HNGSNGBEES, MAHKTECH) se kaam chalega -- wo Upstox se aate hain.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Sirf whitelisted market-data sources. Numbers validate -- 20% se zyada move = suspicious, flag karo.

---

## Kaise measure hoga

India ke opening gap se correlation. Pre-open bias ka hit rate.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
