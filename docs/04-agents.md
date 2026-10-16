# 04 — Agents: Overview, Sources & API Keys

14 LLM agents, teen layers me. Har agent ki apni detail file
[agents/](agents/README.md) me hai.

---

## Layer 1 — Analysts (12)

Ye parallel chalte hain. Kaam: **messy text/numbers → structured JSON**.
Model: fast/cheap.

| # | Agent | Sources | Frequency | Calls/day |
|---|---|---|---|---|
| 1 | [India News](agents/01-india-news.md) | 6 | har 2 min (market hours) | ~260 |
| 2 | [Asia Markets](agents/02-asia-markets.md) | 7 | har 15 min, 05:30–13:30 | ~32 |
| 3 | [US Markets & Fed](agents/03-us-markets.md) | 8 | har 15 min day + 10 min raat | ~70 |
| 4 | [Global Macro](agents/04-global-macro.md) | 5 | har 30 min, 24h | ~48 |
| 5 | [Commodity](agents/05-commodity.md) | 5 | har 15 min, 09:00–23:30 | ~58 |
| 6 | [Currency](agents/06-currency.md) | 5 | har 15 min, 09:00–17:00 | ~32 |
| 7 | [Crypto / Risk](agents/07-crypto-risk.md) | 3 | har 15 min, 24×7 | ~96 |
| 8 | [Sector Rotation](agents/08-sector-rotation.md) | 4 | har 15 min, market hours | ~25 |
| 9 | [Earnings](agents/09-earnings.md) | 4 | event-driven | ~20 |
| 10 | [Power & Policy](agents/10-power-policy.md) | 9 | naya filing aane par | ~50 |
| 11 | [Index Composition](agents/11-index-composition.md) | 7 | har 5 min, market hours | ~75 |
| 12 | [Industry & Stock Selection](agents/12-industry.md) | 8 | har 15 min + pre-open | ~30 |

**Analyst total: ~796 calls/day · 71 distinct sources**

Agents 11 aur 12 index ko andar se padhte hain:

- **#11** Nifty/BankNifty ko ek number ki tarah nahi, uske 50 constituents ke
  weighted sum ki tarah dekhta hai — breadth, contribution attribution aur
  futures basis. "Nifty +0.42% par 39 stocks neeche" index ke apne number se
  bilkul alag kahani hai.
- **#12** global cue ko industry se jodta hai, phir us industry me stock
  chunta hai. Agent #8 index-level sectors dekhta hai; ye ek level neeche
  jaata hai.

## Layer 2 — Synthesis (2)

Model: strong. Yahan asli reasoning hoti hai.

| # | Agent | Input | Frequency | Calls/day |
|---|---|---|---|---|
| 13 | [Chief Strategist](agents/13-chief-strategist.md) | 10 analysts + ML + rules | har 30 min | ~20 |
| 14 | [Red Team](agents/14-red-team.md) | Chief Strategist ka output | har proposal par | ~20 |

## Layer 3 — Review (2)

Offline. Latency matter nahi karti.

| # | Agent | Frequency | Calls/day |
|---|---|---|---|
| 15 | [Post-Trade Review](agents/15-post-trade-review.md) | Roz 16:00 | 1 |
| 16 | [Risk Auditor](agents/16-risk-auditor.md) | Hafte me ek baar | 0.2 |

---

## 🔑 Kitni Groq API keys chahiye — asli hisaab

**Total: ~840 calls/day**

Ab dekhte hain ye kitna load hai:

| | |
|---|---|
| Calls/day | 840 |
| Average | **~0.6 requests/minute** |
| Peak burst (sab analysts ek saath) | **~12 requests/minute** |

| Tier | Limit | Hamara load fit hota hai? |
|---|---|---|
| Groq **free** | ~30 RPM, ~1,000 RPD | 🟡 RPM theek, **daily limit tight** |
| Groq **paid** | ~1,000 RPM, 300K TPM | ✅ **aaram se** |

### Jawab: 16 keys ki zarurat nahi

| Stage | Keys | Kyun |
|---|---|---|
| **Development / testing** | **2–3 free keys** | Daily limits se bachne ke liye, pool me rotate |
| **Paper trading** | **1 paid key** | 840/day << 1000 RPM |
| **Production** | **1 paid + 1 backup** | Backup failover ke liye |

Aap chahein to **har agent ko apni key** de sakte ho — ye bura design nahi hai,
isse ek agent ka rate-limit issue dusre ko nahi chhuyega (bulkhead pattern).
Par **zarurat nahi hai**.

### Key pool design

```python
# agents/llm.py
KEY_POOL = [key1, key2, ...]        # env se, comma separated

#  - har agent ko ek primary key assign hoti hai (consistent routing)
#  - 429 (rate limit) aaye to agli key par failover
#  - saari keys exhaust → agent "unavailable" return karta hai
#  - agent unavailable  →  system trade karta rahega, bas us agent ka
#    input "neutral" maana jayega. Kabhi crash nahi.
```

> **Zaroori:** koi bhi agent fail ho jaye to system **rukega nahi**. Us agent ka
> view "neutral" maana jayega aur risk thoda kam kar diya jayega. Agent ka kaam
> risk ghatana hai — uska na hona trade rokta nahi, bas confidence kam karta hai.

---

## Model routing

| Layer | Model type | Kyun |
|---|---|---|
| Analysts (1–10) | **Fast / chhota** (GPT-OSS 20B, Qwen3.6 27B) | Structured extraction — reasoning kam chahiye, volume zyada |
| Synthesis (11–12) | **Strong** (GPT-OSS 120B) | Conflicting signals ko weigh karna — asli reasoning |
| Review (13–14) | **Strong** | Offline hai, latency matter nahi karti |

⚠️ Llama 3.3 70B aur Llama 3.1 8B **August 2026 me deprecate** ho chuke hain.
Groq console se current model list confirm karo aur `agents/llm.py` me update karo.

---

## Common output schema

Har analyst agent yahi shape return karega. Pydantic se **validate** hoga —
malformed output reject.

```json
{
  "agent": "us_markets",
  "ts": "2026-10-16T15:50:00+05:30",
  "view": "bearish",
  "confidence": 0.72,
  "horizon": "overnight",
  "sectors_positive": ["IT", "PHARMA"],
  "sectors_negative": ["OMC", "AVIATION"],
  "risk_flag": true,
  "reasoning": "...",
  "sources_used": ["cme_futures", "fred_dgs10"]
}
```

### Field rules

| Field | Rule |
|---|---|
| `view` | `bullish` / `bearish` / `neutral` — aur kuch nahi |
| `confidence` | 0.0–1.0, clamp hoga |
| `risk_flag` | `true` → risk engine size kam karega |
| `sectors_*` | Sirf known sector list se — unknown reject |
| `reasoning` | Log ke liye. **Decision me kabhi parse nahi hoga** |

> `reasoning` sirf insaan ke padhne ke liye hai. Decision engine sirf
> structured fields padhta hai. Isse prompt injection ka raasta band hota hai —
> detail [05-security.md](05-security.md) me.

---

## Agent outputs ka storage

```sql
CREATE TABLE agent_outputs (
    id          INTEGER PRIMARY KEY,
    agent       TEXT NOT NULL,
    ts          TEXT NOT NULL,          -- IST, ISO format
    payload     TEXT NOT NULL,          -- validated JSON
    model       TEXT NOT NULL,          -- kaunsa model tha
    prompt_ver  TEXT NOT NULL,          -- prompt ka version
    latency_ms  INTEGER,
    tokens_in   INTEGER,
    tokens_out  INTEGER
);
CREATE INDEX idx_agent_ts ON agent_outputs(agent, ts);
```

**Ye table backtest ki jaan hai.** Backtest me LLM dobara call nahi hoga —
yahi rows replay hongi. Isse:
- Backtest deterministic rehta hai ✅
- Point-in-time correct rehta hai ✅
- Har agent ki accuracy alag se measure ho sakti hai ✅
- Model/prompt badalne par purana result compare kar sakte hain ✅

---

## Agent kitna accurate hai — ye measure hoga

Har agent ke liye alag scorecard:

| Metric | Kya |
|---|---|
| Hit rate | `view` kitni baar sahi nikla (horizon ke hisaab se) |
| Calibration | Jab confidence 0.8 bola, tab 80% baar sahi tha? |
| Lead time | Price move se kitna pehle bola |
| Value-add | Is agent ko hata dein to backtest kitna bigadta hai |

**Jo agent value add nahi karta, use nikal denge.** 14 agents target nahi hai —
jo kaam karein wahi rakhenge.
