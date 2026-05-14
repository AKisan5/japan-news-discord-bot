from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.discord_poster import PostResult
from src.feed_fetcher import FeedItem


def make_item(url: str) -> FeedItem:
    return FeedItem(
        title="Title",
        url=url,
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        feed_name="Feed",
        feed_category="Cat",
    )


def _make_config(feeds=None):
    cfg = MagicMock()
    cfg.feeds = feeds or []
    cfg.settings.http_timeout = 10
    cfg.settings.user_agent = "test-agent"
    cfg.settings.max_items_per_feed = 5
    cfg.settings.post_interval_seconds = 0.0
    cfg.settings.state_retention = 100
    return cfg


# --- exit code 1: missing webhook ---

def test_missing_webhook_exits_1(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    from src.main import main
    assert main() == 1


def test_config_error_exits_1(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    with patch("src.main.load_config", side_effect=ValueError("bad config")):
        from src.main import main
        assert main() == 1


# --- exit code 0: all success ---

def test_no_new_items_does_not_post_exits_0(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    mock_state = MagicMock()
    mock_state.filter_new.return_value = []

    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items") as mock_post:
        from src.main import main
        result = main()

    mock_post.assert_not_called()
    assert result == 0


def test_all_success_exits_0(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    item = make_item("https://example.com/1")
    mock_state = MagicMock()
    mock_state.filter_new.return_value = [item]
    post_result = PostResult(posted=[item], failed=[])

    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items", return_value=post_result):
        from src.main import main
        result = main()

    assert result == 0


# --- exit code 2: partial failure ---

def test_feed_error_with_posts_exits_2(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    good_feed = MagicMock()
    bad_feed = MagicMock()
    item = make_item("https://example.com/1")

    def mock_fetch(feed, **kwargs):
        if feed is good_feed:
            return [item]
        on_error = kwargs.get("on_error")
        if on_error:
            on_error(Exception("network error"))
        return []

    mock_state = MagicMock()
    mock_state.filter_new.return_value = [item]
    post_result = PostResult(posted=[item], failed=[])

    with patch("src.main.load_config", return_value=_make_config([good_feed, bad_feed])), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.fetch_feed", side_effect=mock_fetch), \
         patch("src.main.post_items", return_value=post_result):
        from src.main import main
        result = main()

    assert result == 2


def test_post_failure_exits_2(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    items = [make_item(f"https://example.com/{i}") for i in range(3)]
    mock_state = MagicMock()
    mock_state.filter_new.return_value = items
    post_result = PostResult(posted=[items[0]], failed=[items[1], items[2]])

    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items", return_value=post_result):
        from src.main import main
        result = main()

    assert result == 2


# --- state management ---

def test_only_posted_items_saved(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    posted = make_item("https://example.com/ok")
    failed = make_item("https://example.com/fail")
    mock_state = MagicMock()
    mock_state.filter_new.return_value = [posted, failed]
    post_result = PostResult(posted=[posted], failed=[failed])

    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items", return_value=post_result):
        from src.main import main
        main()

    mock_state.mark_posted.assert_called_once_with([posted])


def test_feed_failure_does_not_stop_posting(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    feed1, feed2 = MagicMock(), MagicMock()
    item = make_item("https://example.com/1")

    def mock_fetch(feed, **kwargs):
        if feed is feed1:
            on_error = kwargs.get("on_error")
            if on_error:
                on_error(Exception("broken"))
            return []
        return [item]

    mock_state = MagicMock()
    mock_state.filter_new.return_value = [item]
    post_result = PostResult(posted=[item], failed=[])
    mock_post = MagicMock(return_value=post_result)

    with patch("src.main.load_config", return_value=_make_config([feed1, feed2])), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.fetch_feed", side_effect=mock_fetch), \
         patch("src.main.post_items", mock_post):
        from src.main import main
        main()

    mock_post.assert_called_once()
