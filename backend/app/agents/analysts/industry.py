"""Agent 12 — Industry & Stock Selection.

Agent 08 reads sectors at index level. This one goes a level down, to the
industry groups NSE itself labels, and then to individual names inside the
leading group.

Two things make it practical rather than speculative.

**The industry mapping is the exchange's.** NSE's constituent CSVs carry an
Industry column, so "which stocks are in Information Technology" is a lookup,
not a judgement we have to maintain and get wrong.

**The global links are known in advance.** Korea and Taiwan semiconductors
lead our IT. Crude up hurts oil marketing and helps exploration. A weaker
rupee helps exporters. None of that needs discovering; it needs applying
consistently. So the mapping goes into the prompt as given facts, and the
model's job is to say which link is actually live today -- not to invent
causal chains.

Per-industry performance is computed in Python from live constituent prices,
for the same reason as in the index agent: averaging 500 numbers is
arithmetic, and an LLM doing arithmetic silently is how a wrong industry ends
up looking like the leader.
"""
from __future__ import annotations

from statistics import median

from ...collectors import constituents
from ...engine import feed_service
from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import KNOWN_INDUSTRIES, IndustryOutput
from . import _shared as sh

SCHEMA_EXTRA = ('  "industries_positive": ["INFORMATION_TECHNOLOGY"],\n'
                '  "industries_negative": ["OIL_GAS_&_CONSUMABLE_FUELS"]')

LINKAGE = """Known global-to-industry links. These are established, so apply
them rather than reasoning from scratch -- your job is to say which one is
live today:
- Korea/Taiwan semiconductors up -> INFORMATION_TECHNOLOGY positive
- Crude up -> oil marketing, aviation, paints, chemicals negative;
  oil exploration positive
- Rupee weaker -> INFORMATION_TECHNOLOGY, HEALTHCARE (pharma exports)
  positive; importers and aviation negative
- US 10-year yield up -> FINANCIAL_SERVICES, REALTY negative
- China stimulus or Shanghai metals up -> METALS_&_MINING positive
- Power demand sustained higher -> POWER, CAPITAL_GOODS positive"""

TASK = f"""Answer two questions in order: which industry is moving, and which
stock inside it.

{LINKAGE}

How to judge:
- The industry table below is computed from live prices. An industry is only
  "leading" if it is clearly ahead of the others, not merely green on a green
  day.
- Prefer an industry whose move has a reason in the peer views or headlines
  below. An industry moving with no visible cause is more likely to be noise
  or a single large constituent.
- Inside a leading industry, the stock that leads is usually the one that
  moved first and on volume, not the one that moved most. A single name far
  ahead of its peers is often reacting to its own news, which is a different
  trade.
- Breadth within the industry matters: three of four stocks up is a sector
  move, one of four is a stock story.
- Use only the industry labels exactly as they appear in the table. Anything
  else is discarded.
- Name a stock in affected_symbols only if it appears below.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

UNIVERSE = "nifty500"

# How many constituents an industry needs before its average means anything.
MIN_MEMBERS_FOR_SIGNAL = 3

# Peer agents whose stored views explain *why* an industry is moving.
PEERS = ("asia_markets", "commodity", "currency", "us_markets",
         "sector_rotation")


def industry_performance(changes: dict[str, float], groups: dict[str, list[str]],
                         min_members: int = MIN_MEMBERS_FOR_SIGNAL
                         ) -> list[dict]:
    """Per-industry performance from constituent moves. Pure, so it is testable.

    Median rather than mean: one constituent limit-up on its own news would
    drag a mean enough to crown the wrong industry, and that is exactly the
    mistake this agent must not make.
    """
    rows = []
    for industry, symbols in groups.items():
        values = [changes[s] for s in symbols if s in changes]
        if len(values) < min_members:
            continue
        up = sum(1 for v in values if v > 0)
        rows.append({
            "industry": industry,
            "covered": len(values),
            "of": len(symbols),
            "median_pct": round(median(values), 2),
            "advancing": up,
            "advance_ratio": round(up / len(values), 2),
            "leader": max(((s, changes[s]) for s in symbols if s in changes),
                          key=lambda kv: kv[1])[0],
            "laggard": min(((s, changes[s]) for s in symbols if s in changes),
                           key=lambda kv: kv[1])[0],
        })
    return sorted(rows, key=lambda r: r["median_pct"], reverse=True)


class IndustryAgent(Agent):
    # Hundreds of constituents, and breadth needs only the last
    # price and the previous close. Full depth on five hundred
    # stocks would be most of the feed budget for data nothing
    # here reads.
    feed_mode = "ltpc"
    output_model = IndustryOutput

    def __init__(self, show_top: int = 6, stocks_per_industry: int = 6) -> None:
        super().__init__(AgentConfig(
            name="industry",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=800,
            freshness_seconds=1200,
        ))
        self.show_top = show_top
        self.stocks_per_industry = stocks_per_industry

    def instrument_keys(self) -> dict[str, str]:
        return constituents.instrument_keys(UNIVERSE)

    def performance(self) -> list[dict]:
        changes = constituents.live_changes(UNIVERSE, feed_service.latest)
        groups = constituents.by_industry(UNIVERSE)
        return industry_performance(changes, groups)

    async def gather(self) -> str:
        changes = constituents.live_changes(UNIVERSE, feed_service.latest)
        groups = constituents.by_industry(UNIVERSE)

        if not groups:
            return ("The Nifty 500 constituent list has not been downloaded "
                    "yet, so industry performance cannot be computed.")
        if not changes:
            return (f"{len(groups)} industries are mapped but no constituents "
                    f"are quoting on the live feed, so performance cannot be "
                    f"computed. The market may be closed.")

        rows = industry_performance(changes, groups)
        table = [
            f"{r['industry']}: median {sh.pct(r['median_pct'])}, "
            f"{r['advancing']}/{r['covered']} advancing "
            f"(of {r['of']} members), leader {r['leader']}, "
            f"laggard {r['laggard']}"
            for r in rows
        ]

        # Names inside the strongest and weakest industries, so the model can
        # pick a stock without being shown all five hundred.
        detail: list[str] = []
        for r in rows[:2] + rows[-2:]:
            members = [(s, changes[s]) for s in groups[r["industry"]] if s in changes]
            members.sort(key=lambda kv: kv[1], reverse=True)
            picks = members[:self.stocks_per_industry]
            detail.append(f"{r['industry']}: " + ", ".join(
                f"{s} {sh.pct(v)}" for s, v in picks))

        # Only the ends of the table carry information -- the middle is the
        # market, and showing it costs tokens for nothing.
        if len(table) > self.show_top * 2:
            shown = table[:self.show_top] + ["..."] + table[-self.show_top:]
        else:
            shown = table

        with_news = set(events.symbols_with_news(since_minutes=120))
        flagged = sorted(with_news.intersection(changes))

        return sh.join(
            sh.block(f"INDUSTRY PERFORMANCE (live, {UNIVERSE} universe, "
                     f"strongest first)", shown),
            sh.block("STOCKS INSIDE THE STRONGEST AND WEAKEST INDUSTRIES", detail),
            sh.block("WHAT THE OTHER AGENTS CURRENTLY SEE",
                     [sh.peer_view(name) for name in PEERS]),
            sh.block("STOCKS WITH FRESH FILINGS (a company reason, not an "
                     "industry one)", flagged or ["none in the last two hours"]),
            sh.block("VALID INDUSTRY LABELS", sorted(KNOWN_INDUSTRIES)),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = IndustryAgent()
