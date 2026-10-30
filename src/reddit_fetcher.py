"""Fetch subreddit listings via Reddit's official OAuth API (app-only)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Callable

import httpx

from .config_loader import FeedConfig
from .feed_fetcher import FeedItem

logger = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
SUMMARY_LIMIT = 300


class RedditClient:
    def __init__(self, client_id: str, client_secret: str, *, user_agent: str, timeout: float):
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_env(cls, *, timeout: float, default_user_agent: str) -> "RedditClient | None":
        cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
        secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
        if not cid or not secret:
            return None
        ua = os.environ.get("REDDIT_USER_AGENT", "").strip() or default_user_agent
        return cls(cid, secret, user_agent=ua, timeout=timeout)

    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = httpx.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Reddit token response missing access_token")
        self._token = token
        return token

    def fetch(
        self,
        feed: FeedConfig,
        *,
        max_items: int,
        on_error: Callable[[Exception], None] | None = None,
    ) -> list[FeedItem]:
        try:
            token = self._get_token()
            params: dict[str, str | int] = {"limit": max(max_items * 2, 10), "raw_json": 1}
            if feed.listing == "top":
                params["t"] = feed.time_filter
            resp = httpx.get(
                f"{API_BASE}/r/{feed.subreddit}/{feed.listing}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            children = resp.json()["data"]["children"]
        except Exception as exc:
            logger.warning("Failed to fetch Reddit %s (r/%s): %s", feed.name, feed.subreddit, exc)
            if on_error:
                on_error(exc)
            return []

        items: list[FeedItem] = []
        for child in children:
            post = child.get("data", {})
            if post.get("stickied") or post.get("over_18"):
                continue
            items.append(_to_item(post, feed))
            if len(items) >= max_items:
                break
        return items


def _to_item(post: dict, feed: FeedConfig) -> FeedItem:
    permalink = "https://www.reddit.com" + post.get("permalink", "")
    selftext = (post.get("selftext") or "").strip()
    external = post.get("url_overridden_by_dest") or ""
    url = external if (external and not selftext and not post.get("is_self")) else permalink
    summary = " ".join(selftext.split())[:SUMMARY_LIMIT]
    if not summary and url != permalink:
        summary = f"r/{post.get('subreddit', feed.subreddit)} discussion: {permalink}"[:SUMMARY_LIMIT]
    created = post.get("created_utc")
    published = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
    return FeedItem(
        title=post.get("title", "(no title)"),
        url=url,
        summary=summary,
        published_at=published,
        feed_name=feed.name,
        feed_category=feed.category,
    )
