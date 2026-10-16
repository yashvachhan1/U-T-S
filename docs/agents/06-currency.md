# Agent 06 — Currency

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, 09:00-17:00 IST |
| **Calls/day** | ~32 |
| **Sources** | 5 |

---

## Kaam

USDINR aur uska **sector-wise** asar. Index direction nahi -- sector rotation.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | USDINR futures | NCD_FO, Upstox se, 17:00 tak | real-time |
| 2 | EURINR / GBPINR | NCD_FO, cross-check | real-time |
| 3 | FRED DTWEXBGS | Dollar index (DXY) | daily |
| 4 | RBI announcements | Intervention, policy | event |
| 5 | RBI speeches | Tone analysis | event |

---

## Kyun ye agent zaroori hai

Rupee se 'market upar ya neeche' poochna galat sawal hai. Asli asar **sector-wise** hota hai:\n\n- Rupee kamzor -> IT, pharma **fayda** (dollar earnings)\n- Rupee kamzor -> OMC, airlines, paints **nuksan** (import cost)\n\n**Crude x Rupee composite** -- dono saath badhein to twin deficit stress, FII outflow.\n\nUSDINR futures **weekly expire** hote hain -- hardcoded key har hafte toot jayegi. Runtime par nearest expiry resolve karo.

---

## Output schema

```json
{
  "agent": "currency",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "usdinr": 95.17, "zscore_60d": 1.8, "rbi_intervention_flag": true, "sector_tilt": {"IT": 1, "OMC": -1}
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Currency segment 17:00 par band -- uske baad last value stale marked.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

RBI announcements sirf rbi.org.in se.

---

## Kaise measure hoga

Sector tilt ka hit rate -- IT vs OMC relative performance.

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
