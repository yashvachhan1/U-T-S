# 09 — Development Process

Har feature isi process se banega. Testing ki detail:
[10-testing.md](10-testing.md)

---

## Teen principles

### 1. Correctness > speed > features

Trading system me **bug ki keemat latency se zyada hai**. Ek galat order ka
nuksan 50 ms ki bachat se kahin bada hai.

```
pehle sahi banao  →  phir measure karo  →  phir sirf slow wala optimize karo
```

### 2. Paper hamesha pehle

Koi bhi naya component **paper mode me kam se kam 2 hafte** chalega, chahe
testing kitni bhi achhi ho. Code sahi hona aur strategy kaam karna — do alag
cheezein hain.

### 3. Har step independently testable ho

Agar ek step ko akele test nahi kar sakte, to wo step **bahut bada** hai.
Chhota karo.

---

## Step ka template — har feature isi loop se

```
┌─────────────────────────────────────────────────────┐
│ 1. SPEC      Kya banega, kya nahi. Done ka matlab?  │
│ 2. CONTRACT  Input/output schema pehle likho        │
│ 3. BUILD     Chhote commits, har ek chalta hua      │
│ 4. UNIT TEST Logic akele test karo                  │
│ 5. INTEGRATE Asli data se jodo                      │
│ 6. VERIFY    Chal raha hai -- proof ke saath        │
│ 7. DOCUMENT  docs/ update karo                      │
│ 8. PAPER     2 hafte paper mode                     │
└─────────────────────────────────────────────────────┘
```

**Step 6 par rukna mat.** "Code likh diya" done nahi hai. "Chal raha hai aur
maine dekha" done hai.

---

## Definition of Done

Koi bhi step tab tak done nahi jab tak ye saare tick na hon:

- [ ] Code likha aur chal raha hai
- [ ] Unit tests pass (jahan logic hai)
- [ ] Asli data ke saath ek baar chalaya — output dekha
- [ ] Failure case test kiya (network down, API error, khali data)
- [ ] Logs me kuch sensitive nahi
- [ ] `docs/` me update hua
- [ ] Purana kuch toota nahi (regression check)
- [ ] Config me koi hardcoded secret nahi

Risk ya order se juda koi bhi code ho to **extra**:

- [ ] Paper mode me test kiya
- [ ] Limits artificially trigger karke dekhe
- [ ] Audit log me entry aa rahi hai

---

## Environments

| Env | `PAPER_TRADING` | Kahan | Kiske liye |
|---|---|---|---|
| **dev** | `true` | Laptop | Code likhna, test |
| **paper** | `true` | VPS | Asli data, nakli paisa, 2-3 mahine |
| **live** | `false` | VPS | Asli paisa, chhoti capital se |

**Teeno ka `.env` alag.** Live wala `.env` kabhi laptop par nahi aayega.

```bash
# dev
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload

# frontend hot reload ke saath
dev.bat

# production build
cd frontend && npm run build
```

---

## Git workflow

```
main        ← hamesha chalne layak, paper par deploy hone layak
  │
  └─ feat/websocket-feed      ← ek feature, ek branch
  └─ feat/fred-collector
  └─ fix/limit-orders
```

### Commit rules

| Rule | Kyun |
|---|---|
| Ek commit = ek logical change | Revert karna aasan |
| Commit message me **kyun**, sirf kya nahi | 3 mahine baad samajh aayega |
| Toota hua code commit mat karo | `main` hamesha chalta rahe |
| `.env` kabhi commit nahi | Git history se nikalna mushkil hai |

```bash
# har commit se pehle
git status                    # koi .env ya data/ to nahi?
git diff --cached             # jo ja raha hai wo dekh lo
```

---

## Code standards

### Python

| Rule | |
|---|---|
| Type hints | Public functions par zaroori |
| Docstrings | **Kyun**, kya nahi. Code kya karta hai wo code batata hai |
| Pure functions | Jahan ho sake — test karna aasan |
| No bare `except:` | Hamesha specific exception |
| Secrets config se | Code me kabhi nahi |
| f-strings | `%` ya `.format()` nahi |

```python
# achha -- kyun bata raha hai
def market_open_now() -> bool:
    """Always IST, whatever the machine's own timezone is set to."""
    return is_open("equity")

# bura -- kya bata raha hai, jo pehle se dikh raha hai
def market_open_now() -> bool:
    """Returns True if market is open."""
    return is_open("equity")
```

### JavaScript / React

| Rule | |
|---|---|
| Functional components + hooks | Class components nahi |
| Ek component = ek kaam | 300 line se bada = tod do |
| API calls `api.js` me | Component me `fetch` nahi |
| Koi secret frontend me nahi | Build public hai |

### Layering — ye kabhi mat todna

```
collectors  →  store  →  features  →  signals  →  decision  →  risk  →  execution
```

Upar wala neeche wale ko import kar sakta hai, **ulta kabhi nahi**.
Broker ka code sirf `upstox/` me — isse kal dusra broker add karna aasan rahega.

---

## Phase-wise steps

### Phase 1 — Compliance (1–2 din)

| Step | Kya | Done ka matlab |
|---|---|---|
| 1.1 | MARKET → LIMIT order conversion | Paper order LIMIT price ke saath banta hai |
| 1.2 | LTP ± band se limit price calculate | Band config se aata hai, test kiya |
| 1.3 | Market Price Protection handle | Order body me sahi field |
| 1.4 | Order rate limiter (<10/sec) | Burst test — 20 orders bheje, 10/sec se zyada nahi gaye |
| 1.5 | Algo tag har order par | Order book me tag dikh raha hai |
| 1.6 | Idempotency key | Same key dobara bheja, duplicate nahi bana |
| 1.7 | VPS + static IP | Upstox app me IP registered, order ja raha hai |
| 1.8 | SSH tunnel access | Dashboard tunnel se khulta hai, public se nahi |

### Phase 2 — Collectors (1–2 hafte)

Har collector ke liye **same 5 steps**:

| Step | Kya |
|---|---|
| a | Source se ek baar data fetch karke dekho (manual script) |
| b | `collectors/base.py` ka interface implement karo |
| c | Store me likhna — schema fix karo |
| d | Scheduler me daalo |
| e | 3 din chalao, data quality check karo |

Order: `feed` (WebSocket) → `crypto` → `fred` → `global_markets` → `news` →
`altdata` → `uncertainty`

🔴 **Gate:** `feed` sabse pehle, kyunki microstructure features uske bina ban
hi nahi sakte.

### Phase 3 — Features (1 hafta)

| Step | Kya | Done ka matlab |
|---|---|---|
| 3.1 | Feature registry + versioning | Har feature ka naam, version, dependency record |
| 3.2 | Incremental O(1) updates | 40,000 updates/sec benchmark pass |
| 3.3 | **Point-in-time correctness** | Backtest me future data leak nahi — test se proof |
| 3.4 | Microstructure features | OFI, book imbalance live data par chal rahe |

🔴 **Gate:** point-in-time test pass kiye bina Phase 4 shuru nahi.

### Phase 4 — ML (2–3 hafte)

| Step | Kya |
|---|---|
| 4.1 | Labeling — target kya hai (agle N bar ka return) |
| 4.2 | Train/test split — **walk-forward**, random nahi |
| 4.3 | Baseline model (LightGBM) |
| 4.4 | Probability calibration |
| 4.5 | Benchmark vs buy & hold **aur** rule strategies |
| 4.6 | Feature importance + stability |

🔴 **Gate:** buy & hold ko nahi harata? Aage mat badho.

### Phase 5 — Agents (2 hafte)

| Step | Kya |
|---|---|
| 5.1 | `agents/base.py` — prompt, schema, retry, cache |
| 5.2 | `agents/llm.py` — Groq client, key pool, failover |
| 5.3 | Ek agent end-to-end (India News) |
| 5.4 | Baaki 9 analysts |
| 5.5 | Chief Strategist + Red Team |
| 5.6 | Agent output storage + replay |
| 5.7 | Per-agent scorecard |

🔴 **Gate:** 5.3 (ek agent poora) ke bina baaki 9 mat banao. Pehle ek ko sahi karo.

### Phase 6 — Decision + Risk (1–2 hafte)

| Step | Kya |
|---|---|
| 6.1 | Decision engine |
| 6.2 | Risk engine + saare limits |
| 6.3 | **Har limit artificially trigger karke test** |
| 6.4 | Execution — LIMIT, idempotency, retry |
| 6.5 | Reconciliation loop |
| 6.6 | Audit log |
| 6.7 | Kill switch (auto + manual) |

🔴 **Gate:** 6.3 aur 6.7 test kiye bina paper mode me bhi mat chalao.

---

## Jab kuch toote

```
1. Ruk jao. Aur code mat likho.
2. Log padho. Audit log padho.
3. Chhota reproduction banao.
4. Root cause dhoondho -- symptom ka ilaaj mat karo.
5. Fix karo.
6. Uska TEST likho taaki dobara na ho.
7. Soch lo: aisa aur kahan ho sakta hai?
```

**Step 6 sabse zaroori hai.** Har bug ka ek test hona chahiye.

### Production me bug mile to

```
1. KILL SWITCH -- pehle trading band karo
2. Phir investigate karo
3. Fix karo + test
4. Paper me 2 din
5. Phir live
```

Live chalte hue debug mat karo.

---

## Weekly rhythm

| Din | Kya |
|---|---|
| Soamwar | Hafte ka plan, Risk Auditor ka output padho |
| Mangal–Guru | Build |
| Shukra | Testing, documentation, cleanup |
| Weekend | Market band — deploy, migration, bada refactor |

**Trading hours me production deploy mat karo.** Market band hone ke baad ya
weekend par.

---

## Kya nahi karna

| ❌ | Kyun |
|---|---|
| Phase 7 (paper test) skip karna | Sabse mehngi galti |
| Backtest achha dekh kar seedha live | Backtest jhooth bolta hai |
| Risk limits "abhi ke liye" badhana | Wapas kam nahi hote |
| Live par debug karna | Paisa jaata rahega |
| Ek saath 10 features banana | Kuch bhi poora nahi hoga |
| Test chhodna kyunki "jaldi hai" | Jaldi me hi bug aate hain |
| 50 features ek model me | Overfitting guaranteed |
