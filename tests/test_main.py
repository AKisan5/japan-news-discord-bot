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
    cfg.settings.max_curated = 10
    cfg.settings.claude_timeout = 60
    return cfg


@pytest.fixture(autouse=True)
def mock_curator():
    # Existing delivery tests isolate curation; dedicated tests override this.
    with patch("src.main.curate_items", side_effect=lambda items, **kw: items) as curator:
        yield curator


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

def test_no_new_items_does_not_post_exits_0(monkeypatch, mock_curator):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    mock_state = MagicMock()
    mock_state.filter_new.return_value = []

    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items") as mock_post:
        from src.main import main
        result = main()

    mock_post.assert_not_called()
    mock_curator.assert_not_called()
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


def test_successful_message_ids_saved_to_pending(monkeypatch, tmp_path):
    import json
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "654321")
    item = make_item("https://example.com/ok")
    mock_state = MagicMock()
    mock_state.filter_new.return_value = [item]
    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=mock_state), \
         patch("src.main.post_items", return_value=PostResult(posted=[item], message_ids=["12345"])):
        from src.main import main
        assert main() == 0
    data = json.loads((tmp_path / "state/pending_reads.json").read_text())
    assert data[0]["message_id"] == "12345"
    assert data[0]["channel_id"] == "654321"
    assert data[0]["kind"] == "news"
    assert data[0]["posted_at"]


def test_only_curated_items_posted_and_all_new_urls_saved(mock_curator):
    raw, noise = make_item("https://example.com/important"), make_item("https://example.com/noise")
    japanese = make_item(raw.url)
    japanese.title = "重要なニュース"
    japanese.summary = "重要な発表です。市場に影響します。"
    mock_curator.side_effect = None
    mock_curator.return_value = [japanese]
    state = MagicMock()
    state.filter_new.return_value = [raw, noise]
    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.test/wh"}), \
         patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=state), \
         patch("src.main.PendingReadStore"), \
         patch("src.main.post_items", return_value=PostResult(posted=[japanese])) as post:
        from src.main import main
        assert main() == 0
    assert post.call_args.args == ([japanese],)
    mock_curator.assert_called_once_with([raw, noise], max_curated=10, timeout=60)
    state.mark_posted.assert_called_once_with([raw, noise])
    state.save.assert_called_once_with(retention=100)


def test_empty_selection_saves_noise_without_discord(mock_curator):
    mock_curator.side_effect = None
    mock_curator.return_value = []
    noise = make_item("https://example.com/noise")
    state = MagicMock()
    state.filter_new.return_value = [noise]
    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.test/wh"}), \
         patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=state), \
         patch("src.main.PendingReadStore") as pending, \
         patch("src.main.post_items") as post:
        from src.main import main
        assert main() == 0
    state.mark_posted.assert_called_once_with([noise])
    post.assert_not_called()
    pending.return_value.append.assert_not_called()


@pytest.mark.parametrize("missing_key", [True, False])
def test_real_curator_failure_never_posts_or_saves(
    monkeypatch, mock_curator, httpx_mock, missing_key, caplog
):
    from src.claude_curator import curate_items
    mock_curator.side_effect = curate_items
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    if missing_key:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret-never-log")
        httpx_mock.add_response(json={"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "not JSON test-secret-never-log"}
        ]})
    state = MagicMock()
    state.filter_new.return_value = [make_item("https://example.com/1")]
    with patch("src.main.load_config", return_value=_make_config()), \
         patch("src.main.StateStore", return_value=state), \
         patch("src.main.PendingReadStore") as pending, \
         patch("src.main.post_items") as post:
        from src.main import main
        assert main() == (1 if missing_key else 2)
    post.assert_not_called()
    state.mark_posted.assert_not_called()
    state.save.assert_not_called()
    pending.return_value.save.assert_not_called()
    assert "test-secret-never-log" not in caplog.text


def test_config_50_category_posts_and_pending_reads(
    monkeypatch, tmp_path, httpx_mock, mock_curator
):
    import json
    from src.categories import CATEGORIES
    from src.claude_curator import CuratedItem
    from src.config_loader import load_config

    config = load_config()
    config.settings.post_interval_seconds = 0
    assert config.settings.max_curated == 50
    assert config.settings.claude_timeout == 120
    assert config.settings.max_items_per_feed == 5
    items = [
        CuratedItem(f"記事{i}", f"https://example.com/{i}", "事実と重要な理由。",
                    None, "Mixed feed", CATEGORIES[i % 6], i % 5 + 1)
        for i in reversed(range(50))
    ]
    mock_curator.side_effect = None
    mock_curator.return_value = items
    state = MagicMock()
    state.filter_new.return_value = items
    # Two batches for each of six categories (8 or 9 items each).
    ids = [str(100 + i) for i in range(12)]
    for message_id in ids:
        httpx_mock.add_response(json={"id": message_id})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/wh")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "654321")
    with patch("src.main.load_config", return_value=config), \
         patch("src.main.StateStore", return_value=state), \
         patch("src.main.fetch_feed", return_value=[]) as fetch:
        from src.main import main
        assert main() == 0
    mock_curator.assert_called_once_with(items, max_curated=50, timeout=120)
    assert all(call.kwargs["max_items"] == 5 for call in fetch.call_args_list)
    requests = httpx_mock.get_requests()
    assert len(requests) == 12
    assert sum(len(json.loads(r.content)["embeds"]) for r in requests) == 50
    for i, category in enumerate(CATEGORIES):
        first, rest = [json.loads(r.content) for r in requests[i * 2:i * 2 + 2]]
        assert f"**{category}**" in first["content"]
        assert f"{9 if i < 2 else 8}件" in first["content"]
        assert f"**{category}**（続き）" in rest["content"]
        assert len(first["embeds"]) == 5
    assert all(r.url.params["wait"] == "true" for r in requests)
    pending = json.loads((tmp_path / "state/pending_reads.json").read_text())
    assert [entry["message_id"] for entry in pending] == ids
    assert all(entry["channel_id"] == "654321" for entry in pending)
    state.mark_posted.assert_called_once_with(items)
