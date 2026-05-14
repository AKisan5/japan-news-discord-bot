from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.feed_fetcher import FeedItem
from src.deduplicator import StateStore

_JST = timezone(timedelta(hours=9))


def make_item(url: str) -> FeedItem:
    return FeedItem(
        title="Test Article",
        url=url,
        summary="Summary text",
        published_at=datetime.now(_JST),
        feed_name="Test Feed",
        feed_category="テスト",
    )


def test_filter_new_empty_state(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    items = [make_item("https://example.com/1"), make_item("https://example.com/2")]
    new = store.filter_new(items)
    assert len(new) == 2


def test_filter_new_excludes_existing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "posted": [
                {"url": "https://example.com/1", "posted_at": "2026-05-14T10:00:00+09:00"}
            ],
        }),
        encoding="utf-8",
    )
    store = StateStore(path)
    store.load()
    items = [make_item("https://example.com/1"), make_item("https://example.com/2")]
    new = store.filter_new(items)
    assert len(new) == 1
    assert new[0].url == "https://example.com/2"


def test_filter_new_all_existing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "posted": [
                {"url": "https://example.com/1", "posted_at": "2026-05-14T10:00:00+09:00"},
                {"url": "https://example.com/2", "posted_at": "2026-05-14T10:00:00+09:00"},
            ],
        }),
        encoding="utf-8",
    )
    store = StateStore(path)
    store.load()
    items = [make_item("https://example.com/1"), make_item("https://example.com/2")]
    assert store.filter_new(items) == []


def test_mark_and_save(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    item = make_item("https://example.com/new")
    store.mark_posted([item])
    store.save(retention=1000)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["posted"]) == 1
    assert data["posted"][0]["url"] == "https://example.com/new"
    assert "posted_at" in data["posted"][0]


def test_retention_trims_oldest(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    items = [make_item(f"https://example.com/{i}") for i in range(10)]
    store.mark_posted(items)
    store.save(retention=5)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(data["posted"]) == 5


def test_state_missing_file_initializes_empty(tmp_path):
    store = StateStore(tmp_path / "nonexistent.json")
    urls = store.load()
    assert urls == set()


def test_state_corrupt_file_resets(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    store = StateStore(path)
    urls = store.load()
    assert urls == set()


def test_atomic_write_no_temp_files(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.mark_posted([make_item("https://example.com/a")])
    store.save(retention=100)

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0
    assert (tmp_path / "state.json").exists()


def test_save_creates_parent_dirs(tmp_path):
    nested_path = tmp_path / "deep" / "dir" / "state.json"
    store = StateStore(nested_path)
    store.load()
    store.mark_posted([make_item("https://example.com/a")])
    store.save(retention=100)
    assert nested_path.exists()


def test_duplicate_urls_deduplicated(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    item = make_item("https://example.com/same")
    store.mark_posted([item, item])
    store.save(retention=1000)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    urls = [p["url"] for p in data["posted"]]
    assert urls.count("https://example.com/same") == 1


def test_load_returns_url_set(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "posted": [
                {"url": "https://a.com/1", "posted_at": "2026-05-14T10:00:00+09:00"},
                {"url": "https://a.com/2", "posted_at": "2026-05-14T10:00:00+09:00"},
            ],
        }),
        encoding="utf-8",
    )
    store = StateStore(path)
    urls = store.load()
    assert urls == {"https://a.com/1", "https://a.com/2"}
