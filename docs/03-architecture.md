# 03 — Architecture & Code Structure

---

## Do loops — yahi poore system ki buniyad hai

Har data ek hi speed par nahi aata, aur har data ka kaam alag hai.

### 🐌 Slow loop — roz subah 08:00 IST, market khulne se pehle

Input: FII/DII, fundamentals, US overnight close, Asian open, events calendar,
uncertainty indices, alt data

Output: **"aaj ka context"**
- Market regime: trending / choppy / high-vol
- Risk budget: normal / reduced / zero
- Sector allow-list aur block-list
- Event blackout list (kis stock par aaj result hai)

> Ye **trigger nahi** hai. Ye din bhar ke liye **rules** set karta hai.

### ⚡ Fast loop — har tick / har candle, market hours me

Input: live price, OI, VIX, breadth, order book

Output: **actual trade decisions** — entry, exit, stop management

> Slow data decide karta hai **"aaj kya allowed hai"**.
> Fast data decide karta hai **"abhi kya karna hai"**.

---

## Data flow

```
 Upstox WS ──┐
 News feeds ─┤
 FRED ───────┼──► INGESTION ──► normalized events ──► TIME-SERIES STORE
 Crypto ─────┤       (12 services)                          │
 Alt data ───┘                                              │
                                                            ▼
                                                   FEATURE BUILDER
                                              (point-in-time correct)
                                                            │
                         ┌──────────────────────────────────┼──────────────┐
                         ▼                                  ▼              ▼
                   ML MODELS                        10 ANALYST        RULE SIGNALS
                   (numbers)                        AGENTS (LLM)      (deterministic)
                         │                                  │              │
                         └──────────────┬───────────────────┴──────────────┘
                                        ▼
                         CHIEF STRATEGIST  +  RED TEAM        (2 agents)
                                        │
                                        ▼
                              DECISION ENGINE                 deterministic
                                        │
                                        ▼
                              RISK ENGINE (hard limits)       final say
                                        │
                                        ▼
                         EXECUTION ──► Upstox ──► RECONCILIATION
                                        │
                                        ▼
                                   AUDIT LOG (append-only)
```

---

## Components — 31 total

### Data & infra services (12) — deterministic, koi LLM nahi

| # | Service | Kaam |
|---|---|---|
| 1 | `feed` | Upstox WebSocket — equity, currency, commodity ticks |
| 2 | `news_collector` | Primary sources + RSS polling |
| 3 | `crypto_collector` | BTC 24×7 |
| 4 | `global_collector` | US futures, Asian markets |
| 5 | `macro_collector` | FRED, FII/DII, EOD files |
| 6 | `altdata_collector` | Vidyut Pravah, VAHAN, GST, UPI |
| 7 | `uncertainty_collector` | GPR, EPU (monthly) |
| 8 | `feature_builder` | Raw → features, point-in-time correct |
| 9 | `model_scorer` | ML inference |
| 10 | `reconciler` | Broker se position match (broker hi sach hai) |
| 11 | `scheduler` | Kaun kab chalega |
| 12 | `audit` | Append-only decision + order log |

### Signal modules (2)
13. `ml_models` — LightGBM etc., numbers predict karte hain
14. `rule_signals` — EMA, Supertrend, OFI rules

### LLM agents (14) → detail [04-agents.md](04-agents.md) aur [agents/](agents/README.md)

### Core (3)
29. `decision_engine` · 30. `risk_engine` · 31. `execution`

---

## Teen rules jo kabhi nahi tootenge

1. **LLM order place nahi karega.** Agent structured output deta hai; faisla
   deterministic code karta hai.
2. **Agent risk KAM kar sakta hai, BADHA nahi.** "Mat trado" / "aadha size" —
   haan. "Double size" — kabhi nahi.
3. **Har agent output timestamp ke saath save hoga.** Backtest saved output
   replay karega, LLM dobara call nahi hoga → deterministic + point-in-time correct.

---

## Code structure

```
trading-app/
├── backend/
│   └── app/
│       ├── main.py                 # FastAPI (API + React build serve)
│       ├── config.py               # .env settings
│       ├── clock.py                # IST helpers (fixed +05:30, tzdata ke bina)
│       ├── db.py                   # SQLite — Phase 2 me Postgres
│       │
│       ├── upstox/                 # broker boundary
│       │   ├── client.py           # OAuth + REST wrapper
│       │   ├── instruments.py      # instrument master + search
│       │   ├── marketdata.py       # candles, quotes, LTP
│       │   ├── trading.py          # orders, positions, funds
│       │   └── feed.py             # 🆕 WebSocket market feed
│       │
│       ├── collectors/             # 🆕 har external source ka apna module
│       │   ├── base.py             # retry, rate limit, cache, polite scraping
│       │   ├── news.py
│       │   ├── crypto.py
│       │   ├── global_markets.py
│       │   ├── fred.py
│       │   ├── altdata.py          # Vidyut Pravah, VAHAN
│       │   └── uncertainty.py      # GPR, EPU
│       │
│       ├── store/                  # 🆕 persistence
│       │   ├── timeseries.py       # tick + candle storage
│       │   ├── events.py           # news, filings, events
│       │   ├── agent_outputs.py    # har agent ka output + timestamp
│       │   └── audit.py            # append-only
│       │
│       ├── features/               # 🆕 feature engineering
│       │   ├── microstructure.py   # OFI, book imbalance, spread
│       │   ├── technical.py        # momentum, volatility
│       │   ├── derivatives.py      # OI, IV, GEX
│       │   ├── macro.py            # crude×rupee, regime
│       │   └── registry.py         # feature catalogue + versioning
│       │
│       ├── agents/                 # 🆕 LLM layer
│       │   ├── base.py             # prompt, schema validation, retry, cache
│       │   ├── llm.py              # Groq client, model routing, key pool
│       │   ├── schemas.py          # pydantic output schemas
│       │   ├── analysts/           # 10 analyst agents
│       │   ├── synthesis/          # chief strategist, red team
│       │   └── review/             # post-trade, risk auditor
│       │
│       ├── engine/
│       │   ├── indicators.py       # pure-python TA
│       │   ├── backtester.py       # event-driven backtest
│       │   ├── runner.py           # live bot loop
│       │   ├── decision.py         # 🆕 decision engine
│       │   └── risk.py             # 🆕 risk engine + kill switches
│       │
│       ├── strategies/
│       │   ├── base.py             # strategy contract + registry
│       │   └── builtin.py          # built-in strategies
│       │
│       └── routers/
│           └── api.py              # HTTP endpoints
│
├── frontend/                       # React + Vite
│   └── src/
│       ├── App.jsx                 # shell
│       ├── api.js                  # fetch wrapper + formatting
│       ├── styles.css              # design system (light + dark)
│       ├── components/
│       └── views/
│
├── tools/                          # one-off diagnostics
│   └── check_market_data.py
│
├── docs/                           # ye documentation
├── data/                           # db, token, instrument cache (gitignored)
├── setup.bat · start.bat · dev.bat
└── requirements.txt
```

---

## Layering rules

```
collectors  →  store  →  features  →  signals  →  decision  →  risk  →  execution
```

- **Upar wala layer neeche wale ko import kar sakta hai, ulta kabhi nahi.**
- `collectors` ko pata nahi ki features kya hain.
- `features` ko pata nahi ki koi strategy hai.
- `execution` ko pata nahi ki signal kahan se aaya.
- Broker ka code sirf `upstox/` me — baaki system broker-agnostic rahega, to
  kal Zerodha add karna ho to sirf ek folder likhna padega.

---

## Storage

| Data | Volume | Store |
|---|---|---|
| Orders, bot runs, logs | Chhota | **PostgreSQL** |
| Agent outputs | Chhota | **PostgreSQL** (JSONB) |
| Audit log | Moderate | **PostgreSQL** (append-only) |
| Daily candles | Chhota | **PostgreSQL** |
| **Tick data** | **Bahut bada** | **Parquet** (→ TimescaleDB on VPS) |

PostgreSQL 18 is machine par already chal raha hai. SQLite abhi codebase me hai,
Phase 2 me migrate hoga.

**Rule:** ticks ko SQLite me mat daalna. Din bhar ka tick data GB me jaata hai.
Parquet files date ke hisaab se partition karo, DuckDB se query karo.

Measured benchmark aur poora storage design: [11-database.md](11-database.md)

---

## Process separation

Sab ek process me mat chalao — ek crash sab le dubega.

```
process 1: feed          (realtime, kabhi block nahi hona chahiye)
process 2: collectors    (network-heavy, slow, fail ho sakta hai)
process 3: agents        (LLM calls, slow)
process 4: decision+risk+execution  (critical path)
process 5: api + frontend
```

Beech me queue (Redis ya file-based) — taaki ek slow component dusre ko na roke.
Detail: [06-performance.md](06-performance.md)

---

## Configuration

| | |
|---|---|
| **Secrets** | `.env` — kabhi commit nahi ([05-security.md](05-security.md)) |
| **Risk limits** | Alag config file, code me hardcoded nahi — par **code me enforce** |
| **Feature set** | Versioned — kaunse features kis model ne use kiye, record rahega |
| **Agent prompts** | Version controlled, change par re-test |

**Zaroori:** risk limits config se aate hain par unka enforcement code me hai.
Config galat ho jaye to system **band ho jayega**, limits ignore nahi karega.
