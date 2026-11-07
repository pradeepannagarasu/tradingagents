"""News intelligence: ingestion from allow-listed sources, de-duplication, entity tagging,
sentiment/importance classification, fact-vs-speculation separation and point-in-time filtering.

Classification here is a transparent lexicon model — deliberately simple and auditable.
It is a MODEL ESTIMATE, not a fact, and is labelled as such in every report.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

# Trust tiers. Social media / anonymous sources are never treated as verified.
SOURCE_TIERS = {
    "official": {"NSE", "BSE", "SEBI", "RBI", "PIB", "MoF", "MoSPI", "NSE Indices"},
    "licensed_wire": {"Reuters", "Bloomberg", "PTI"},
    "media": {"Economic Times", "Business Standard", "Mint", "Moneycontrol", "CNBC-TV18", "Financial Express"},
}

RSS_SOURCES = {
    "SEBI": "https://www.sebi.gov.in/sebirss.xml",
    "RBI": "https://www.rbi.org.in/pressreleases_rss.xml",
    "PIB": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
}

NIFTY_ENTITY_ALIASES = {
    "RELIANCE": ["reliance industries", "ril"], "HDFCBANK": ["hdfc bank"], "ICICIBANK": ["icici bank"],
    "INFY": ["infosys"], "TCS": ["tata consultancy", "tcs"], "ITC": ["itc ltd", "itc limited"],
    "LT": ["larsen", "l&t"], "SBIN": ["state bank of india", "sbi"], "BHARTIARTL": ["bharti airtel", "airtel"],
    "KOTAKBANK": ["kotak mahindra bank", "kotak bank"], "AXISBANK": ["axis bank"], "HINDUNILVR": ["hindustan unilever", "hul"],
    "BAJFINANCE": ["bajaj finance"], "MARUTI": ["maruti suzuki"], "SUNPHARMA": ["sun pharma"],
    "TATAMOTORS": ["tata motors"], "M&M": ["mahindra & mahindra", "mahindra and mahindra"], "WIPRO": ["wipro"],
    "HCLTECH": ["hcl tech"], "NTPC": ["ntpc"], "ONGC": ["ongc", "oil and natural gas"], "TITAN": ["titan company"],
    "ADANIENT": ["adani enterprises"], "ULTRACEMCO": ["ultratech"], "ASIANPAINT": ["asian paints"],
    "NIFTY 50": ["nifty", "sensex", "indian equities", "dalal street", "stock market"],
    "MACRO": ["repo rate", "rbi policy", "monetary policy", "inflation", "cpi", "gdp", "budget", "fiscal deficit",
              "rupee", "crude", "federal reserve", "fed ", "treasury yield"],
}

POSITIVE = ["beats", "surge", "record high", "upgrade", "rate cut", "growth accelerat", "profit rises", "profit jumps",
            "strong demand", "order win", "approval", "inflows", "rally", "outperform", "raises guidance", "eases"]
NEGATIVE = ["miss", "plunge", "downgrade", "rate hike", "slowdown", "loss widens", "profit falls", "default", "fraud",
            "probe", "penalty", "outflows", "selloff", "sell-off", "cuts guidance", "pledge", "resign", "ban", "war",
            "tariff", "recession", "crash"]
SPECULATIVE = ["reportedly", "sources said", "sources say", "people familiar", "rumour", "rumor", "likely to", "may ",
               "could ", "speculation", "unconfirmed", "expected to", "said to be"]
HIGH_IMPORTANCE = ["rbi", "repo", "monetary policy", "budget", "sebi", "results", "earnings", "gdp", "cpi", "fed",
                   "merger", "acquisition", "default", "fraud", "war", "election"]
SCHEDULED_HINTS = ["to announce", "scheduled", "will meet", "board meeting", "policy meeting", "on monday", "next week",
                   "results on", "record date", "ex-date"]


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    published_at: datetime
    summary: str = ""
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entities: list[str] = field(default_factory=list)
    sentiment: int = 0
    importance: float = 0.0
    speculative: bool = False
    scheduled: bool = False
    trust_tier: str = "unknown"
    duplicate_of: str | None = None

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.url}|{self.title}".encode()).hexdigest()[:20]

    @property
    def verified(self) -> bool:
        return self.trust_tier in ("official", "licensed_wire") and not self.speculative

    def to_row(self) -> dict:
        return {"id": self.id, "source": self.source, "url": self.url, "title": self.title, "summary": self.summary,
                "published_at": self.published_at.astimezone(timezone.utc).replace(tzinfo=None),
                "retrieved_at": self.retrieved_at.astimezone(timezone.utc).replace(tzinfo=None),
                "entities": self.entities, "sentiment": self.sentiment, "importance": self.importance,
                "verified": self.verified, "speculative": self.speculative, "duplicate_of": self.duplicate_of,
                "trust_tier": self.trust_tier}


def tier_for(source: str) -> str:
    for tier, names in SOURCE_TIERS.items():
        if source in names:
            return tier
    return "unverified"


def classify(item: NewsItem) -> NewsItem:
    text = f"{item.title} {item.summary}".lower()
    pos = sum(text.count(w) for w in POSITIVE)
    neg = sum(text.count(w) for w in NEGATIVE)
    item.sentiment = 1 if pos > neg else (-1 if neg > pos else 0)
    item.speculative = any(w in text for w in SPECULATIVE)
    item.scheduled = any(w in text for w in SCHEDULED_HINTS)
    item.entities = sorted({sym for sym, aliases in NIFTY_ENTITY_ALIASES.items() if any(a in text for a in aliases)})
    hits = sum(1 for w in HIGH_IMPORTANCE if w in text)
    item.trust_tier = tier_for(item.source)
    tier_w = {"official": 1.0, "licensed_wire": 0.85, "media": 0.6}.get(item.trust_tier, 0.2)
    item.importance = round(min(1.0, 0.25 + 0.2 * hits) * tier_w, 3)
    return item


def _shingles(text: str, k: int = 3) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


def deduplicate(items: list[NewsItem], threshold: float = 0.6) -> list[NewsItem]:
    """Mark near-duplicate stories (Jaccard on title word 3-shingles). Earliest publication wins."""
    items = sorted(items, key=lambda x: x.published_at)
    kept: list[tuple[NewsItem, set[str]]] = []
    for it in items:
        sh = _shingles(it.title)
        for k, ksh in kept:
            inter = len(sh & ksh)
            union = len(sh | ksh) or 1
            if inter / union >= threshold:
                it.duplicate_of = k.id
                break
        if not it.duplicate_of:
            kept.append((it, sh))
    return items


def point_in_time(items: list[NewsItem], as_of: datetime) -> list[NewsItem]:
    """Only stories published at or before the decision time (no future leakage)."""
    return [i for i in items if i.published_at <= as_of and i.duplicate_of is None]


def parse_rss(xml_text: str, source: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    out = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = node.findtext("pubDate")
        if not title or not pub:
            continue  # no timestamp → cannot be used point-in-time
        try:
            ts = parsedate_to_datetime(pub)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        desc = re.sub(r"<[^>]+>", " ", node.findtext("description") or "").strip()
        out.append(classify(NewsItem(source=source, title=title, url=link, published_at=ts, summary=desc[:500])))
    return out


class NewsService:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": "triagent-research/0.1"})

    def fetch_official(self) -> tuple[list[NewsItem], list[str]]:
        items, errors = [], []
        for src, url in RSS_SOURCES.items():
            try:
                r = self.client.get(url)
                r.raise_for_status()
                items.extend(parse_rss(r.text, src))
            except Exception as e:  # noqa: BLE001 — record and continue; never fabricate
                errors.append(f"{src}: {type(e).__name__}: {e}")
        return deduplicate(items), errors
