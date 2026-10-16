"""PostgreSQL vs SQLite vs Parquet -- tick-shaped data par, is machine par.

Postgres ka password chat me mat daalna. Shell me env var set karke chalao:

    set PGPASSWORD=aapka_password
    .venv\\Scripts\\python.exe tools\\bench_storage.py

Ya poora DSN:

    set PG_DSN=postgresql://postgres:pw@127.0.0.1:5432/postgres

Postgres na mile to script SQLite aur Parquet ka comparison phir bhi dega.
"""
from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path

N = int(os.environ.get("BENCH_ROWS", "1000000"))
TMP = Path(os.environ.get("TEMP", "/tmp")) / "bench_storage"
TMP.mkdir(exist_ok=True)

COLS = ["ts", "symbol", "ltp", "volume", "bid", "ask", "bid_qty", "ask_qty"]


def mb(p: Path) -> float:
    return p.stat().st_size / 1_048_576


def make_rows(n: int) -> list[tuple]:
    random.seed(7)
    syms = [f"SYM{i:03d}" for i in range(200)]
    base = 1_760_000_000_000
    return [(
        base + i * 50, syms[i % 200],
        round(random.uniform(100, 3000), 2), random.randint(1, 5000),
        round(random.uniform(100, 3000), 2), round(random.uniform(100, 3000), 2),
        random.randint(1, 900), random.randint(1, 900),
    ) for i in range(n)]


# --------------------------------------------------------------- SQLite
def bench_sqlite(rows) -> dict:
    db = TMP / "ticks.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)

    t0 = time.perf_counter()
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE ticks (ts INTEGER, symbol TEXT, ltp REAL,
                   volume INTEGER, bid REAL, ask REAL,
                   bid_qty INTEGER, ask_qty INTEGER)""")
    con.executemany("INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.execute("CREATE INDEX idx ON ticks(symbol, ts)")
    con.commit()
    write = time.perf_counter() - t0

    t0 = time.perf_counter()
    con.execute("SELECT symbol, AVG(ltp), COUNT(*) FROM ticks "
                "WHERE symbol='SYM042' GROUP BY symbol").fetchall()
    q_point = time.perf_counter() - t0

    t0 = time.perf_counter()
    con.execute("SELECT symbol, AVG(ltp) FROM ticks GROUP BY symbol").fetchall()
    q_scan = time.perf_counter() - t0

    size = mb(db)
    con.close()
    return dict(name="SQLite (WAL, indexed)", write=write, size=size,
                point=q_point, scan=q_scan)


# -------------------------------------------------------------- Parquet
def bench_parquet(rows) -> dict | None:
    try:
        import duckdb
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None

    f = TMP / "ticks.parquet"
    f.unlink(missing_ok=True)

    t0 = time.perf_counter()
    table = pa.table({c: [r[i] for r in rows] for i, c in enumerate(COLS)})
    pq.write_table(table, f, compression="zstd")
    write = time.perf_counter() - t0

    d = duckdb.connect()
    t0 = time.perf_counter()
    d.execute(f"SELECT symbol, AVG(ltp), COUNT(*) FROM '{f}' "
              f"WHERE symbol='SYM042' GROUP BY symbol").fetchall()
    q_point = time.perf_counter() - t0

    t0 = time.perf_counter()
    d.execute(f"SELECT symbol, AVG(ltp) FROM '{f}' GROUP BY symbol").fetchall()
    q_scan = time.perf_counter() - t0

    return dict(name="Parquet + DuckDB (zstd)", write=write, size=mb(f),
                point=q_point, scan=q_scan)


# ------------------------------------------------------------- Postgres
def bench_postgres(rows) -> list[dict]:
    try:
        import psycopg
    except ImportError:
        print("  psycopg nahi mila -- pip install 'psycopg[binary]'")
        return []

    dsn = os.environ.get("PG_DSN")
    if not dsn:
        pw = os.environ.get("PGPASSWORD")
        if not pw:
            print("  PGPASSWORD ya PG_DSN set nahi -- Postgres skip")
            return []
        dsn = f"postgresql://postgres:{pw}@127.0.0.1:5432/postgres"

    try:
        con = psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        print(f"  Postgres connect fail: {exc}")
        return []

    out = []
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench_ticks")
        cur.execute("""CREATE TABLE bench_ticks (
            ts BIGINT, symbol TEXT, ltp DOUBLE PRECISION, volume INTEGER,
            bid DOUBLE PRECISION, ask DOUBLE PRECISION,
            bid_qty INTEGER, ask_qty INTEGER)""")

        # COPY -- Postgres ka sabse tez bulk load
        t0 = time.perf_counter()
        with cur.copy("COPY bench_ticks FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute("CREATE INDEX bench_idx ON bench_ticks(symbol, ts)")
        cur.execute("ANALYZE bench_ticks")
        write = time.perf_counter() - t0

        cur.execute("SELECT pg_total_relation_size('bench_ticks')")
        size = cur.fetchone()[0] / 1_048_576

        t0 = time.perf_counter()
        cur.execute("SELECT symbol, AVG(ltp), COUNT(*) FROM bench_ticks "
                    "WHERE symbol='SYM042' GROUP BY symbol")
        cur.fetchall()
        q_point = time.perf_counter() - t0

        t0 = time.perf_counter()
        cur.execute("SELECT symbol, AVG(ltp) FROM bench_ticks GROUP BY symbol")
        cur.fetchall()
        q_scan = time.perf_counter() - t0

        out.append(dict(name="PostgreSQL 18 (COPY, btree)", write=write,
                        size=size, point=q_point, scan=q_scan))

        # TimescaleDB mile to columnstore bhi dekh lo
        cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")
        if cur.fetchone():
            print("  TimescaleDB available hai -- hypertable test ho sakta hai")
        else:
            print("  TimescaleDB installed nahi (Windows par Docker chahiye)")

        cur.execute("DROP TABLE IF EXISTS bench_ticks")
    con.close()
    return out


def main() -> None:
    print(f"\nGenerating {N:,} ticks...\n")
    rows = make_rows(N)

    results = [bench_sqlite(rows)]
    pqres = bench_parquet(rows)
    if pqres:
        results.append(pqres)
    print("PostgreSQL:")
    results += bench_postgres(rows)

    print(f"\n{'=' * 78}")
    print(f"  {N:,} ticks, 8 columns")
    print(f"{'=' * 78}")
    print(f"  {'Store':<30} {'Write':>9} {'Size':>10} {'Point':>10} {'Scan':>10}")
    print(f"  {'-' * 74}")
    for r in results:
        print(f"  {r['name']:<30} {r['write']:>7.2f} s {r['size']:>8.1f} MB "
              f"{r['point'] * 1000:>8.1f} ms {r['scan'] * 1000:>8.1f} ms")

    if len(results) > 1:
        best_size = min(results, key=lambda r: r["size"])
        best_scan = min(results, key=lambda r: r["scan"])
        best_point = min(results, key=lambda r: r["point"])
        print(f"\n  Sabse chhota : {best_size['name']}")
        print(f"  Scan me tez  : {best_scan['name']}")
        print(f"  Point lookup : {best_point['name']}")

    days = 250
    print(f"\n  Per-year projection ({days} trading days, {N:,} ticks/day):")
    for r in results:
        print(f"    {r['name']:<30} {r['size'] * days / 1024:>7.1f} GB/year")
    print()


if __name__ == "__main__":
    main()
