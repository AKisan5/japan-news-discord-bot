from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .feed_fetcher import FeedItem

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))


class StateStore:
    def __init__(self, path: Path = Path("state/posted_links.json")) -> None:
        self._path = path
        self._posted: dict[str, str] = {}  # url -> posted_at ISO string

    def load(self) -> set[str]:
        if not self._path.exists():
            logger.debug("State file not found, starting fresh: %s", self._path)
            return set()
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._posted = {
                item["url"]: item["posted_at"] for item in data.get("posted", [])
            }
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Corrupt state file, resetting: %s", exc)
            self._posted = {}
        return set(self._posted.keys())

    def filter_new(self, items: list[FeedItem]) -> list[FeedItem]:
        posted_urls = set(self._posted.keys())
        return [item for item in items if item.url not in posted_urls]

    def mark_posted(self, items: list[FeedItem]) -> None:
        now = datetime.now(_JST).isoformat()
        for item in items:
            self._posted[item.url] = now

    def save(self, *, retention: int) -> None:
        sorted_entries = sorted(
            self._posted.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )[:retention]

        data = {
            "version": 1,
            "posted": [{"url": url, "posted_at": ts} for url, ts in sorted_entries],
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
