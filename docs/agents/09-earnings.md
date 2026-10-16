# Agent 09 — Earnings

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Event-driven (results season me zyada) |
| **Calls/day** | ~20 |
| **Sources** | 4 |

---

## Kaam

Results, guidance, aur management ka tone.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | NSE/BSE results filings | Actual numbers | 1-2 min poll |
| 2 | Earnings call transcripts | Management tone -- LLM ka perfect kaam | post-call |
| 3 | Analyst estimate consensus | Surprise calculate karne ke liye | varies |
| 4 | Results calendar | Kis din kiska result | scheduled |

---

## Kyun ye agent zaroori hai

**Sirf surprise matter karta hai** -- actual minus expected. Agar market already expect kar raha tha, to achha result bhi move nahi karta.\n\nPost-earnings drift documented factor hai -- surprise ke baad stock usi direction me kuch din aur chalta hai.\n\nTranscript tone LLM ka sabse achha use case hai: guidance me hedging language, management ka confidence -- numbers me ye nahi dikhta.

---

## Output schema

```json
{
  "agent": "earnings",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "surprise_pct": 8.2, "guidance": "raised|maintained|lowered", "tone": "confident", "drift_expectation": "positive"
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Consensus estimate na mile to surprise calculate nahi hoga -- tab sirf tone analysis, confidence kam.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Transcript untrusted text hai -- structured output only.

---

## Kaise measure hoga

Post-earnings drift ka hit rate, 1/5/20 din par.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
