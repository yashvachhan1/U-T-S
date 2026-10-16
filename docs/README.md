# Trading Desk — System Documentation

Ye folder poore system ka blueprint hai: kaunsa data, kaunse signal, kitne
agent, kya risk limits, aur code kaise structure hoga.

> **Abhi kya ban chuka hai:** broker connection, charts, manual orders,
> backtester, rule-based bots (Phase 0). Baaki sab is documentation ke hisaab
> se banega.

---

## Padhne ka order

| # | File | Kya hai |
|---|---|---|
| 1 | [01-research.md](01-research.md) | Market kin factors par chalta hai — evidence ke saath |
| 2 | [02-data-sources.md](02-data-sources.md) | Har data source: URL, frequency, cost, kaise milega |
| 3 | [03-architecture.md](03-architecture.md) | Components, data flow, code structure |
| 4 | [04-agents.md](04-agents.md) | 14 agents ka overview, API key ka hisaab, model routing |
| 5 | [05-security.md](05-security.md) | Secrets, auth, prompt injection, order safety |
| 6 | [06-performance.md](06-performance.md) | Latency budget — aur microseconds par sach |
| 7 | [07-risk-limits.md](07-risk-limits.md) | Saare limits, kill switches |
| 8 | [08-roadmap.md](08-roadmap.md) | Phase-wise build plan |
| 9 | [09-development.md](09-development.md) | Step-wise development process, code standards |
| 10 | [10-testing.md](10-testing.md) | Testing — unit se live tak |
| 11 | [11-database.md](11-database.md) | Database stack — benchmark ke saath |
| 12 | [agents/](agents/README.md) | Har agent ki apni detail file |

---

## System ek nazar me

```
                    ┌──────────────────────────────┐
                    │   DATA LAYER  (12 services)  │
                    │  feed · macro · news · alt   │
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │   FEATURE STORE              │
                    │  point-in-time correct       │
                    └───────────────┬──────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
      ┌───────▼───────┐     ┌───────▼───────┐    ┌────────▼────────┐
      │  ML MODELS    │     │ 10 ANALYST    │    │  RULE SIGNALS   │
      │  (numbers)    │     │ AGENTS (LLM)  │    │  (deterministic)│
      └───────┬───────┘     └───────┬───────┘    └────────┬────────┘
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │  CHIEF STRATEGIST + RED TEAM │
                    │  (2 agents, synthesis)       │
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │  DECISION ENGINE             │  ← deterministic
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │  RISK ENGINE  (hard limits)  │  ← deterministic, final say
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │  EXECUTION  →  Upstox        │
                    └──────────────────────────────┘
```

---

## Teen rules jo kabhi nahi tootenge

1. **LLM kabhi order place nahi karega.** Agent structured output deta hai;
   trade ka faisla deterministic code karta hai, jise backtest kiya ja sakta hai.

2. **Agent risk KAM kar sakta hai, BADHA nahi.** Koi bhi agent "aaj mat trado"
   ya "aadha size lo" keh sakta hai. "Double size lo" koi nahi keh sakta.
   Hard limits code me hain, prompt me nahi.

3. **Har agent ka output timestamp ke saath save hoga.** Backtest me LLM dobara
   call nahi hoga — saved output replay hoga. Isse backtest deterministic
   rehta hai aur point-in-time correct bhi.

---

## Component count

| Layer | Count |
|---|---|
| Data / infra services | 12 |
| ML + rule signal modules | 2 |
| Analyst agents (LLM) | 10 |
| Synthesis agents (LLM) | 2 |
| Review agents (LLM) | 2 |
| Decision + Risk + Execution | 3 |
| **Total** | **31** |

LLM agents: **14**. Groq API keys ka hisaab [04-agents.md](04-agents.md) me hai.
