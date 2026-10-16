# Agent 12 — Industry & Stock Selection

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 15 min, market hours + ek pre-open run |
| **Calls/day** | ~30 |
| **Sources** | 8 |

---

## Kaam

Do sawaalon ka jawab, isi order me:

1. **Kaunsi industry chalegi?** — news, global cues aur relative strength se
2. **Us industry me kaunsa stock?** — leader kaun hai, laggard kaun

Agent #8 (Sector Rotation) index-level sectors dekhta hai. Ye ek level neeche
jaata hai: **industry → individual stock**, aur global linkage ko explicit
banata hai.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | `ind_nifty500list.csv` | 500 stocks, har ek par **Industry** label | daily |
| 2 | NSE sector indices | NIFTY IT, BANK, AUTO, PHARMA, FMCG, METAL... | real-time |
| 3 | Upstox feed | Har stock ka price, volume, depth | real-time |
| 4 | Events store | Industry-wise filings aur news | 1-2 min |
| 5 | Asia markets | Korea/Taiwan semis → hamara IT | 15 min |
| 6 | Commodity | Crude → OMC/paints/chemicals, metals → steel | 15 min |
| 7 | Currency | USDINR → IT/pharma fayda, importers nuksan | 15 min |
| 8 | Factor ETFs | MOVALUE / MOQUALITY / MOLOWVOL | real-time |

NSE ki constituent CSVs me **Industry column hota hai** — Financial Services,
Information Technology, Healthcare, Metals & Mining, Capital Goods, aur aage.
Matlab industry mapping banani nahi padti, exchange se hi aati hai.

---

## Global → industry linkage

Yahi iska asli kaam hai. Ye mapping pehle se maloom hai, discover karne ki
zarurat nahi:

| Global signal | Kaunsi industry | Direction |
|---|---|---|
| Korea/Taiwan semis ↑ | Information Technology | ✅ |
| Crude ↑ | Oil marketing, Aviation, Paints, Chemicals | ❌ |
| Crude ↑ | Oil exploration (ONGC, Oil India) | ✅ |
| USDINR ↑ (rupee weak) | IT, Pharma (export) | ✅ |
| USDINR ↑ | Importers, Aviation, Capital Goods | ❌ |
| US 10Y ↑ | Financial Services, Realty | ❌ |
| Gold ↑ | Jewellery (Titan, Kalyan) | mixed |
| China stimulus | Metals & Mining | ✅ |
| Shanghai metals ↑ | Steel, Aluminium | ✅ |
| Monsoon achha | FMCG, Auto (rural), Fertilisers | ✅ |

Agent ka kaam ye mapping **lagana aur verify karna** hai — sirf maan lena
nahi. Agar crude 3% chadha par OMC stocks nahi gire, to ya to move pehle hi
price me tha, ya kuch aur chal raha hai. Dono cases me confidence girni
chahiye.

---

## Industry se stock tak

Industry sahi pakadna aadha kaam hai. Usme kaunsa stock lena hai — ye alag
sawal hai:

| Criteria | Kyun |
|---|---|
| **Relative strength** | Industry ke andar jo pehle se lead kar raha hai |
| **Volume confirmation** | Move ke saath volume aaya? Warna wo shor hai |
| **Liquidity** | Spread chaudi ho to edge cost me chala jayega |
| **News clean hai?** | Blackout list me to nahi |
| **Index weight** | Heavyweight ka move index ko bhi hilata hai |

---

## Output schema

```json
{
  "agent": "industry",
  "ts": "2026-10-16T11:30:00+05:30",
  "view": "bullish",
  "confidence": 0.58,
  "horizon": "intraday",
  "industries_favoured": [
    {
      "industry": "Information Technology",
      "driver": "Korea semis +2.1%, rupee weak 0.4%",
      "strength": 0.72,
      "candidates": [
        {"symbol": "INFY", "rel_strength": 1.8, "volume_ratio": 1.4, "news_clean": true},
        {"symbol": "TCS",  "rel_strength": 1.2, "volume_ratio": 0.9, "news_clean": true}
      ]
    }
  ],
  "industries_avoided": [
    {"industry": "Oil Marketing", "driver": "crude +3.2%", "strength": -0.65}
  ],
  "linkage_confirmed": true,
  "sectors_positive": ["IT"],
  "sectors_negative": ["OMC"],
  "risk_flag": false,
  "reasoning": "...",
  "sources_used": ["asia_markets", "currency", "nifty500_list", "feed"]
}
```

`linkage_confirmed: false` ka matlab hai ki expected global→industry reaction
nahi dikhi — tab risk engine size kam karta hai.

---

## Kyun ye agent zaroori hai

Index-level sector rotation batata hai ki **paisa kahan ja raha hai**. Ye
batata hai **kyun**, aur **kis stock me**.

Teen cheezein jo sirf yahan milti hain:

**1. Global cause, local effect** — "IT upar hai" aur "IT upar hai kyunki
Korea me semis chale aur rupee kamzor hua" — doosri statement par aap position
size badha sakte ho, pehli par nahi. Cause pata ho to pata chalta hai ki move
kab tak chalega.

**2. Linkage verification** — jab expected reaction **nahi** aati, wo khud ek
signal hai. Crude chadha par OMC nahi gira = ya to already priced in hai, ya
koi aur force kaam kar rahi hai. Dono me confidence kam honi chahiye.

**3. Stock selection** — industry sahi par stock galat chunna aam galti hai.
Relative strength + volume confirmation + liquidity + news-clean, chaaron ek
saath.

---

## Failure mode

Global sources (Asia, commodity, currency) na milen to agent sirf relative
strength par chalega — kaam karega, par `linkage_confirmed: false` aur
confidence kam.

Nifty 500 list cache hoti hai, to NSE down hone par purani list chalti rahegi.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Industry mapping exchange CSV se aati hai, price hamari feed se. News ka
sirf **blackout flag** padha jaata hai (boolean), uska text nahi — to news
wale raaste se bhi koi free text model tak nahi pahunchta.

Industry names ko known list ke against validate kiya jayega; unknown industry
aaye to reject.

---

## Kaise measure hoga

| Metric | Kya |
|---|---|
| Industry hit rate | Favoured industry ne agle 1-5 din me outperform kiya? |
| Stock selection | Chune gaye stock ne apni hi industry ko beat kiya? |
| Linkage accuracy | Jab `linkage_confirmed: true` tha, tab prediction behtar thi? |
| Value-add | Is agent ko hata dein to backtest kitna bigadta hai |

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
