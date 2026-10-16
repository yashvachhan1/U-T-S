# Agent 10 — Power & Policy Intelligence

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Naya filing/statement aane par |
| **Calls/day** | ~50 |
| **Sources** | 9 |

---

## Kaam

Leaders ke actions, policy events, aur disclosed insider activity -- sector impact me translate karna, **historical base rates** ke saath.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | PIB press releases | Govt announcements, media se pehle | 2 min poll |
| 2 | RBI website | Speeches, circulars, notifications | 1-2 min poll |
| 3 | SEBI circulars | Regulatory changes | 5 min poll |
| 4 | NSE/BSE insider disclosures | **Promoter buy/sell -- legally disclosed** | EOD |
| 5 | Pledge disclosures | Promoter pledge changes | EOD |
| 6 | Bulk/block deals | Smart money footprint | EOD |
| 7 | Official leader accounts | Verified statements | varies |
| 8 | Diplomatic/event calendar | State visits, summits | scheduled |
| 9 | Court / regulatory orders | Binary events for specific stocks | varies |

---

## Kyun ye agent zaroori hai

**Tier 4 (disclosed insider activity) sabse underused hai.** SEBI ke rules me promoters ko batana **padta** hai jab wo apni company ke shares khareedte/bechte hain. Ye seedha 'bade log kya kar rahe hain' ka data hai, aur **poori tarah public**.\n\n**'Agar X wahan gaya to kya hoga'** -- ye vibes se nahi, historical base rates se:\n\n```\nEvent: PM Russia ja rahe hain, defence deal expected\n  -> pichhle 6 aise events dhoondo\n  -> defence stocks ne kya kiya\n  -> output: avg +3.2%, hit 4/6, confidence 0.55\n```\n\nJab tak 6-10 analogues na hon, confidence 'low' rahegi aur risk engine size kam kar dega.

---

## Output schema

```json
{
  "agent": "power_policy",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "event_type": "state_visit", "historical_n": 6, "avg_move_5d": 3.2, "hit_rate": "4/6", "sectors": ["DEFENCE"]
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Analogue kam hon to confidence low -- agent phir bhi output deta hai, par risk engine use halka weight deta hai.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

**Sirf public sources.** Login ke peeche ka data, paywalled content, ya non-public information nahi. Government sites par polite scraping -- rate limit, cache, robots.txt.

---

## Kaise measure hoga

Base rate prediction vs actual. Confidence calibration -- jab 0.6 bola tab 60% baar sahi tha?

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
