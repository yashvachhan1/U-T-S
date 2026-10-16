# Trading Desk — Upstox se juda hua trading software

**Backend:** Python + FastAPI  ·  **Frontend:** React + Vite  ·  **Broker:** Upstox API v2/v3

Local web app jisme chaar cheezein hain:

| Feature | Kya karta hai |
|---|---|
| **Chart & Trade** | Live candlestick chart (1m se 1d tak), watchlist, aur ek panel se BUY/SELL order |
| **Portfolio** | Positions, holdings, day P&L, funds |
| **Backtest** | Kisi bhi strategy ko purane data par test karein — equity curve, win rate, drawdown, har trade ki list |
| **Algo Bots** | Strategy ko live chalayein — bot khud signal dekhkar order dalta hai |

5 built-in strategies: EMA Crossover, RSI Reversal, Supertrend, MACD, Bollinger Bounce.
Light aur dark dono theme hain (top-right ka sun/moon icon).

UI me ye bhi hai:

- **Command palette** — `Ctrl+K` dabayein, kahin se bhi stock dhoondein ya page badlein
- **Live price flash** — price badalne par green/red pulse, watchlist me mini sparkline
- **Backtest par trade markers** — har entry/exit price chart par arrow ke roop me
- **Portfolio allocation bar** — kis stock me kitna paisa laga hai
- **Skeleton loaders** — data aane tak khaali panel nahi dikhta

### Keyboard shortcuts

| Key | Kya karta hai |
|---|---|
| `Ctrl` + `K` | Command palette kholein |
| `Alt` + `1…6` | Seedha us page par jayein |
| `↑` `↓` | Palette me navigate |
| `Enter` | Palette me select |
| `Esc` | Palette / dialog band karein |

> **Default me `PAPER_TRADING=true` hai.** Is mode me koi bhi order Upstox tak nahi
> jaata — sab local database me record hota hai. Asli paise se trade karne ke liye
> hi isko `false` karein.

---

## 📚 Full system documentation

Poora blueprint `docs/` folder me hai — research, architecture, har agent ki
alag file, security aur performance:

| File | Kya |
|---|---|
| [docs/README.md](docs/README.md) | Index + system overview |
| [01-research.md](docs/01-research.md) | Market kin factors par chalta hai (evidence ke saath) |
| [02-data-sources.md](docs/02-data-sources.md) | Har data source: URL, frequency, cost |
| [03-architecture.md](docs/03-architecture.md) | Components, data flow, code structure |
| [04-agents.md](docs/04-agents.md) | 14 agents, API key ka hisaab, model routing |
| [05-security.md](docs/05-security.md) | Secrets, auth, prompt injection, order safety |
| [06-performance.md](docs/06-performance.md) | Latency budget |
| [07-risk-limits.md](docs/07-risk-limits.md) | Saare limits aur kill switches |
| [08-roadmap.md](docs/08-roadmap.md) | Phase-wise build plan |
| [09-development.md](docs/09-development.md) | Step-wise development process + code standards |
| [10-testing.md](docs/10-testing.md) | Testing — unit, backtest, paper, live |
| [11-database.md](docs/11-database.md) | Database stack — SQLite + Parquet/DuckDB |
| [docs/agents/](docs/agents/README.md) | Har agent ki detail file |

---

## 1. Install (sirf ek baar)

**`setup.bat`** par double-click karein. Ye:

1. Python virtual environment banata hai
2. Python libraries install karta hai
3. React frontend ka `npm install` + production build karta hai
4. `.env` file banakar Notepad me khol deta hai

Chahiye:

- **Python 3.11+** — [python.org](https://www.python.org/downloads/), install karte waqt
  **"Add Python to PATH"** zaroor tick karein
- **Node.js 18+** — [nodejs.org](https://nodejs.org/) (React build ke liye)

---

## 2. Upstox API app banana

Ye zaroori hai — iske bina app sirf UI dikhayega, data nahi aayega.

1. Upstox account me login karein
2. Kholein: **https://account.upstox.com/developer/apps**
3. **New App** dabayein
4. Form bharein:
   - **App name** — kuch bhi, jaise `My Trading Desk`
   - **Redirect URL** — bilkul yahi daalein (ek bhi character alag nahi):
     ```
     http://127.0.0.1:8000/api/auth/callback
     ```
   - **Postback URL** — khaali chhod dein
5. Save karein. Aapko **API Key** aur **API Secret** milega.
6. Project folder me `.env` file kholein (Notepad se) aur bharein:

```ini
UPSTOX_API_KEY=yahan_aapki_api_key
UPSTOX_API_SECRET=yahan_aapka_api_secret
UPSTOX_REDIRECT_URI=http://127.0.0.1:8000/api/auth/callback
PAPER_TRADING=true
```

Yahi steps app ke **Setup** tab me bhi hain, redirect URL copy button ke saath.

---

## 3. Chalana

**`start.bat`** par double-click karein. Browser khud khulega:
**http://127.0.0.1:8000**

Phir dashboard me:

1. Top-right ka **refresh** icon dabayein (pehli baar — NSE+BSE ke ~61,000 instruments download honge, 1–2 min)
2. **Upstox Login** dabayein → Upstox ka page khulega → approve karein
3. Search box me stock ka naam likhein (RELIANCE, NIFTY, TCS…) aur chunein

Server band karne ke liye black window me **Ctrl+C**.

---

## 4. Roz ka routine

Upstox ka access token **har raat 3:30 baje expire** ho jaata hai — ye Upstox ki
policy hai, is app ki kami nahi. Isliye har trading din:

1. `start.bat` chalayein
2. **Upstox Login** ek baar dabayein

Bas. Watchlist, theme aur baaki settings save rehti hain.

---

## 5. Backtest kaise karein

1. **Backtest** tab → instrument chunein
2. Strategy chunein, uske parameters adjust karein
3. Interval (1 day se shuru karein), lookback days, capital bharein
4. **Stop loss %** aur **Target %** set karein — 0 matlab band
5. **Backtest chalayein**

Dhyaan dein:

- **Buy & hold** number se compare karein — agar strategy usse kam de rahi hai
  to strategy ka koi fayda nahi
- Brokerage (₹20/order) aur slippage (0.03%) pehle se jude hue hain
- Signal **agle candle ke open par** fill hota hai, isliye result realistic hai
  (look-ahead bias nahi)
- **Max drawdown** sabse important number hai — itna paisa aap beech me kho sakte hain

---

## 6. Algo bot kaise chalayein

1. **Algo Bots** tab → instrument, strategy, interval, quantity chunein
2. Stop loss % aur target % set karein
3. **Bot start karein**

Bot me ye safety rails hamesha on rehte hain:

- `PAPER_TRADING=true` rehte hue koi order Upstox tak nahi jaata
- Ek time par ek hi position
- Daily trade limit (default 10)
- Sirf market hours (9:15 AM – 3:30 PM, Mon–Fri) me chalta hai
- Intraday position 3:15 PM par apne aap square off
- Koi bhi error aane par bot ruk jaata hai, blindly retry nahi karta
- Sirf **band ho chuki candle** par kaam karta hai — isse backtest aur live
  behaviour same rehta hai

Har bot ka live log dashboard me dikhta hai.

### Live mode me jaane se pehle

1. Kam se kam **2–4 hafte paper mode** me chalayein
2. Paper results apne backtest se match karein — agar bahut alag hain to
   strategy live me kaam nahi karegi
3. `.env` me `PAPER_TRADING=false` karein aur server restart karein
4. Chhoti quantity se shuru karein

---

## Folder structure

```
trading-app/
├── setup.bat            # pehli baar install (Python + React build)
├── start.bat            # roz server start
├── dev.bat              # sirf development: React hot-reload
├── .env                 # aapki API key (git me kabhi mat daalein)
├── backend/app/
│   ├── main.py          # FastAPI server, React build serve karta hai
│   ├── config.py        # .env se settings
│   ├── db.py            # SQLite (paper orders, bot runs, logs, watchlist)
│   ├── upstox/
│   │   ├── client.py        # OAuth + REST wrapper
│   │   ├── instruments.py   # instrument master download + search
│   │   ├── marketdata.py    # candles, quotes, LTP
│   │   └── trading.py       # orders, positions, holdings, funds
│   ├── engine/
│   │   ├── indicators.py    # EMA, RSI, ATR, MACD, Bollinger, Supertrend
│   │   ├── backtester.py    # event-driven backtest + stats
│   │   └── runner.py        # live bot loop
│   ├── strategies/
│   │   ├── base.py          # strategy contract + registry
│   │   └── builtin.py       # 5 strategies
│   └── routers/api.py       # saare HTTP endpoints
├── frontend/            # React + Vite
│   ├── src/
│   │   ├── App.jsx          # shell: topbar, sidebar, view switch
│   │   ├── api.js           # fetch wrapper + formatting helpers
│   │   ├── styles.css       # design system (light + dark tokens)
│   │   ├── components/      # Icon, ui, charts, Feedback, search, strategy
│   │   └── views/           # ChartTrade, OrdersPortfolio, Backtest, Bots, Setup
│   └── dist/                # build output (FastAPI yahi serve karta hai)
└── data/                # SQLite db, token, instrument cache (gitignored)
```

API docs khud ba khud milti hain: **http://127.0.0.1:8000/docs**

---

## Development (optional)

React me change karke turant dekhna ho to:

```bash
dev.bat
```

Ye API ko `:8000` par aur Vite dev server ko `:5173` par chalata hai (hot reload
ke saath). Browser me **http://localhost:5173** kholein.

Kaam poora hone ke baad build zaroor karein, warna `start.bat` purana version
dikhayega:

```bash
cd frontend
npm run build
```

---

## Nayi strategy kaise add karein

`backend/app/strategies/builtin.py` me ek function likhein jo candles lekar
har candle ke liye `"BUY"`, `"SELL"` ya `None` return kare — aur use register
kar dein. Wahi strategy backtest aur live bot dono me turant dikhne lagegi,
frontend me kuch badalne ki zaroorat nahi (parameters backend se aate hain).

```python
def _my_strategy(candles, p):
    closes = [c["close"] for c in candles]
    fast = ta.ema(closes, int(p["period"]))
    out = [None] * len(candles)
    for i in range(1, len(candles)):
        if fast[i] and closes[i] > fast[i] and closes[i-1] <= fast[i-1]:
            out[i] = "BUY"
        elif fast[i] and closes[i] < fast[i] and closes[i-1] >= fast[i-1]:
            out[i] = "SELL"
    return out

register(Strategy(
    key="my_strategy",
    name="Meri Strategy",
    description="Price EMA ko cross kare to trade.",
    params=[Param("period", "EMA Period", 20, min=2, max=200, step=1)],
    fn=_my_strategy,
))
```

---

## Common problems

| Problem | Ilaaj |
|---|---|
| "Frontend build nahi mila" | `setup.bat` dobara chalayein, ya `cd frontend && npm run build` |
| "Upstox me login nahi hai" | Header me **Upstox Login** dabayein |
| "Upstox session expire ho gaya" | Token roz 3:30 AM par marta hai — dobara login karein |
| Search me kuch nahi milta | Top-right ka **refresh** icon dabayein |
| Login par "redirect_uri mismatch" | Upstox app ka Redirect URL aur `.env` ka `UPSTOX_REDIRECT_URI` bilkul same hone chahiye |
| Chart khaali hai | Market band hone par intraday candles nahi aate — **1D** interval try karein |
| TLS / certificate error | `setup.bat` aur `start.bat` ye khud handle karte hain. Manually chala rahe hain to `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE` clear karein |
| Port 8000 busy | `.env` me `PORT` badlein — par tab Upstox app ka Redirect URL bhi badalna padega |
| npm nahi mila | nodejs.org se Node 18+ install karein |

---

## Disclaimer

Ye software educational aur personal use ke liye hai. Trading me paisa doobta
hai. Backtest ka result future ki guarantee nahi hai. Koi bhi strategy live
chalane se pehle usko achhe se samjhein aur paper mode me test karein. Is code
se hone wale kisi bhi nuksaan ki zimmedari user ki hai.
