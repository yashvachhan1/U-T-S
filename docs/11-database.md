# 11 — Database & Storage

> **Ye doc revise hua hai.** Pehle maine SQLite recommend kiya tha. Sawal uthaya
> gaya ki Postgres kyun nahi — aur wo sawal sahi tha. Neeche revised
> recommendation hai, wajah ke saath.

---

## Pehle: SQLite ya PostgreSQL?

### Maine pehle SQLite kyun kaha tha

- Zero setup
- Ek file = aasan backup
- Codebase me already chal raha hai
- Point lookup me tez (benchmark neeche)

### Par ye reasoning adhoori thi

| Factor | Sach |
|---|---|
| "Setup ka jhanjhat" | ❌ **PostgreSQL 18.4 aapke machine par already chal raha hai** (port 5432). Setup cost zero hai |
| "Zero ops" | ❌ Wo Windows service ke roop me already chal raha hai |
| Concurrency | 🔴 **Phase 6 me processes alag honge.** SQLite me **ek hi writer** ho sakta hai. Ye blocker ban jayega |
| VPS deployment | 🔴 Engine VPS par, dashboard local — SQLite network par kaam nahi karta |
| Agent outputs | 🟠 Postgres me **JSONB** milta hai — indexed, queryable. SQLite me sirf TEXT |
| Backup / monitoring | 🟠 `pg_dump`, replication, proper tooling |

### ✅ Revised: PostgreSQL use karenge

Transactional sab kuch Postgres me jayega — orders, positions, bot runs, agent
outputs, audit log, macro series.

**Migration Phase 2 me hogi**, jab `store/` layer banega. Abhi SQLite chalta
rahega taaki Phase 1 ruke nahi.

---

## Doosra sawal: ticks bhi Postgres me?

Yahan jawab utna seedha nahi hai.

### Teen options

| Option | Kya | Trade-off |
|---|---|---|
| **A. Plain PostgreSQL** | Normal table + btree/BRIN | Simple, par row-store — analytics scan dheema, storage bada |
| **B. PostgreSQL + TimescaleDB** | Hypertable + columnstore compression | **90–98% compression**, time-series queries fast, **continuous aggregates** |
| **C. Parquet + DuckDB** | Files + embedded analytics engine | Best compression + scan, portable archives, par alag system |

### TimescaleDB genuinely strong hai

Jo cheez isse sabse attractive banati hai:

| Feature | Kya |
|---|---|
| **Columnstore compression** | Production reports me 88–98% reduction |
| **Continuous aggregates** | **Ticks se 1m/5m/daily candles apne aap maintain honge** — hum haath se nahi banayenge |
| **Ek hi system** | Ek SQL, ek backup, ek monitoring |
| Retention policies | Purana data auto-drop |

Continuous aggregates wali baat badi hai — warna hume khud rollup pipeline
likhni padegi.

⚠️ **Par ek dikkat:** TimescaleDB Windows par aasani se install nahi hota —
Docker chahiye hoga. Linux VPS par trivial hai. Official install docs se confirm
karna.

---

## 📊 Benchmark — is machine par asli numbers

200,000 ticks, 8 columns (ts, symbol, ltp, volume, bid, ask, bid_qty, ask_qty):

| Store | Write | Size | Point lookup | Full scan |
|---|---|---|---|---|
| SQLite (WAL, indexed) | 0.74 s | 15.1 MB | **5.0 ms** ✅ | 507.6 ms |
| Parquet + DuckDB (zstd) | **0.28 s** | **4.2 MB** ✅ | 16.0 ms | **6.6 ms** ✅ |

1,000,000 ticks par (pehla run):

| Store | Write | Size | Point | Full scan |
|---|---|---|---|---|
| SQLite (WAL, indexed) | 3.46 s | 75.5 MB | **16.7 ms** ✅ | 3027.9 ms |
| Parquet + DuckDB | **1.28 s** | **16.4 MB** ✅ | 34.4 ms | **24.5 ms** ✅ |

> Point lookup me **row-store jeetta hai** (2–3×).
> Full scan me **columnar 77–123× tez hai**.

Yahi pattern Postgres par bhi milega — plain Postgres row-store hai.

### Postgres khud benchmark karein

Password chat me mat daalna. Shell me env var set karke:

```bash
set PGPASSWORD=aapka_password
.venv\Scripts\python.exe tools\bench_storage.py
```

Ye script Postgres (COPY + btree), SQLite aur Parquet — teeno compare karta hai,
aur per-year projection bhi deta hai. Postgres credentials na hon to baaki do
ka comparison phir bhi chalta hai.

---

## ✅ Final stack

```
┌──────────────────────────────────────────────────────────────┐
│  PostgreSQL 18          orders, positions, bot runs,         │
│  (already running)      agent outputs, audit log, macro      │
│                         → ACID, concurrency, JSONB, network  │
├──────────────────────────────────────────────────────────────┤
│  Parquet + DuckDB       ticks, candles, features             │
│  (abhi)                 → 4.6x compression, 77-123x scan     │
│                                                              │
│  ya TimescaleDB         Linux VPS par -- ek hi system,       │
│  (VPS par evaluate)     continuous aggregates free milte hain│
├──────────────────────────────────────────────────────────────┤
│  Redis (Phase 6+)       hot cache, inter-process queues      │
└──────────────────────────────────────────────────────────────┘
```

### Ticks ke liye faisla kyun tala hua hai

Abhi Parquet se shuru karo kyunki:
- Windows par zero extra setup (TimescaleDB ko Docker chahiye)
- Best compression aur scan speed
- Archive portable hai — file copy kar do, bas

VPS (Linux) par shift karte waqt TimescaleDB evaluate karna, kyunki tab:
- Install trivial hai
- Ek hi system ka fayda zyada dikhega
- Continuous aggregates se rollup code bachega

**`store/` layer ki wajah se ye badalna aasan rahega** — baaki code ko pata hi
nahi hoga ki neeche kya hai.

> **DuckDB aur Postgres competitor nahi hain.** DuckDB embedded analytics engine
> hai — wo Parquet bhi padh sakta hai aur **Postgres bhi**. Research aur ad-hoc
> analysis ke liye wo rahega, chahe storage kuch bhi ho.

---

## Kya kahan jayega

| Data | Store | Kyun |
|---|---|---|
| Orders (paper + live) | **PostgreSQL** | ACID, concurrency |
| Positions | **PostgreSQL** | ACID |
| Bot runs + logs | **PostgreSQL** | Transactional |
| Agent outputs | **PostgreSQL** (JSONB) | Indexed JSON queries |
| Watchlist, settings | **PostgreSQL** | Chhota |
| Audit log | **PostgreSQL** (append-only) | Durability critical |
| Macro / alt series | **PostgreSQL** | Tiny, relational |
| News / events | **PostgreSQL** (JSONB + full-text) | Text search milta hai |
| **Ticks** | **Parquet** (→ TimescaleDB on VPS) | Bada, scan-heavy |
| 1m / 5m candles | **Parquet** | Bada |
| Daily candles | **PostgreSQL** | Chhota, point lookup zyada |
| Computed features | **Parquet** | Columnar |
| Instrument master | JSON file | 61,290 rows — file theek hai |

---

## PostgreSQL — schemas

```sql
-- agent outputs: backtest replay ki jaan
CREATE TABLE agent_outputs (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    payload     JSONB       NOT NULL,      -- indexed JSON, TEXT nahi
    model       TEXT        NOT NULL,
    prompt_ver  TEXT        NOT NULL,
    latency_ms  INTEGER,
    tokens_in   INTEGER,
    tokens_out  INTEGER
);
CREATE INDEX ON agent_outputs (agent, ts DESC);
CREATE INDEX ON agent_outputs USING GIN (payload);   -- JSON ke andar query

-- audit log: append-only
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type      TEXT NOT NULL,         -- decision|order|risk_block|halt
    instrument_key  TEXT,
    features_snap   JSONB,
    agents_snap     JSONB,
    model_score     DOUBLE PRECISION,
    decision        TEXT,
    risk_checks     JSONB,
    order_payload   JSONB,
    broker_response JSONB
);
CREATE INDEX ON audit_log (ts DESC);
CREATE INDEX ON audit_log (event_type, ts DESC);

-- append-only enforce karo
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- macro / alt data
CREATE TABLE macro_series (
    series_id   TEXT        NOT NULL,      -- 'INDEPUINDXM', 'vidyut_demand_gw'
    ts          TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    source      TEXT        NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL,      -- point-in-time ke liye zaroori
    PRIMARY KEY (series_id, ts)
);
```

> `fetched_at` **bahut zaroori** hai. Macro data **revise** hota hai — GDP/CPI ka
> pehla estimate baad me badal jaata hai. Backtest me wahi value dikhni chahiye
> jo **us waqt** pata thi, aaj ki revised nahi.

### Postgres settings

```sql
-- trading workload ke liye
ALTER SYSTEM SET shared_buffers = '2GB';          -- RAM ka ~25%
ALTER SYSTEM SET effective_cache_size = '6GB';
ALTER SYSTEM SET work_mem = '64MB';
ALTER SYSTEM SET synchronous_commit = 'on';       -- paisa hai, risk mat lo
ALTER SYSTEM SET max_connections = 50;
```

| Rule | Kyun |
|---|---|
| `synchronous_commit = on` | Order data kabhi lose nahi hona chahiye |
| Connection **pool** use karo | Har query par naya connection nahi |
| Prepared statements | Repeat queries tez |
| Writes batch me | `COPY` bulk insert ke liye, `INSERT` loop nahi |

---

## Parquet — sahi tarike se

```python
import pyarrow as pa, pyarrow.parquet as pq

BUFFER_SIZE = 10_000          # background thread me batch

def flush(buffer: list[dict]):
    pq.write_to_dataset(
        pa.Table.from_pylist(buffer),
        root_path="data/market/ticks",
        partition_cols=["date"],
        compression="zstd",
    )
```

| Rule | Kyun |
|---|---|
| Batch writes (10k rows) | Har tick par write = disaster |
| Background thread | Feed loop kabhi block na ho |
| zstd | Best size/speed tradeoff |
| Date partition | Query sirf relevant folder padhe |
| Din ke end par compact | Chhoti files slow karti hain |

```python
# DuckDB -- koi import step nahi, seedha files par SQL
duckdb.connect().execute("""
    SELECT symbol, AVG(ltp) FROM 'data/market/ticks/date=2026-10-16/*.parquet'
    WHERE symbol = 'RELIANCE' GROUP BY symbol
""").fetchdf()
```

---

## Folder layout

```
data/
├── market/                       # Parquet
│   ├── ticks/date=2026-10-16/
│   ├── candles_1m/date=2026-10-16/
│   └── features/date=2026-10-16/
├── token.json                    # Upstox token (gitignored)
└── instruments.json              # instrument master cache

PostgreSQL database: trading_desk  (server-side)
```

---

## Retention

| Data | Hot | Archive | Delete |
|---|---|---|---|
| Ticks | 90 din | 1 saal (compressed) | 1 saal baad |
| 1m candles | 1 saal | 5 saal | — |
| Daily candles | **Hamesha** | — | **Kabhi nahi** |
| Orders / audit | **Hamesha** | — | **Kabhi nahi** |
| Agent outputs | **Hamesha** | — | **Kabhi nahi** |
| News / events | 1 saal | 3 saal | — |

TimescaleDB use karein to retention policy ek line me:
```sql
SELECT add_retention_policy('ticks', INTERVAL '90 days');
```

---

## Backup

| Kya | Kaise | Kab |
|---|---|---|
| PostgreSQL | `pg_dump -Fc trading_desk` | Roz |
| Parquet | Cloud sync ya external drive | Hafte me |
| `.env` | Encrypted, alag jagah | Change par |

```bash
# roz ka backup
pg_dump -Fc -d trading_desk -f backups/trading-$(date +%F).dump

# restore
pg_restore -d trading_desk backups/trading-2026-10-16.dump
```

Postgres ka ek bada fayda: **PITR (point-in-time recovery)** — WAL archiving se
kisi bhi second par wapas ja sakte ho. SQLite me ye nahi milta.

---

## Migration plan (Phase 2)

```
1. store/ layer banao -- saara DB access uske peeche
2. Postgres schema banao (upar wale DDL se)
3. Dual-write: SQLite aur Postgres dono me likho
4. Verify: dono me same data hai
5. Read switch karo Postgres par
6. SQLite writes band
7. SQLite file archive karke rakh do
```

**Step 3–4 zaroori hain.** Seedha switch mat karna — pehle proof chahiye ki
Postgres me sab sahi ja raha hai.

### `store/` layer

```
features/  →  store.get_candles(...)  →  [Parquet | TimescaleDB]
engine/    →  store.save_order(...)   →  [PostgreSQL]
```

Baaki code ko pata nahi hoga ki neeche kya hai. Isse ticks ko Parquet se
TimescaleDB me le jaana ek layer badalne ka kaam reh jayega.

---

## Rules — short me

| ✅ Karo | ❌ Mat karo |
|---|---|
| Transactional data Postgres me | SQLite par multi-process writes |
| Ticks Parquet/TimescaleDB me | Ticks plain Postgres table me |
| JSONB agent outputs ke liye | TEXT me JSON thoos kar |
| `COPY` bulk insert ke liye | `INSERT` loop me |
| Writes batch me | Har tick par write |
| `fetched_at` macro data me | Revised data se backtest |
| `pg_dump -Fc` se backup | Data directory copy |
| `store/` layer ke peeche sab | Code me SQL bikhra hua |
| Redis tab jab zarurat ho | Pehle se infra lagana |

---

## Sources

- [TimescaleDB compression — 90% reduction (production)](https://dev.to/polliog/timescaledb-compression-from-150gb-to-15gb-90-reduction-real-production-data-bnj)
- [Cloudflare: TimescaleDB at scale](https://blog.cloudflare.com/timescaledb-art/)
- [add_columnstore_policy() reference](https://www.tigerdata.com/docs/reference/timescaledb/hypercore/add_columnstore_policy)
- [Compressed chunk backfill](https://stormatics.tech/blogs/backfill-compressed-timescaledb-chunks)
