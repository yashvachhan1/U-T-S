# Agent 01 — India News

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 2 min (09:00-16:00), har 15 min baaki time |
| **Calls/day** | ~260 |
| **Sources** | 6 |

---

## Kaam

Domestic news aur exchange filings ko structured impact me badalna. Ye sabse high-frequency analyst hai kyunki material information yahin sabse pehle aati hai.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | NSE corporate announcements | Material disclosures, results, orders | 1-2 min poll |
| 2 | BSE corporate announcements | Same, cross-check ke liye | 1-2 min poll |
| 3 | Moneycontrol RSS | Broad market news | 1-5 min |
| 4 | Economic Times RSS | Business news | 1-5 min |
| 5 | Business Standard RSS | Policy + markets | 1-5 min |
| 6 | Company IR pages | Press releases, media ko milne se pehle | 5 min poll |

---

## Kyun ye agent zaroori hai

Exchange filing media me aane se 5-30 min pehle exchange site par hoti hai. Wahi window hamara speed edge hai. News par trade karne ki koshish mat karo -- HFT already move kar chuka hoga. Iska asli kaam **blackout aur exit** hai: 'is stock par bura news hai, position band karo / naya mat lo'.

---

## Output schema

```json
{
  "agent": "india_news",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "affected_stocks": ["RELIANCE"], "event_type": "earnings|order_win|regulatory|management_change|other"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Source down ho to purana output stale mark hoga. 30 min se purana output = us stock par naya entry nahi, exits allowed.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

News text **untrusted input** hai. Structured output only, `reasoning` kabhi parse nahi hoga. Source whitelist -- random site ka text agent tak nahi jayega.

---

## Kaise measure hoga

Hit rate: jo stocks flag kiye, unka agla move. Lead time: filing timestamp vs media timestamp -- edge kitne second ka hai.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
