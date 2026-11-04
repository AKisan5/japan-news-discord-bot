"""Shared helpers for the SUI / Even G2 watchers: time, HTTP, state, evidence, posting."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; aki-watchers/1.0; +https://github.com/AKisan5/japan-news-discord-bot)"
NEWS_CHANNEL_ID = "1290456657653928048"
TEST_CHANNEL_ID = "1365351401173028916"
REPORTED_KEEP_DAYS = 45


def now_jst() -> datetime:
    return datetime.now(JST)


def fmt_jst(dt: datetime | None, pattern: str = "%m/%d %H:%M") -> str:
    return dt.astimezone(JST).strftime(pattern) + " JST" if dt else ""


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")  # fxtwitter created_at
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get(url: str, *, timeout: float = 20, headers: dict | None = None,
        params: dict | None = None) -> httpx.Response:
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    return httpx.get(url, timeout=timeout, headers=hdrs, params=params, follow_redirects=True)


def github_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    hdrs = {"Accept": "application/vnd.github+json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    return hdrs


def strip_html(text: str, limit: int = 600) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def feed_entries(url: str) -> list[dict]:
    """RSS/Atom entries as {title, link, published (ISO, UTC), summary}. Raises on fetch errors."""
    import feedparser  # local import: only the collectors need it

    resp = get(url)
    resp.raise_for_status()
    rows = []
    for e in feedparser.parse(resp.text).entries:
        tp = e.get("published_parsed") or e.get("updated_parsed")
        when = datetime(*tp[:6], tzinfo=timezone.utc).isoformat() if tp else (
            e.get("published") or e.get("updated") or "")
        rows.append({"title": strip_html(e.get("title", ""), 200), "link": e.get("link", ""),
                     "published": when, "summary": strip_html(e.get("summary", ""), 700)})
    return rows


@dataclass
class Evidence:
    """One fact source the writer may cite. Only these URLs may appear in a post."""
    source: str
    title: str
    url: str
    published_at: str = ""   # ISO 8601 (UTC or with offset)
    summary: str = ""
    extra_urls: list[str] = field(default_factory=list)
    unverified: bool = False  # e.g. Reddit RSS excerpt only

    def as_payload(self, index: int) -> dict:
        d = asdict(self)
        d["id"] = str(index)
        when = parse_dt(self.published_at)
        d["published_jst"] = fmt_jst(when, "%Y/%m/%d %H:%M") if when else ""
        return d


@dataclass
class Collected:
    evidence: list[Evidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)      # numbers etc. (price, TVL)
    seen: dict[str, list[str]] = field(default_factory=dict)  # per-source keys seen this run

    def fail(self, source: str, exc: Exception | str) -> None:
        msg = f"{source}: {type(exc).__name__ if isinstance(exc, Exception) else exc}"
        logger.warning("source failed: %s", msg)
        self.errors.append(msg)


class WatcherState:
    """state/watchers/<name>.json — reported URLs, seen keys, last post, alert suppression."""

    def __init__(self, name: str, root: Path = Path("state/watchers")) -> None:
        self.path = root / f"{name}.json"
        self.data: dict = {"reported": {}, "seen": {}, "last_posted_at": "", "alerts": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError as exc:
                logger.warning("corrupt state %s: %s (starting fresh)", self.path, exc)

    @property
    def reported(self) -> dict[str, str]:
        return self.data["reported"]

    def is_reported(self, url: str) -> bool:
        return normalize_url(url) in self.reported

    def mark_reported(self, urls: list[str], day: str) -> None:
        for u in urls:
            self.reported[normalize_url(u)] = day

    def seen(self, source: str) -> set[str]:
        return set(self.data["seen"].get(source, []))

    def set_seen(self, source: str, keys: list[str]) -> None:
        merged = list(dict.fromkeys(list(self.data["seen"].get(source, [])) + list(keys)))
        self.data["seen"][source] = merged[-400:]

    def last_posted(self) -> datetime | None:
        return parse_dt(self.data.get("last_posted_at"))

    def save(self, today: str) -> None:
        cutoff = (datetime.fromisoformat(today) - timedelta(days=REPORTED_KEEP_DAYS)).date().isoformat()
        self.data["reported"] = {u: d for u, d in self.reported.items() if d >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


_TRACKING = re.compile(r"[?&](utm_[^=&]+|ref|s|t)=[^&]*")


def normalize_url(url: str) -> str:
    u = url.strip().strip("<>")
    u = re.sub(r"^https?://(www\.)?(twitter|x)\.com/", "https://x.com/", u)
    u = _TRACKING.sub("", u).rstrip("?&")
    return u.rstrip("/")


def since_window(state: WatcherState, now: datetime, *, default_hours: int = 26,
                 max_hours: int = 72) -> datetime:
    """Start of the reporting window: last post (if recent) else default_hours ago.

    The window is never shorter than default_hours: an extra post (e.g. the 19:14 trial on
    11/03) must not shrink the next morning's window. Items already posted are excluded by the
    reported-URL state, so the overlap does not repeat them.
    """
    last = state.last_posted()
    floor = now - timedelta(hours=max_hours)
    minimum = now - timedelta(hours=default_hours)
    if last and last > floor:
        return min(last, minimum)
    return minimum if not last else floor


# --- X (fxtwitter) ---------------------------------------------------------

_STATUS = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")


def fetch_x_post(url: str) -> Evidence | None:
    """Re-fetch an X post through the fxtwitter API (exact text, author and time)."""
    m = _STATUS.search(url)
    if not m:
        return None
    user, sid = m.groups()
    try:
        resp = get(f"https://api.fxtwitter.com/{user}/status/{sid}", timeout=15)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/JSON
        logger.warning("fxtwitter failed for %s: %s", url, type(exc).__name__)
        return None
    if body.get("code") != 200 or not body.get("tweet"):
        return None
    tw = body["tweet"]
    author = (tw.get("author") or {}).get("screen_name") or user
    created = parse_dt(tw.get("created_at"))
    text = tw.get("raw_text", {}).get("text") if isinstance(tw.get("raw_text"), dict) else None
    text = text or tw.get("text") or ""
    return Evidence(
        source=f"X @{author}",
        title=text.splitlines()[0][:120] if text else f"@{author} の投稿",
        url=f"https://x.com/{author}/status/{sid}",
        published_at=created.isoformat() if created else "",
        summary=text[:800],
    )


# --- web articles ----------------------------------------------------------

_META_DATE = [
    re.compile(r'<meta[^>]+(?:property|name|itemprop)="(?:article:published_time|datePublished|pubdate|'
               r'publish[-_]?date|date|dc\.date|parsely-pub-date|sailthru\.date)"[^>]+content="([^"]+)"', re.I),
    re.compile(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name|itemprop)="(?:article:published_time|'
               r'datePublished)"', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'<time[^>]+datetime="([^"]+)"', re.I),
]


def _meta(html: str, prop: str) -> str:
    m = (re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"', html, re.I)
         or re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{prop}"', html, re.I))
    return strip_html(m.group(1), 600) if m else ""


def _page_age(value: str) -> datetime | None:
    dt = parse_dt(value)
    if dt:
        return dt
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_article(url: str, *, page_age: str = "", label: str = "Web 記事") -> Evidence | None:
    """Evidence from the article page itself (title, description, published time), or None.

    The published time comes from the page's own metadata; the search result's page_age is a
    fallback only. No date at all -> None (cannot prove it is new).
    """
    try:
        resp = get(url, timeout=20)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", ""):
        return None
    html = resp.text[:600_000]
    when = None
    for pat in _META_DATE:
        m = pat.search(html)
        if m and (when := parse_dt(m.group(1))):
            break
    when = when or _page_age(page_age)
    if not when:
        return None
    title = _meta(html, "og:title") or strip_html((re.search(r"<title>(.*?)</title>", html, re.S | re.I)
                                                   or re.match("()", "")).group(1), 200)
    desc = _meta(html, "og:description") or _meta(html, "description")
    body = strip_html(" ".join(re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I)), 900)
    host = re.sub(r"^www\.", "", httpx.URL(str(resp.url)).host or "")
    return Evidence(source=f"{label}（{host}）", title=title or url, url=str(resp.url),
                    published_at=when.isoformat(), summary=(desc + " " + body).strip()[:1200])


# --- URL check and posting -------------------------------------------------

def url_alive(url: str) -> bool:
    """False only for definite dead links (404/410) or unknown X posts."""
    if _STATUS.search(url):
        return fetch_x_post(url) is not None
    try:
        resp = get(url, timeout=15)
    except httpx.HTTPError:
        logger.warning("URL check network error: %s", url)
        return True  # do not drop on transient network errors
    if resp.status_code in (404, 410):
        return False
    return True


def post_to_discord(text: str, *, username: str, channel: str) -> list[str]:
    """Post via the #ニュース webhook (username override) or, for tests, via bot token to #bot."""
    from ..discord_poster import _post_with_retry, split_message

    chunks = split_message(text)
    ids: list[str] = []
    if channel == "test":
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set for channel=test")
        with httpx.Client(timeout=30) as client:
            for i, chunk in enumerate(chunks):
                body = f"【テスト・{username}】\n{chunk}" if i == 0 else chunk
                resp = client.post(
                    f"https://discord.com/api/v10/channels/{TEST_CHANNEL_ID}/messages",
                    headers={"Authorization": f"Bot {token}", "User-Agent": UA},
                    json={"content": body[:2000], "allowed_mentions": {"parse": []}},
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Discord bot post failed: HTTP {resp.status_code}")
                ids.append(resp.json()["id"])
                time.sleep(1.5)
        return ids
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")
    with httpx.Client(timeout=30) as client:
        for chunk in chunks:
            mid = _post_with_retry(client, webhook, {
                "content": chunk, "username": username, "allowed_mentions": {"parse": []},
            })
            if not mid:
                raise RuntimeError("Discord webhook post failed")
            ids.append(mid)
            time.sleep(1.5)
    return ids


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
