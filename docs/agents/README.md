# Agents — Index

Har agent ki apni file. Overview aur API key ka hisaab:
[../04-agents.md](../04-agents.md)

## Analyst (12) — ✅ saare bane hue hain

| # | Agent | Sources | Calls/day | Model | Code |
|---|---|---|---|---|---|
| 01 | [India News](01-india-news.md) | 6 | ~260 | fast | `india_news.py` |
| 02 | [Asia Markets](02-asia-markets.md) | 7 | ~32 | fast | `asia_markets.py` |
| 03 | [US Markets & Fed](03-us-markets.md) | 8 | ~70 | fast | `us_markets.py` |
| 04 | [Global Macro](04-global-macro.md) | 5 | ~48 | fast | `global_macro.py` |
| 05 | [Commodity](05-commodity.md) | 5 | ~58 | fast | `commodity.py` |
| 06 | [Currency](06-currency.md) | 5 | ~32 | fast | `currency.py` |
| 07 | [Crypto / Risk Appetite](07-crypto-risk.md) | 3 | ~96 | fast | `crypto_risk.py` |
| 08 | [Sector Rotation](08-sector-rotation.md) | 4 | ~25 | fast | `sector_rotation.py` |
| 09 | [Earnings](09-earnings.md) | 4 | ~20 | fast | `earnings.py` |
| 10 | [Power & Policy Intelligence](10-power-policy.md) | 9 | ~50 | fast | `power_policy.py` |
| 11 | [Index Composition](11-index-composition.md) | 7 | ~75 | fast | `index_composition.py` |
| 12 | [Industry & Stock Selection](12-industry.md) | 8 | ~30 | fast | `industry.py` |

## Synthesis (2)

| # | Agent | Sources | Calls/day | Model |
|---|---|---|---|---|
| 13 | [Chief Strategist](13-chief-strategist.md) | 4 | ~20 | strong |
| 14 | [Red Team (Devil's Advocate)](14-red-team.md) | 3 | ~20 | strong |

## Review (2)

| # | Agent | Sources | Calls/day | Model |
|---|---|---|---|---|
| 15 | [Post-Trade Review](15-post-trade-review.md) | 4 | 1 | strong |
| 16 | [Risk Auditor](16-risk-auditor.md) | 4 | 0.2 | strong |

---

**Total: 16 agents · 86 source connections** — 12 analysts ✅, 4 baaki

## Jo saare analysts pe laagu hota hai

| | |
|---|---|
| **Registry** | `backend/app/agents/__init__.py` — 13 instances (index agent Nifty aur Bank Nifty dono pe chalta hai) |
| **Shared helpers** | `analysts/_shared.py` — prompt formatting, instrument resolution, peer views |
| **Agent ek doosre ko call nahi karte** | Doosre ki raay store se padhte hain (`peer_view`), taaki backtest wahi raay replay kare jo us waqt thi |
| **Number Python compute karta hai** | Breadth, industry median, advance ratio — model se sirf matlab poochha jaata hai |
| **Missing data chhupti nahi** | "not subscribed" / "unavailable" prompt me likha jaata hai, 0.00 nahi |
| **Subscription check** | `GET /api/agents/instruments` — 532 instruments chahiye, kitne mil rahe hain |
| **Reference data** | `collectors/constituents.py` — NSE constituent CSVs (symbol, Industry, ISIN) |

Har agent ka output timestamp ke saath save hota hai, taaki backtest
LLM ko dobara call kiye bina saved outputs replay kar sake.
