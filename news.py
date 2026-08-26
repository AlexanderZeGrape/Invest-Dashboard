"""A FinancialJuice-style squawk built from free RSS feeds.

No API key, no signup. Feeds are fetched in parallel-ish sequence, merged,
deduplicated, tagged by keyword and sorted newest first.

Add or remove sources in FEEDS below — anything that serves RSS works.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SignalDesk/1.0)"}
TIMEOUT = 12

FEEDS = {
    "Breaking": "https://www.investing.com/rss/news.rss",
    "Economy": "https://www.investing.com/rss/news_14.rss",
    "Economic data": "https://www.investing.com/rss/news_95.rss",
    "Commodities": "https://www.investing.com/rss/news_11.rss",
    "Forex": "https://www.investing.com/rss/news_1.rss",
    "Stocks": "https://www.investing.com/rss/news_25.rss",
    "Earnings": "https://www.investing.com/rss/news_1062.rss",
    "ForexLive": "https://www.forexlive.com/feed/",
}

# Headline keyword -> tag. Order matters only for readability.
TAGS = {
    "Energy": ["oil", "crude", "opec", "brent", "wti", "natural gas", "lng",
               "refinery", "pipeline", "diesel", "gasoline", "barrel"],
    "Metals": ["gold", "silver", "copper", "platinum", "palladium", "bullion",
               "miner", "mining", "aluminium", "aluminum", "iron ore"],
    "Agri": ["corn", "wheat", "soybean", "grain", "ethanol", "harvest", "crop",
             "fertiliser", "fertilizer", "livestock", "sugar", "coffee"],
    "AI & chips": ["chip", "chips", "semiconductor", "nvidia", "tsmc", "memory", "dram",
                   "nand", "artificial intelligence", "ai", "data center",
                   "data centre", "asml", "foundry", "micron", "hbm"],
    "Rates & macro": ["fed", "fomc", "ecb", "boj", "cpi", "inflation", "pce",
                      "rate cut", "rate hike", "yield", "treasury", "payroll",
                      "jobless", "gdp", "powell", "central bank", "jackson hole",
                      "bond"],
    "FX": ["dollar", "euro", "yen", "sterling", "currency", "forex", "fx",
           "krona", "peso", "yuan"],
    "Crypto": ["bitcoin", "ethereum", "crypto", "token", "stablecoin"],
    "Geopolitics": ["war", "sanction", "tariff", "strike", "conflict", "iran",
                    "russia", "ukraine", "hormuz", "israel", "china trade",
                    "export control", "embargo"],
}

# Words that historically move whole asset classes, not one company.
HIGH_IMPACT = [
    "fed", "fomc", "ecb", "cpi", "pce", "opec", "sanction", "tariff", "war",
    "rate decision", "jackson hole", "payroll", "emergency", "halt", "default",
    "invasion", "embargo", "hormuz", "shutdown",
]


def _text(node, *names) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(xml_text: str, source: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for node in root.iter("item"):
        title = _text(node, "title")
        if not title:
            continue
        items.append({
            "headline": title,
            "url": _text(node, "link", "guid"),
            "published_raw": _text(node, "pubDate", "date"),
            "source": source,
            "author": _text(node, "author", "{http://purl.org/dc/elements/1.1/}creator"),
        })
    return items


def fetch_feeds(selected: list[str] | None = None, limit_per_feed: int = 40
                ) -> pd.DataFrame:
    """Merged, deduplicated, tagged headline stream."""
    names = selected or list(FEEDS)
    rows = []
    for name in names:
        url = FEEDS.get(name)
        if not url:
            continue
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code != 200:
                continue
            rows.extend(parse_feed(response.text, name)[:limit_per_feed])
        except Exception:
            continue

    if not rows:
        return pd.DataFrame(columns=["published", "headline", "source", "url",
                                     "tags", "impact"])

    frame = pd.DataFrame(rows)
    frame["published"] = pd.to_datetime(
        frame["published_raw"], errors="coerce", utc=True, format="mixed"
    )
    frame = frame.drop(columns=["published_raw"])
    frame["headline"] = frame["headline"].str.strip()
    frame = frame.drop_duplicates(subset=["headline"], keep="first")

    frame["tags"] = frame["headline"].apply(tag_headline)
    frame["impact"] = frame["headline"].apply(
        lambda h: any(_mentions(f" {h.lower()} ", word) for word in HIGH_IMPACT)
    )
    return frame.sort_values("published", ascending=False, na_position="last")


def _mentions(lowered: str, word: str) -> bool:
    """Whole-word match, so 'warnings' never counts as 'war'."""
    if " " in word:
        return word in lowered
    return re.search(rf"\b{re.escape(word.strip())}\b", lowered) is not None


def tag_headline(headline: str) -> list[str]:
    lowered = f" {headline.lower()} "
    return [tag for tag, words in TAGS.items()
            if any(_mentions(lowered, word) for word in words)]


def match_universe(frame: pd.DataFrame, tickers: list[str],
                   names: list[str] | None = None) -> pd.Series:
    """True where a headline mentions one of your tickers or company names."""
    if frame.empty:
        return pd.Series(dtype=bool)
    needles = set()
    for ticker in tickers:
        base = ticker.split(".")[0].split("-")[0].strip().lower()
        if len(base) >= 2 and not base.startswith("^") and not base.isdigit():
            needles.add(base)
    for name in names or []:
        if not isinstance(name, str):
            continue
        first = name.split()[0].strip().lower().strip(",.")
        if len(first) > 3 and first not in {"the", "inc", "corp"}:
            needles.add(first)
    if not needles:
        return pd.Series(False, index=frame.index)
    lowered = frame["headline"].str.lower()
    hit = pd.Series(False, index=frame.index)
    for needle in needles:
        hit |= lowered.str.contains(rf"\b{needle}\b", regex=True, na=False)
    return hit
