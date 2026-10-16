"""News and filings, primary sources first.

The edge here is speed, and it comes from skipping the media. An NSE filing
sits on the exchange site five to thirty minutes before it is written up, and
that window is the whole advantage. Wire services are kept as a backstop for
stories that never become filings.

What this is *not* for: entry signals. By the time a headline is readable, the
move has happened and faster participants have taken it. News earns its place
as a **blackout and exit** input -- "there is fresh news on this stock, do not
open a new position, and consider closing the one we have."

Everything fetched here is untrusted text. It is stored and counted; it is
never parsed for instructions. See docs/05-security.md.

Source status (probed 2026-10-16):
  NSE announcements  working  -- JSON, carries the symbol, the best source
  RBI (4 feeds)      working  -- press releases, notifications, speeches,
                                 publications. The documented Scripts/Rss.aspx
                                 URLs serve HTML; the real feeds live at
                                 rbi.org.in/<name>_rss.xml
  SEBI circulars     working  -- RSS
  PIB press releases working  -- RSS
  Moneycontrol/ET/BS working  -- RSS
  BSE announcements  flaky    -- needs a Referer header or it is an instant
                                 403, and rate-limits hard even with one.
                                 Treated as best-effort: NSE already covers
                                 most filings, BSE adds BSE-only listings.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from ..ratelimit import TokenBucket
from ..store import events
from .base import Collector

# Exchange and government sites reject obviously scripted clients.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/xml, application/rss+xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements?index=equities"

# BSE rejects anything without a Referer from its own site -- that alone turns
# an instant 403 into a 200. It still rate-limits aggressively, so this is a
# bonus source rather than one we depend on.
BSE_ANNOUNCEMENTS = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_HEADERS = {
    **BROWSER_HEADERS,
    "Referer": "https://www.bseindia.com/corporates/ann.html",
    "Origin": "https://www.bseindia.com",
}


@dataclass(frozen=True)
class RssFeed:
    key: str
    name: str
    url: str
    category: str


# Primary sources carry material information before anyone reports it.
# Wire feeds are breadth, not speed.
RSS_FEEDS = [
    # RBI moves rates, liquidity and bank sentiment. Speeches are in here on
    # purpose: research finds central-bank tone has a measurable effect on
    # returns, and a significant one for India specifically.
    RssFeed("rbi_press", "RBI press releases",
            "https://rbi.org.in/pressreleases_rss.xml", "policy"),
    RssFeed("rbi_notifications", "RBI notifications",
            "https://rbi.org.in/notifications_rss.xml", "regulatory"),
    RssFeed("rbi_speeches", "RBI speeches",
            "https://rbi.org.in/speeches_rss.xml", "policy"),
    RssFeed("rbi_publications", "RBI publications",
            "https://rbi.org.in/Publication_rss.xml", "policy"),
    RssFeed("sebi", "SEBI", "https://www.sebi.gov.in/sebirss.xml", "regulatory"),
    RssFeed("pib", "PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "policy"),
    RssFeed("moneycontrol", "Moneycontrol",
            "https://www.moneycontrol.com/rss/business.xml", "news"),
    RssFeed("et_markets", "Economic Times",
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "news"),
    RssFeed("business_standard", "Business Standard",
            "https://www.business-standard.com/rss/markets-106.rss", "news"),
]

# Filing descriptions that routinely move a price, used to mark an event as
# material rather than housekeeping.
MATERIAL_PATTERNS = re.compile(
    r"board meeting|financial result|dividend|bonus|split|buy ?back|merger|"
    r"acquisition|resignation|appointment|order (win|receipt)|credit rating|"
    r"open offer|delisting|insolvency|fund rais|preferential|qip",
    re.I,
)


def strip_html(text: str) -> str:
    """RSS descriptions arrive as HTML fragments."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return text


def classify(title: str, body: str = "") -> tuple[str, bool]:
    """Returns (category, material). Material filings drive the blackout."""
    blob = f"{title} {body}"
    if MATERIAL_PATTERNS.search(blob):
        return "filing_material", True
    return "filing", False


def parse_nse(payload: Any) -> list[events.Event]:
    """Pure: NSE announcements JSON -> Events.

    NSE gives the symbol directly, which is what makes this source worth more
    than any wire feed: the event is already attached to an instrument.
    """
    out: list[events.Event] = []
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        symbol = (row.get("symbol") or "").strip().upper()
        desc = (row.get("desc") or "").strip()
        detail = (row.get("attchmntText") or "").strip()
        title = f"{symbol}: {desc}" if symbol and desc else (desc or detail[:120])
        if not title:
            continue

        category, material = classify(desc, detail)
        out.append(events.Event(
            source="nse",
            category=category,
            title=title,
            body=detail,
            url=(row.get("attchmntFile") or "").strip(),
            symbols=[symbol] if symbol else [],
            published_at=row.get("an_dt") or row.get("sort_date"),
            meta={
                "company": row.get("sm_name"),
                "industry": row.get("smIndustry"),
                "isin": row.get("sm_isin"),
                "material": material,
                "desc": desc,
            },
        ))
    return out


def parse_bse(payload: Any) -> list[events.Event]:
    """Pure: BSE announcements JSON -> Events.

    BSE identifies companies by scrip code and long name rather than a ticker,
    so there is no symbol to attach the way NSE gives one. The company name is
    kept in meta instead.
    """
    out: list[events.Event] = []
    rows = (payload or {}).get("Table") if isinstance(payload, dict) else payload
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        headline = (row.get("NEWSSUB") or row.get("HEADLINE") or "").strip()
        company = (row.get("SLONGNAME") or "").strip()
        if not headline and not company:
            continue
        title = f"{company}: {headline}" if company and headline else (headline or company)
        detail = (row.get("MORE") or row.get("NEWSBODY") or "").strip()
        category, material = classify(headline, detail)
        out.append(events.Event(
            source="bse",
            category=category,
            title=title,
            body=strip_html(detail),
            url=(row.get("ATTACHMENTNAME") or "").strip(),
            published_at=row.get("NEWS_DT") or row.get("DissemDT"),
            meta={
                "company": company,
                "scrip_code": row.get("SCRIP_CD"),
                "material": material,
            },
        ))
    return out


def parse_rss(feed: RssFeed, xml_text: str) -> list[events.Event]:
    """Pure: RSS/Atom XML -> Events. Malformed feeds yield nothing."""
    out: list[events.Event] = []
    try:
        root = ET.fromstring(xml_text.lstrip("﻿").strip())
    except ET.ParseError:
        return out

    # RSS 2.0 puts items under channel/item; Atom uses feed/entry.
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")

    for item in items:
        def text_of(*names: str) -> str:
            for name in names:
                node = item.find(name)
                if node is not None and (node.text or "").strip():
                    return node.text.strip()
                # Atom links carry the URL in an attribute.
                if node is not None and node.get("href"):
                    return node.get("href", "")
            return ""

        title = strip_html(text_of("title", "{http://www.w3.org/2005/Atom}title"))
        if not title:
            continue
        body = strip_html(text_of(
            "description", "summary", "{http://www.w3.org/2005/Atom}summary"))
        link = text_of("link", "{http://www.w3.org/2005/Atom}link", "guid")
        published = text_of(
            "pubDate", "published", "{http://www.w3.org/2005/Atom}published", "updated")

        out.append(events.Event(
            source=feed.key,
            category=feed.category,
            title=title,
            body=body,
            url=link,
            published_at=published or None,
            meta={"feed": feed.name},
        ))
    return out


class NewsCollector(Collector):
    """Polls NSE plus the RSS feeds."""

    def __init__(self) -> None:
        super().__init__(
            name="news",
            source="mixed",
            # Filings land all day; two minutes keeps the speed edge without
            # hammering a government site.
            interval_seconds=120.0,
            rate=TokenBucket(rate=1.0, capacity=3.0, name="news"),
        )
        self.last_counts: dict[str, int] = {}

    async def fetch(self) -> Any:
        raw: dict[str, Any] = {"nse": None, "bse": None, "rss": {}}

        try:
            r = await self.http_get(NSE_ANNOUNCEMENTS, headers=BROWSER_HEADERS)
            raw["nse"] = r.json()
        except Exception as exc:          # noqa: BLE001
            # NSE is the most valuable source, so log it loudly -- but one
            # source failing must not cost us the others.
            print(f"[news] NSE unavailable: {exc}")

        # Best-effort: BSE blocks often enough that a failure here is routine.
        try:
            from datetime import timedelta

            from ..clock import now_ist

            today = now_ist().date()
            r = await self.http_get(
                BSE_ANNOUNCEMENTS,
                params={
                    "pageno": 1, "strCat": -1, "strType": "C", "strSearch": "P",
                    "subcategory": -1, "strScrip": "",
                    "strPrevDate": (today - timedelta(days=3)).strftime("%Y%m%d"),
                    "strToDate": today.strftime("%Y%m%d"),
                },
                headers=BSE_HEADERS,
            )
            raw["bse"] = r.json()
        except Exception as exc:          # noqa: BLE001
            print(f"[news] BSE unavailable (expected sometimes): {exc}")

        for feed in RSS_FEEDS:
            try:
                r = await self.http_get(feed.url, headers=BROWSER_HEADERS)
                raw["rss"][feed.key] = r.text
            except Exception as exc:      # noqa: BLE001
                print(f"[news] {feed.key}: {exc}")

        if raw["nse"] is None and raw["bse"] is None and not raw["rss"]:
            raise RuntimeError("Koi news source available nahi hai")
        return raw

    def parse(self, raw: Any) -> list[events.Event]:
        out: list[events.Event] = []
        counts: dict[str, int] = {}

        nse_rows = parse_nse((raw or {}).get("nse"))
        if nse_rows:
            counts["nse"] = len(nse_rows)
            out.extend(nse_rows)

        bse_rows = parse_bse((raw or {}).get("bse"))
        if bse_rows:
            counts["bse"] = len(bse_rows)
            out.extend(bse_rows)

        feeds_by_key = {f.key: f for f in RSS_FEEDS}
        for key, xml_text in ((raw or {}).get("rss") or {}).items():
            feed = feeds_by_key.get(key)
            if not feed:
                continue
            rows = parse_rss(feed, xml_text)
            if rows:
                counts[key] = len(rows)
                out.extend(rows)

        self.last_counts = counts
        return out

    async def run(self) -> int:
        """Events go to the events store, not the macro series table."""
        from ..clock import now_ist
        from ..store import macro

        self.status.runs += 1
        self.status.last_run = now_ist().isoformat(timespec="seconds")
        run_id = macro.start_run(self.name)

        try:
            parsed = self.parse(await self.fetch())
        except Exception as exc:          # noqa: BLE001
            self.status.failures += 1
            self.status.last_error = str(exc)
            macro.finish_run(run_id, "error", 0, str(exc))
            print(f"[news] failed: {exc}")
            return 0

        result = events.save(parsed)
        self.status.points_last = result["new"]
        self.status.points_total += result["new"]
        self.status.last_success = self.status.last_run
        self.status.last_error = None
        macro.finish_run(run_id, "ok", result["new"])
        return result["new"]


# ------------------------------------------------------------------ reads
def blackout_symbols(minutes: int = 120) -> list[str]:
    """Instruments with fresh filings -- no new positions in these.

    Deliberately conservative: any filing counts, material or not. Missing a
    trade costs an opportunity, trading into an unread announcement costs
    money.
    """
    return events.symbols_with_news(since_minutes=minutes)


def news_read(limit: int = 20) -> dict[str, Any]:
    return {
        "recent": events.recent(limit=limit),
        "blackout_symbols": blackout_symbols(),
        "stats": events.stats(),
    }


collector = NewsCollector()
