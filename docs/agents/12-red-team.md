# Agent 12 — Red Team (Devil's Advocate)

| | |
|---|---|
| **Layer** | Synthesis |
| **Model** | strong |
| **Frequency** | Har trade proposal par |
| **Calls/day** | ~20 |
| **Sources** | 3 |

---

## Kaam

Chief Strategist ke **khilaf** argue karna. 'Ye trade kyun galat ho sakta hai?'

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | Chief Strategist output | Jiska virodh karna hai | per proposal |
| 2 | Same analyst outputs | Dusre nazariye se | per proposal |
| 3 | Historical failures | Pichhle similar trades jo fail hue | per proposal |

---

## Kyun ye agent zaroori hai

Ye sabse **underrated** agent hai. LLM naturally agreeable hota hai -- agar aap ek hi agent se poochho 'ye trade theek hai?', wo aksar haan bolega.\n\nIsliye ek agent ka kaam **sirf virodh** karna hai. Jab Chief Strategist aur Red Team **disagree** karein, system automatically **size aadha** kar deta hai.\n\nOverconfidence hi sabse bada killer hai, aur ye uska structural ilaaj hai.

---

## Output schema

```json
{
  "agent": "red_team",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bullish | bearish | neutral",
  "confidence": 0.0,
  "horizon": "intraday | overnight | swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "risk_flag": false,
  "reasoning": "insaan ke padhne ke liye -- decision me parse NAHI hoga",
  "sources_used": [],
  "objections": ["crude spike abhi price me nahi hai"], "agreement": false, "suggested_size_multiplier": 0.5
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Failure mode

Red Team unavailable -> size automatically 0.5x (safe default). Kabhi full size nahi jab challenge nahi hua.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

`suggested_size_multiplier` **sirf 0.0-1.0** clamp hoga. 1 se upar ka value reject -- Red Team risk badha nahi sakta.

---

## Kaise measure hoga

Jab Red Team ne objection kiya, wo trades kaisa perform kiye vs baaki?

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
