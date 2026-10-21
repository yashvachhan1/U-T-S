# 12 — Paper Run: shuru se chalu tak

Phase 7 chalane ke liye step-by-step. Har step ke baad ek command hai jo
batati hai ki wo step sach me hua ya nahi.

```bash
python tools/preflight.py
```

Ye kuch badalta nahi, sirf padhta hai — aur batata hai ki **kya baaki hai**
aur usko theek karne ki exact line kya hai. Har step ke baad ise dobara
chala lein.

> **Pehle ye samajh lein:** ye setup ek din ka hai. Uske baad **2–3 mahine
> chalana** hai. Wo hissa code se nahi aata.

---

## Step 0 — Install (ek baar)

```bash
setup.bat
cd frontend && npm install && npm run build
```

Phir `.env` banayein:

```bash
copy .env.example .env
```

`.env.example` me har value ke upar comment hai ki wo kya karti hai. Jo
samajh na aaye, usko default par chhod dein — defaults jaan-boojhkar
conservative hain.

**Check:** `python tools/preflight.py` → section 1 saaf hona chahiye.

---

## Step 1 — Safety pehle

`.env` me teen cheezein:

```ini
PAPER_TRADING=true
TRADING_CAPITAL=1000000
HOST=127.0.0.1
```

### `PAPER_TRADING=true`

Forward test ka pura point yahi hai. `false` karne ka mann tab hoga jab
paper results achhe aayenge — **wahi waqt hai jab nahi karna**, kyunki do
hafte ka achha paper result sirf do hafte ka achha paper result hai.

### `TRADING_CAPITAL`

Risk engine **har position isi ke against size karta hai**. Set kiye bina
wo size karne se **mana kar dega** — guess nahi karega, kyunki guess karne
ka matlab hota aapki risk appetite khud maan lena.

Jitni capital se aap asal me test karna chahte hain wahi daalein. Paper me
bhi. 10 lakh ka paper result 1 lakh ke live run ke baare me kuch nahi
batata.

### `HOST=127.0.0.1`

**App me koi authentication nahi hai.** Jo bhi port 8000 tak pahunch sakta
hai wo aapke orders bhej sakta hai. VPS par chalana ho to bahar expose mat
karein — SSH tunnel se connect karein:

```bash
ssh -L 8000:127.0.0.1:8000 user@your-vps
```

**Check:** preflight section 2.

---

## Step 2 — Groq key (agents ke liye)

1. https://console.groq.com — free account
2. API key banayein
3. `.env` me:

```ini
GROQ_API_KEY=gsk_...
```

### Ek key kaafi hai

14 agents hain par sab ek saath nahi chalte. Hisaab:

| | |
|---|---|
| Fast agents (12) | har 15–30 min, market hours me |
| Strong agents (2) | har 30 min |
| Total | ~840 calls/day = **0.6 request/minute** |

Free tier isse kaafi upar hai.

### Kai keys chahiyein to

```ini
GROQ_API_KEYS=key1,key2,key3
```

Pool automatically rotate karta hai, aur rate-limited key ko thodi der ke
liye **park** kar deta hai — ek agent ka rate limit doosre ko nahi chhuyega.

### Bina key ke kya hoga

System **chalega**. Har agent har baar `neutral` dega, `size_multiplier`
0.5 par rahega (kyunki Red Team kabhi challenge nahi karega), aur trades
ML + rules se honge.

Matlab: forward test chalega, par **agent layer ka koi data nahi banega**.
Phase 7 ka ek pura sawaal — "kaunsa agent kaam ka hai" — bina jawab reh
jayega.

**Check:** `python tools/preflight.py --network` → Groq reachable.

---

## Step 3 — FRED key (macro ke liye)

1. https://fredaccount.stlouisfed.org/apikeys — free
2. `.env` me `FRED_API_KEY=...`

Ek key se crude, dollar index, US 10Y yield, VIX, India EPU — sab aate
hain. Iske bina **4 agents aadhe andhe** ho jaate hain (US Markets, Global
Macro, Commodity, Currency).

Blocking nahi hai, par 2 minute ka kaam hai.

---

## Step 4 — Upstox app aur login

### App banana (ek baar)

1. https://account.upstox.com/developer/apps
2. New app
3. **Redirect URL** me bilkul yahi daalein:
   ```
   http://127.0.0.1:8000/api/auth/callback
   ```
   Ek character ka fark bhi login tod dega.
4. API key aur secret `.env` me:

```ini
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URI=http://127.0.0.1:8000/api/auth/callback
```

> **Ek user par ek hi active API app** ho sakta hai. Pehle se koi app hai to
> usi ki key use karein.

### Login (har din)

```bash
start.bat
```

Browser me http://127.0.0.1:8000 → upar right me **Upstox Login**.

**Upstox ka token roz subah expire hota hai.** Ye SEBI requirement hai,
workaround nahi hai. Matlab har trading din subah ek baar login karna
padega — isliye paper run ke liye roz 2 minute ka routine maan kar
chalein.

**Check:** preflight section 3.

---

## Step 5 — Mode chunein: system ya strategy

Forward test **do alag sawaal** ka jawab de sakta hai, aur dono ko mila dene
se report dono ke baare me galat ho jaati hai.

| Mode | Sawaal | Backtest se compare? |
|---|---|---|
| **system** | Machinery kaam karti hai? Roz chalti hai, order fill hote hain, har position par stop lagta hai, books match karte hain | ❌ skip |
| **strategy** | Ye strategy apne fitted data ke bahar bhi chalti hai? | ✅ yahi pura point hai |

```bash
curl -X POST http://127.0.0.1:8000/api/forward/mode ^
  -H "Content-Type: application/json" -d "{\"mode\":\"system\"}"
```

### Pehli baar? **system** chunein

Wajah seedhi hai: jab tak machinery sabit na ho, strategy ka result padhne
ka matlab hi nahi. Agar roz system crash ho raha hai, ya stop lag hi nahi
raha, to P&L kisi strategy ke baare me kuch nahi batata.

Aur ek baar machinery par bharosa ho gaya, to **agli strategy usi machinery
par chadh jayegi** — wo kaam dobara nahi karna padega.

System mode me checklist alag cheezein dekhti hai:

| Check | Kya poochta hai |
|---|---|
| `loop_ran_every_session` | Loop sach me roz chala, ya ek baar start karke bhool gaye? |
| `system_actually_traded` | Zero trades matlab order path kabhi chala hi nahi — kuch sabit nahi hua |
| `no_orders_left_hanging` | Koi order dino se live padi to poll usse dekhna band kar chuka hai |
| `every_position_had_a_stop` | Hamesha khali rehna chahiye |
| `books_reconciled` | Broker se kabhi mismatch hua? |
| `agents_producing_views` | Agents chal rahe hain (coverage, accuracy nahi) |

> **Dhyan dein:** system mode me bhi P&L record hota hai — bas usse **judge
> nahi** kiya jaata. Paisa kaunsi strategy banati hai, wo agla phase hai.

---

## Step 6 — Backtest baseline (sirf strategy mode me)

Paper run **kis se compare hoga**? Ye decide kiye bina forward test ka
aadha matlab chala jaata hai.

1. Dashboard → **Backtest** → jo strategy paper me chalani hai usko chalayein
2. Result note karein: return %, max drawdown %, win rate %, trades
3. Register:

```bash
curl -X POST http://127.0.0.1:8000/api/forward/baseline ^
  -H "Content-Type: application/json" ^
  -d "{\"label\":\"ema-crossover v1\",\"total_return_pct\":18.0,\"max_drawdown_pct\":6.0,\"win_rate_pct\":56.0,\"trades\":120,\"sessions\":60}"
```

### Sirf ek baseline rakha jaata hai — jaan-boojhkar

Kai rakhne par baad me **flattering wala chun lene** ka raasta khul jaata
hai. Wahi to wo failure hai jiske liye ye comparison banaya gaya hai.

Isliye soch kar chunein: wahi backtest jo aap sach me chalane wale hain,
wahi parameters, wahi period.

**Check:** preflight section 5.

---

## Step 7 — Chalu karein

```bash
start.bat
```

Phir dashboard par:

| Kya | Kahan | Kya hota hai |
|---|---|---|
| 1. Upstox Login | upar right | token aata hai |
| 2. Feed start | Chart & Trade, ya `POST /api/feed/start` | 532 instruments subscribe, constituent lists refresh |
| 3. Loop start | **Execution** page → "Chalu karo" | scan → poll → stop → square-off → reconcile → sweep, har 15 second |

Loop chalte hi forward journal apne aap likhna shuru kar deta hai.

**Check:** Execution page par "Loop: Chalu" aur cycles count badhta hua.

---

## Step 8 — Scanner chalu karein

> **Ye step chhoda to paper run do mahine chalega aur zero trade karega.**

Loop khud se koi signal nahi dhoondta. Scanner wo hissa hai jo watchlist
par strategy chalata hai aur signal ko decision engine tak pahunchata hai.

### Pehle watchlist

Dashboard → **Chart & Trade** → jo instruments test karne hain unhe
watchlist me add karein. 10–25 se zyada mat rakhein.

### Phir scanner configure karein

```bash
curl -X POST "http://127.0.0.1:8000/api/scanner/config" ^
  -H "Content-Type: application/json" ^
  -d "{\"strategy\":\"ema_crossover\",\"interval\":\"5m\",\"enabled\":true}"
```

Ab wo signal **dhoondhega par bhejega nahi**. Ek-do din aise chalayein aur
dekhein ki kya mil raha hai:

```bash
curl http://127.0.0.1:8000/api/scanner/status
curl -X POST http://127.0.0.1:8000/api/scanner/run     # ek baar haath se
```

### Jab bharosa ho jaye, tab execute

```bash
curl -X POST "http://127.0.0.1:8000/api/scanner/config" ^
  -H "Content-Type: application/json" -d "{\"auto_execute\":true}"
```

### `enabled` aur `auto_execute` alag kyun hain

Signal **dhoondna** aur us par **act karna** do alag faisle hain. Beech ka
gap wahi jagah hai jahan aap dekh sakte hain ki strategy kya nikal rahi
hai, **isse pehle ki wo paisa lagaye**.

### Scanner restart par band ho jaata hai — jaan-boojhkar

Config disk par save nahi hoti. Jo scanner restart jhel jaye, wo ek din
aise shuru ho jayega kyunki **machine reboot hui thi**. Roz subah ek line
chalana isse behtar hai.

### Scanner risk engine ko bypass nahi kar sakta

Har signal `decide()` se guzarta hai — sizing, exposure limits, edge test,
blackout, kill switch. Scanner **order place karta hi nahi**; wo proposal
banata hai aur risk engine faisla karta hai.

> **Algo Bots bhi ab isi raaste se jaate hain.** Pehle wo seedha order
> bhejte the; ab bot propose karta hai aur risk engine faisla karta hai.
> Do cheezein isme badli hain: **stop-loss % set kiye bina bot start nahi
> hoga**, aur bot ko **feed chahiye** (live tick ke bina entry reject hogi).
> Scanner aur bots dono theek hain — scanner watchlist par chalta hai, bot
> ek instrument par.

---

## Roz ka routine (2 minute)

| Kab | Kya |
|---|---|
| 09:00 | `start.bat`, Upstox login, feed start, loop start, **scanner enable** |
| 09:15 | **Agents** page — size multiplier aur stance dekh lein |
| Din me | kuch nahi. Ye point hai. |
| 15:30 | **Execution** page — koi unprotected position to nahi? |

### Kya **turant** dekhna hai

Teen cheezein aisi hain jo dikhte hi rokna chahiye:

| Dikhe | Matlab |
|---|---|
| **Execution page par laal banner: "position bina stop ke"** | Sabse mehngi state. System halt ho chuka hoga. |
| **"Books match nahi kar rahe"** | Har risk limit galat book par chal rahi hai. |
| **Risk page HALTED aur wajah samajh na aaye** | Kyun hua, ye samjhe bina reset mat karein. |

Baaki sab weekly report me dekh lenge.

---

## Hafte me ek baar

```bash
python tools/forward_report.py
```

Ye batata hai:

- paper vs backtest — **gap ka naam**, score nahi
- har agent ka value-add — `adds_value` / `costs_money` / `silent`
- go-live checklist ka wo aadha hissa jo system khud check kar sakta hai

### Shuruat me zyadatar "not enough evidence" aayega

Ye theek hai. 20 trades se kam par **koi edge number nikalta hi nahi**,
kyunki caveat ke saath diya gaya number quote karte waqt caveat chhod deta
hai. 6–8 hafte lagenge pehle asli numbers aane me.

Report ka format abhi dekhna ho to:

```bash
python tools/forward_report.py --demo
```

60 banaye hue sessions par chalta hai. Usme problems jaan-boojhkar daale
gaye hain, taaki dikhe ki report unhe pakadti hai.

---

## Kill switch test kar lein (ek baar)

```bash
python tools/killswitch_drill.py
```

Scratch database par har switch jaan-boojhkar trigger karta hai — 13 check.
`docs/07-risk-limits.md` ki go-live list me ye item hai, aur **test kiye
bina us item ko tick karna hi sabse badi galti hogi**.

Execution path bhi:

```bash
python tools/execution_drill.py
```

---

## Kab khatam

Jab ye sab sach ho:

| | |
|---|---|
| **40+ sessions** | ~2 mahine. Kam me kuch sabit nahi hota. |
| **Uptime 95%+** | Strategy chalna aur process chalna do alag daave hain. |
| **Backtest se match** | Ya har gap ka jawab hai. "Shayad theek ho jayega" jawab nahi hai. |
| **Koi agent `costs_money` nahi** | Jo hai use hata dein. |
| **Har position par stop laga tha** | Audit log me check hota hai. |
| **Books hamesha match hue** | Wahi. |

`python tools/forward_report.py` ye sab khud check karta hai.

> **Report kabhi "live jao" nahi kehti.** Jo system apna hi paper khud check
> kare wo saboot nahi hota. Jo cheezein system check nahi kar sakta — chhoti
> capital se shuru karna, static IP, broker side ke alerts — wo report ke
> aakhir me alag list me hain.

---

## Aksar yahi galtiyan hoti hain

| Dikhta hai | Asal wajah |
|---|---|
| Agents hamesha neutral | Groq key nahi hai, ya market band hai |
| "not subscribed" har line par | Feed start nahi kiya, ya login expire |
| Sab kuch 0 | `TRADING_CAPITAL` set nahi — engine size karne se mana kar raha hai |
| Loop chal raha par kuch nahi hota | Market band. Loop out of hours 2 min par chalta hai. |
| Login kal kaam kar raha tha, aaj nahi | Token roz expire hota hai. Dobara login. |
| Trades ho hi nahi rahe | Pehle `GET /api/scanner/status` dekhein — `enabled` false to kuch nahi hoga. Uske baad: normal, edge test zyadatar signals reject karta hai. |
| Scanner kal chalu tha, aaj band | Config restart par nahi bachti. Jaan-boojhkar. Roz enable karein. |

---

[← Testing](10-testing.md) · [Roadmap](08-roadmap.md) · [Risk limits](07-risk-limits.md)
