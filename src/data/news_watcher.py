import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import feedparser
import httpx
from loguru import logger

from src.database.db import AsyncSessionLocal
from src.database.models import NewsItem

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
]

# Keywords that suggest high market impact
HIGH_IMPACT_KEYWORDS = [
    "etf", "sec", "federal reserve", "fomc", "cpi", "inflation",
    "hack", "exploit", "bankrupt", "insolvent", "stablecoin depeg",
    "usdt", "usdc", "blackrock", "grayscale", "spot bitcoin",
    "liquidation", "flash crash", "regulation", "ban", "sanctions",
]

MAJOR_MACRO_EVENTS = ["fomc", "cpi", "nfp", "pce", "fed", "interest rate"]


class NewsWatcher:
    def __init__(self):
        self._latest: list[dict] = []
        self._last_fetch: datetime = datetime.min.replace(tzinfo=timezone.utc)

    async def fetch_latest(self) -> list[dict]:
        items = []
        async with httpx.AsyncClient(timeout=15) as client:
            for feed_url in RSS_FEEDS:
                try:
                    resp = await client.get(feed_url)
                    parsed = feedparser.parse(resp.text)
                    for entry in parsed.entries[:10]:
                        title = entry.get("title", "")
                        link = entry.get("link", "")
                        pub = self._parse_date(entry)
                        impact = self._classify_impact(title)
                        items.append({
                            "title": title,
                            "url": link,
                            "published_at": pub,
                            "source": feed_url,
                            "impact": impact,
                        })
                except Exception as e:
                    logger.warning(f"news_watcher | {feed_url}: {e}")

        self._latest = items
        self._last_fetch = datetime.now(tz=timezone.utc)
        await self._persist(items)
        logger.debug(f"news_watcher | fetched {len(items)} items")
        return items

    def has_major_event_soon(self, minutes: int = 60) -> bool:
        """Returns True if any high-impact news was published in the last `minutes`."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
        for item in self._latest:
            if item.get("impact") == "high":
                pub = item.get("published_at")
                if pub and pub >= cutoff:
                    return True
        return False

    def get_recent_titles(self, hours: int = 24) -> list[str]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        return [
            item["title"] for item in self._latest
            if item.get("published_at") and item["published_at"] >= cutoff
        ]

    @staticmethod
    def _classify_impact(title: str) -> str:
        lower = title.lower()
        if any(kw in lower for kw in HIGH_IMPACT_KEYWORDS):
            return "high"
        return "low"

    @staticmethod
    def _parse_date(entry) -> datetime | None:
        try:
            raw = entry.get("published") or entry.get("updated")
            if raw:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    async def _persist(self, items: list[dict]):
        async with AsyncSessionLocal() as session:
            for item in items:
                row = NewsItem(
                    published_at=item.get("published_at"),
                    source=item.get("source"),
                    title=item["title"],
                    url=item.get("url"),
                    impact=item.get("impact"),
                )
                session.add(row)
            await session.commit()
