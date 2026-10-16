# Agent 11 — Index Composition

| | |
|---|---|
| **Layer** | Analyst |
| **Model** | fast |
| **Frequency** | Har 5 min, market hours |
| **Calls/day** | ~75 |
| **Sources** | 7 |

---

## Kaam

Nifty 50 aur Bank Nifty ko ek number ki tarah nahi, **apne 50 constituents ke
weighted sum** ki tarah padhna.

Index khud ek derived cheez hai. Agar hum har constituent ka live price,
volume aur OI dekh rahe hain, to index ka move **banne se pehle** dikh sakta
hai — aur zyada important, ye dikhta hai ki wo move **kitna broad** hai.

---

## Sources

| # | Source | Kya | Latency |
|---|---|---|---|
| 1 | `ind_nifty50list.csv` | 50 constituents + **Industry** | daily |
| 2 | `ind_niftybanklist.csv` | Bank Nifty constituents | daily |
| 3 | Upstox WebSocket | Har constituent ka live price + depth | real-time |
| 4 | Upstox WebSocket | NIFTY / BANKNIFTY index level | real-time |
| 5 | NSE_FO option chain | Har stock ka OI change | 3 min |
| 6 | Index futures | Basis (premium/discount) | real-time |
| 7 | Events store | Har constituent par filings | 1-2 min |

Constituent lists verified working:
`nsearchives.nseindia.com/content/indices/ind_<index>list.csv` — Nifty 50,
Bank Nifty, IT, Auto, Pharma, Nifty 500, sab mil jaate hain, aur har row me
**Industry** column hota hai.

---

## Weightage — publish nahi hota, par zaroorat bhi nahi

NSE constituent list deta hai, weights nahi. Teen tarike, aasaan se mushkil:

### 1. Breadth — weight chahiye hi nahi

```
50 me se kitne up hain?  Kitne down?
```

Ye sabse robust signal hai aur isme koi weight nahi lagta. **"Index +0.5% par 40
stocks neeche"** ka matlab hai ki 4-5 heavyweights akele rally utha rahe hain —
aisa rally tootna aasaan hota hai.

### 2. Equal-weight vs cap-weight divergence

Index ka move vs **median stock** ka move. Gap jitna bada, rally utni hi narrow.

### 3. Weights estimate karna (least squares)

```
index_return(t)  ≈  Σ weight_i × stock_return_i(t)
```

60 din ke daily returns par least squares solve karo → effective weights mil
jaate hain. Iska ek bada fayda hai: **NSE rebalance kare to ye khud adjust ho
jaata hai**, kyunki weights data se aate hain, hardcoded nahi.

Validation: estimated weights se bana synthetic index asli index se match hona
chahiye. Match toot jaye → rebalance hua hai, ya hamara data kharab hai.

---

## Output schema

```json
{
  "agent": "index_composition",
  "ts": "2026-10-16T13:45:00+05:30",
  "view": "bearish",
  "confidence": 0.62,
  "horizon": "intraday",
  "index": "NIFTY 50",
  "index_change_pct": 0.42,
  "advancing": 11,
  "declining": 39,
  "breadth_ratio": 0.22,
  "median_stock_change_pct": -0.31,
  "breadth_divergence": true,
  "top_contributors": [
    {"symbol": "HDFCBANK", "contribution_pct": 0.28},
    {"symbol": "RELIANCE", "contribution_pct": 0.19}
  ],
  "top_detractors": [{"symbol": "INFY", "contribution_pct": -0.11}],
  "concentration": 0.71,
  "futures_basis_pct": -0.08,
  "constituents_with_news": ["DLF", "LT"],
  "sectors_positive": [],
  "sectors_negative": ["IT"],
  "risk_flag": true,
  "reasoning": "...",
  "sources_used": ["nifty50_list", "feed", "option_chain"]
}
```

Pydantic se validate hoga. Malformed output = reject, agent "unavailable".

---

## Kyun ye agent zaroori hai

**Index ka number jhooth nahi bolta, par poora sach bhi nahi batata.**

| Index dikhata hai | Ye agent dikhata hai |
|---|---|
| Nifty +0.42% | 39 stocks neeche, sirf 11 upar |
| "Market up hai" | 2 stocks 70% move utha rahe hain |
| Ek level | Kaunsa sector kheench raha hai, kaunsa gira raha |

### Teen cheezein jo sirf yahan milti hain

**1. Breadth divergence** — index upar par breadth kharab. Research me ye
documented factor hai: narrow rallies reverse hone ki sambhavna zyada hoti hai.

**2. Contribution attribution** — "Nifty 100 point upar hai" ka matlab bahut
alag hai agar wo 100 point ek hi stock se aa rahe hon bajaye 50 stocks se.
`concentration` field yahi batata hai.

**3. Futures basis** — index futures spot se premium par hain ya discount par.
Ye positioning ka direct read hai, aur spot-futures divergence index ke apne
move se pehle dikhta hai.

### Constituent news ka index par asar

Ek heavyweight par bura filing poore index ko hila sakta hai. Agent dekhta hai
ki **kaunse constituents par aaj news hai aur unka weight kitna hai** — HDFCBANK
par news Nifty ke liye matter karti hai, ek 0.3% weight wale stock par nahi.

---

## Failure mode

Constituent list roz ek baar fetch hoti hai aur cache hoti hai, to NSE down
hone par purani list chalti rehti hai (membership roz nahi badalti).

Live feed na ho to agent kuch nahi bol sakta — tab `view: neutral`,
`confidence: 0`, aur ek `data_stale` flag.

> Koi bhi agent fail ho to **system rukta nahi**. Us agent ka view "neutral"
> maana jayega aur risk thoda kam kar diya jayega.

---

## Security

Sab kuch internal data hai — exchange CSV, hamari apni feed, hamara apna
events store. Koi free text model tak nahi jaata, to **prompt injection ka
raasta yahan hai hi nahi**.

Ek sanity check zaroori hai: synthetic index asli index se 2% se zyada diverge
kare to data problem maano, signal nahi. Tab agent `data_quality: suspect`
bolega aur confidence gira dega.

---

## Kaise measure hoga

| Metric | Kya |
|---|---|
| Breadth divergence hit rate | Jab narrow rally flag kiya, agle 1-3 din me reversal aaya? |
| Contribution accuracy | Estimated weights se bana index asli se kitna match karta hai (R²) |
| Lead time | Breadth turn index turn se kitna pehle dikha |
| Rebalance detection | NSE ne constituents badle to kitni jaldi pata chala |

---

[← Saare agents](README.md) · [Agent overview](../04-agents.md)
