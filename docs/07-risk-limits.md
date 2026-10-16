# 07 — Risk Limits & Kill Switches

> SEBI FY26 data: **87.7% individual F&O traders ne paisa khoya** (₹91,685 crore
> total, average ₹1.17 lakh per trader). FY25 me 91%, FY24 me 93%.
>
> Ye log strategy ki wajah se nahi haare. **Risk management ki wajah se haare.**

---

## Pehla principle

```
Agent / model / strategy  →  sirf SUGGEST karte hain
Risk engine               →  FINAL faisla, hamesha
```

Risk engine ko koi override nahi kar sakta — na LLM, na strategy, na config.
Config galat ho to system **band hoga**, limits ignore nahi karega.

---

## Hard limits — hamesha on

### Position level

| Limit | Default | Kyun |
|---|---|---|
| Max risk per trade | **1% capital** | Ek trade account nahi dubaye |
| Max position size | **10% capital** | Concentration se bacho |
| Hard stop-loss | **Har position par, exception nahi** | Bina stop ke entry nahi |
| Max leverage | **1× (koi leverage nahi)** shuru me | MIS/MTF baad me |

### Portfolio level

| Limit | Default | Kyun |
|---|---|---|
| Max open positions | **5** | Manage kar sako |
| Per-sector exposure | **30%** | Sector crash se bacho |
| Correlated positions | **Max 2** | IT ke 3 stocks = 1 hi bet hai |
| Total exposure | **80% capital** | Cash buffer rakho |

### Daily

| Limit | Default | Action |
|---|---|---|
| **Daily loss kill-switch** | **−3%** | 🔴 Sab band, din khatam |
| Max trades/day per bot | **10** | Overtrading rokna |
| Max orders/second | **<10** | SEBI requirement |
| Max order value | Set karna hoga | Fat-finger guard |

### Account

| Limit | Default | Action |
|---|---|---|
| **Max drawdown halt** | **−10%** | 🔴 Band jab tak manual restart na ho |
| Consecutive losses | **5** | 🟠 Size aadha |
| Weekly loss | **−6%** | 🟠 Hafte ke liye size aadha |

---

## Filters — trade lene se pehle

| Filter | Rule |
|---|---|
| **Liquidity** | Min avg daily volume. Illiquid = skip |
| **Spread** | Bid-ask spread > X% → skip (cost kha jayega) |
| **Circuit** | Upper/lower circuit par → skip |
| **Event blackout** | Results se 2 din pehle → naya entry nahi |
| **Market hours** | Sirf 09:15–15:30 IST ([clock.py](../backend/app/clock.py)) |
| **First/last 15 min** | Optional — volatile, spread chaudi |
| **Stale data** | Tick 5 second se purana → trade nahi |
| **Regime** | Choppy regime → trend strategies off |
| **Expected edge** | `edge <= cost` → trade nahi |

### Edge calculation

```
expected_move  =  model_probability × average_move
cost           =  brokerage + STT + exchange + GST + stamp + slippage
edge           =  expected_move − cost

if edge <= 0:  trade mat karo
```

Backtester me ye already hai: brokerage ₹20/order, slippage 0.03%, taxes 0.12%.

---

## Position sizing

```
risk_per_trade  =  capital × 0.01              # 1%
stop_distance   =  entry_price − stop_price
quantity        =  risk_per_trade / stop_distance

# volatility-targeted alternative (behtar):
quantity        =  (capital × 0.01) / (ATR × multiplier)
```

**Kabhi fixed quantity mat use karna.** Volatile stock me 100 shares aur calm
stock me 100 shares — bilkul alag risk hai.

### Agent ka asar sirf ek taraf

```python
size = base_size

if any(agent.risk_flag for agent in agents):
    size *= 0.5                     # ✅ allowed

if agents_disagree(chief_strategist, red_team):
    size *= 0.5                     # ✅ allowed

# size *= 2  ← ❌ aisa kuch kabhi nahi
size = min(size, HARD_MAX)          # hamesha aakhir me
```

> **Agent risk ghata sakta hai, badha nahi.** Isse prompt injection ka worst
> case "trade nahi hua" ban jaata hai, "paisa gaya" nahi.

---

## BTST ka special risk

**Overnight stop-loss kaam nahi karta.** Gap seedha aapke stop ke neeche khul
sakta hai.

| Rule | Value |
|---|---|
| Position size | Aise rakho jaise **−5% gap** aa hi gaya ho |
| Max BTST positions | 2 |
| BTST capital allocation | Max 20% |
| News check | Shaam ko dobara — bura news aaya to next-day exit plan |

---

## Options ka special risk

SEBI data dekh chuke ho. Isliye:

| Rule | |
|---|---|
| **Naked option buying** | ❌ Shuru me bilkul nahi |
| **Defined-risk spreads only** | ✅ Bull call spread, bear put spread |
| **Option selling** | ❌ Expert stage tak nahi |
| Max options allocation | 10% capital |
| Theta awareness | Expiry ke 2 din pehle no new buy |

---

## Kill switches

### Automatic

| Trigger | Action |
|---|---|
| Daily loss −3% | 🔴 Saare bots stop, naya order nahi |
| Drawdown −10% | 🔴 System halt, manual restart chahiye |
| Position mismatch (broker vs hamara) | 🔴 Halt, reconcile, phir resume |
| Upstox 401 / session expire | 🔴 Bot stop, login maango |
| Order reject rate > 20% | 🟠 Pause, investigate |
| Stale feed > 30 second | 🟠 Naya entry nahi, exits allowed |
| Agent unavailable | 🟡 Us agent ko neutral maano, size thoda kam |

### Manual

Ek command / ek button:
```
STOP ALL  →  saare bots band
          →  naye orders band
          →  open positions chhedi nahi jayengi (wo aapka faisla)
```

> **Open position apne aap close nahi hogi.** Kill switch naye trades rokta hai.
> Position band karna aapka manual faisla hai — kyunki automatic panic-exit
> aksar sabse bure price par hota hai.

---

## Market hours safety

| Time (IST) | Rule |
|---|---|
| 09:15–09:30 | Optional: naya entry nahi (volatile, chaudi spread) |
| 09:30–15:00 | Normal trading |
| **15:15** | Intraday positions **square off** |
| 15:15–15:30 | Sirf exits |
| 15:30+ | Market band — bot sota hai |
| Sat / Sun | Kuch nahi |

Implementation: [`backend/app/clock.py`](../backend/app/clock.py) — fixed IST
offset use karta hai, machine ke local time par bharosa nahi karta.

---

## Config — par enforcement code me

```python
# config se aate hain
RISK = {
    "max_risk_per_trade_pct": 1.0,
    "max_positions": 5,
    "daily_loss_halt_pct": 3.0,
    "max_drawdown_halt_pct": 10.0,
    "max_sector_exposure_pct": 30.0,
    ...
}

# par ye code me hain, config me nahi
ABSOLUTE_MAX_RISK_PER_TRADE = 2.0      # config isse upar nahi ja sakti
ABSOLUTE_MAX_POSITIONS = 10
ABSOLUTE_DAILY_LOSS_HALT = 5.0
```

Config me koi galat value daal de (ya bug se badal jaye), to bhi system in
absolute limits se bahar nahi jayega. Aur agar config absolute se bahar ho,
system **start hi nahi hoga**.

---

## Live jaane se pehle

- [ ] Saare limits config me set hain
- [ ] Daily loss kill-switch **test kiya hua** hai (artificially trigger karke)
- [ ] Drawdown halt test kiya hua hai
- [ ] Manual kill switch test kiya hua hai
- [ ] Reconciliation chal raha hai
- [ ] Position sizing volatility-based hai, fixed nahi
- [ ] Har position par stop-loss lag raha hai
- [ ] Event blackout list update ho rahi hai
- [ ] **2–4 hafte paper mode** chala hai
- [ ] Paper results backtest se match karte hain (bahut alag = strategy live me kaam nahi karegi)
- [ ] Chhoti capital se shuru — poori nahi
