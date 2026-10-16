# 06 — Performance & Latency

---

## Pehle sach: microseconds hamare liye matter nahi karte

Aapne kaha "microsecond bhi impact karta hai". Ye **HFT ke liye sach hai** — par
unka setup alag hota hai:

| | HFT firm | Hum |
|---|---|---|
| Server kahan | Exchange ke **andar** (colocation) | VPS / ghar |
| Connection | Direct exchange link | Internet → broker → exchange |
| Broker | Khud member hai | Upstox beech me |
| Latency | **microseconds** | **milliseconds** |

Hamare raaste me **network aur broker** hain. Chahe code 1 microsecond me
chale, Upstox tak jaane me 10–50 ms lagenge. Matlab:

> Code ko microsecond tak optimize karna **bekaar** hai jab network 20 ms kha
> raha hai. Par **milliseconds** aur **architecture** bahut matter karte hain.

**Jahan asli faayda hai:**
1. Polling hatao, WebSocket lagao (5000 ms → 50 ms) — **100× faayda**
2. VPS Mumbai me rakho (150 ms → 15 ms) — **10× faayda**
3. Feature incrementally update karo, dobara compute mat karo — **100× faayda**

Ye teeno micro-optimization se kahin zyada dete hain.

---

## Latency budget — tick se order tak

Intraday strategy ke liye target: **tick se order tak < 100 ms**

| Stage | Target | Kaise |
|---|---|---|
| Upstox → hamara server | 10–30 ms | VPS Mumbai region me |
| Tick parse (protobuf) | < 1 ms | Pre-compiled schema |
| Feature update | **< 5 ms** | **Incremental**, full recompute nahi |
| ML inference | < 10 ms | Chhota model, pre-loaded |
| Decision + risk checks | < 5 ms | Pure python, koi I/O nahi |
| Order build + send | 10–30 ms | Keep-alive connection |
| **Total** | **~60–80 ms** | |

### LLM agents is budget me nahi hain

| | |
|---|---|
| LLM call | **200–2000 ms** |
| Fast loop me? | ❌ **Kabhi nahi** |

Agents **slow loop** me chalte hain. Unka output database me pada rehta hai,
aur fast loop usko **padh** leta hai — call nahi karta.

```
Agent (har 15 min)  →  DB me likha  →  Fast loop (har tick)  →  DB se padha
                                        (0.1 ms, koi network nahi)
```

---

## Asli optimizations — priority order

### 1. WebSocket, polling nahi — **100× faayda**

Abhi: har 5 second REST poll. Problems:
- Price 5 second purana
- Stop-loss 5 second late lagega
- Rate limit khaata hai
- **OFI calculate hi nahi ho sakta** (order book chahiye)

Baad me: ek WebSocket connection, tick-by-tick.

### 2. Incremental features — **100× faayda**

❌ **Galat:**
```python
# har tick par poora history dobara compute
def on_tick(tick):
    candles = load_all_candles()        # 10,000 rows
    ema = compute_ema(candles, 20)      # poora dobara
    return ema[-1]
```

✅ **Sahi:**
```python
# har tick par sirf naya value
class EMA:
    def __init__(self, period):
        self.k = 2 / (period + 1)
        self.value = None

    def update(self, price):            # O(1), ~0.001 ms
        self.value = price if self.value is None else \
                     price * self.k + self.value * (1 - self.k)
        return self.value
```

**Har feature O(1) update hona chahiye.** Ring buffer rakho fixed-size window
ke liye. Jo feature incrementally update nahi ho sakta, wo fast loop me nahi
aayega.

### 3. VPS Mumbai me — **10× faayda**

Static IP ke liye VPS waise bhi chahiye. To Mumbai region chuno:

| Location | Upstox tak RTT |
|---|---|
| Ghar (broadband) | 30–150 ms |
| VPS Mumbai | **5–20 ms** |
| VPS Singapore | 40–60 ms |
| VPS US | 200+ ms |

Mumbai me lena free upgrade hai — same paisa, 10× kam latency.

### 4. Hot path me ye mat karo

| ❌ Mat karo | ✅ Karo |
|---|---|
| `pandas` DataFrame har tick par | Plain list / numpy array |
| Database write har tick par | Batch write, background thread |
| Log every tick | Sample / aggregate |
| JSON parse har tick | Pre-compiled protobuf |
| Instrument key lookup har baar | Startup par resolve, cache |
| New HTTP connection har order | Keep-alive session |
| Blocking I/O event loop me | `async`, ya alag thread |

### 5. Process separation

```
process 1: feed          ← kabhi block nahi hona chahiye
process 2: collectors    ← slow, network-heavy, fail ho sakta hai
process 3: agents        ← LLM calls, 2 second tak
process 4: decision+risk+execution  ← critical path
process 5: api+frontend  ← user-facing
```

Beech me queue. Agar collector slow ho jaye, feed par koi asar nahi.

---

## Throughput

| | |
|---|---|
| Watchlist size | 50–200 instruments |
| Ticks/second (peak) | ~500–2000 |
| Features per instrument | ~20 |
| Feature updates/second | **10,000–40,000** |

Pure Python me 40,000 O(1) updates/second aaram se ho jaate hain **agar** har
update sach me O(1) ho. Nahi to turant blow up hoga.

---

## Storage performance

| Data | Volume/day | Format |
|---|---|---|
| Ticks | **1–5 GB** | **Parquet**, date-partitioned |
| 1-min candles | ~10 MB | Parquet |
| Daily candles | ~1 MB | SQLite ✅ |
| Orders / decisions | < 1 MB | SQLite ✅ |
| Agent outputs | < 1 MB | SQLite ✅ |

> **Ticks SQLite me mat daalna.** Writes block karenge aur feed pichhad jayegi.
> Parquet file me likho, DuckDB se query karo.

Tick writes **background thread** me, batch me (har 1000 ticks ya 5 second).

---

## Measure karo, guess mat karo

Har stage par timing record:

```python
# har decision cycle ka breakdown
{
  "tick_to_feature_ms": 2.1,
  "feature_to_score_ms": 8.4,
  "score_to_decision_ms": 1.2,
  "decision_to_order_ms": 24.0,
  "total_ms": 35.7
}
```

Dashboard par **p50 / p95 / p99** dikhao. p99 important hai — worst case me
system kitna slow hota hai.

**Alert:** p99 > 200 ms → kuch galat hai.

---

## Optimization kab karein

```
1. Pehle kaam karne wala banao
2. Phir measure karo
3. Phir sirf wahi optimize karo jo sach me slow hai
```

Premature optimization se code complex ho jaata hai aur bug chhup jaate hain.
Trading system me **bug ki keemat latency se zyada hai**.

### Har strategy ko latency chahiye bhi nahi

| Strategy | Latency ki zarurat |
|---|---|
| **Swing** (din–hafte) | Koi nahi. 1 second bhi theek |
| **BTST** (overnight) | Koi nahi. Decision shaam ko lena hai |
| **Intraday** (minute) | 100 ms theek |
| **Scalping** (second) | < 50 ms — ye hamare liye mushkil |
| **HFT** (microsecond) | ❌ Retail ke liye possible nahi |

**Isliye build order:** Swing → BTST → Intraday. Pehli do me latency ka
jhanjhat hai hi nahi, to wahan strategy aur risk par dhyan de sakte ho.
Intraday tab aayega jab WebSocket aur VPS taiyar honge.
