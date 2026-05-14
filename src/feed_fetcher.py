from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import feedparser
import httpx

from .config_loader import FeedConfig

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    published_at: datetime | None
    feed_name: str
    feed_category: str


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_feed(
    feed: FeedConfig,
    *,
    timeout: int,
    user_agent: str,
    max_items: int,
    on_error: Callable[[Exception], None] | None = None,
) -> list[FeedItem]:
    try:
        resp = httpx.get(
            feed.url,
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
    except Exception as exc:
        logger.warning("Failed to fetch %s (%s): %s", feed.name, feed.url, exc)
        if on_error:
            on_error(exc)
        return []

    items: list[FeedItem] = []
    for entry in parsed.entries:
        url = getattr(entry, "link", None)
        if not url:
            continue

        title = getattr(entry, "title", "(no title)")
        raw_summary = getattr(entry, "summary", "") or ""
        summary = _clean_html(raw_summary)[:300]

        published_at: datetime | None = None
        pt = getattr(entry, "published_parsed", None)
        if pt:
            ts = calendar.timegm(pt)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc)

        items.append(
            FeedItem(
                title=title,
                url=url,
                summary=summary,
                published_at=published_at,
                feed_name=feed.name,
                feed_category=feed.category,
            )
        )

    items.sort(
        key=lambda x: (
            x.published_at is None,
            -(x.published_at.timestamp() if x.published_at else 0),
        )
    )
    return items[:max_items]
