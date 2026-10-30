from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import trafilatura

from .feed_fetcher import FeedItem

_log = logging.getLogger(__name__)

MAX_BODY_CHARS = 6000
MIN_BODY_CHARS = 400


@dataclass
class Article:
    title: str
    url: str
    source: str
    body: str = ""


def _publisher_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and bool(host) and not (
        host == "google.com" or host.endswith(".google.com")
        or host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
    )


class _GoogleLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.urls.append(attrs["href"])
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.urls.insert(0, attrs.get("href", ""))
        elif tag == "meta" and attrs.get("property") == "og:url":
            self.urls.insert(0, attrs.get("content", ""))


def _google_target(url: str, html: str) -> str | None:
    # Older Google News links carry a publisher URL in a base64 protobuf token.
    token = urlparse(url).path.rsplit("/", 1)[-1]
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        match = re.search(rb"https?://[^\s\x00-\x20\x7f-\xff]+", decoded)
        if match:
            candidate = match.group().decode("ascii")
            if _publisher_url(candidate):
                return candidate
    except (ValueError, UnicodeError):
        pass
    # New opaque tokens cannot always be resolved. Use an explicit publisher
    # link when Google supplies one, otherwise leave the body unavailable.
    links = _GoogleLinks()
    links.feed(html)
    return next((u for u in links.urls if _publisher_url(u)), None)


def fetch_article(
    item: FeedItem, *, timeout: float = 20, user_agent: str = "news-discord-bot/1.0"
) -> Article:
    """Fetch an article body, retrying once on transient network errors."""
    for attempt in (1, 2):
        try:
            return _fetch_article_once(item, timeout=timeout, user_agent=user_agent)
        except httpx.TransportError as exc:
            if attempt == 2:
                _log.info("Body unavailable for %s: %s", item.url, type(exc).__name__)
    return Article(item.title, item.url, item.feed_name)


def _fetch_article_once(item: FeedItem, *, timeout: float, user_agent: str) -> Article:
    article = Article(item.title, item.url, item.feed_name)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": user_agent}) as client:
            response = client.get(item.url)
            if response.url.host != "news.google.com":
                article.url = str(response.url)
            response.raise_for_status()
            if response.url.host == "news.google.com":
                target = _google_target(str(response.url), response.text)
                if not target:
                    return article
                article.url = target
                response = client.get(target)
                if response.url.host != "news.google.com":
                    article.url = str(response.url)
                response.raise_for_status()
            if response.url.host == "news.google.com":
                return article
            article.url = str(response.url)
            text = trafilatura.extract(
                response.text, url=article.url, include_comments=False,
                include_tables=False, favor_precision=True,
            ) or ""
            if len(text.strip()) >= MIN_BODY_CHARS:
                article.body = text.strip()[:MAX_BODY_CHARS]
    except httpx.TransportError:
        raise
    except Exception as exc:
        _log.info("Body unavailable for %s: %s", item.url, type(exc).__name__)
        # Paywalls, timeouts, malformed HTML, and extraction failures must not
        # promote an RSS teaser into apparent article-body evidence.
        pass
    return article
