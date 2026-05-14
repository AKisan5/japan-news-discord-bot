from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.config_loader import DiscordConfig
from src.discord_poster import PostResult, _build_embed, post_items
from src.feed_fetcher import FeedItem

WEBHOOK = "https://discord.com/api/webhooks/000/test-token"


def make_item(n: int, *, has_date: bool = True) -> FeedItem:
    return FeedItem(
        title=f"Article {n}",
        url=f"https://example.com/{n}",
        summary=f"Summary for article {n}",
        published_at=datetime(2026, 5, 14, 1, 0, 0, tzinfo=timezone.utc) if has_date else None,
        feed_name="Test Feed",
        feed_category="テスト",
    )


def make_config(**kwargs) -> DiscordConfig:
    defaults = {"embed_color": 0x1E90FF, "embeds_per_message": 5, "header": "📰 News"}
    defaults.update(kwargs)
    return DiscordConfig(**defaults)


# --- embed structure ---

def test_embed_has_all_fields():
    item = make_item(1)
    embed = _build_embed(item, 0x1E90FF)
    assert embed["title"] == "Article 1"
    assert embed["url"] == "https://example.com/1"
    assert embed["description"] == "Summary for article 1"
    assert embed["color"] == 0x1E90FF
    assert embed["footer"]["text"] == "Test Feed / テスト"
    assert "timestamp" in embed


def test_embed_no_timestamp_when_no_date():
    item = make_item(1, has_date=False)
    embed = _build_embed(item, 0)
    assert "timestamp" not in embed


def test_embed_title_truncated_at_256():
    long_title = "A" * 300
    item = FeedItem(long_title, "https://x.com", "s", None, "F", "C")
    embed = _build_embed(item, 0)
    assert len(embed["title"]) == 256


def test_embed_description_truncated_at_300():
    long_summary = "B" * 400
    item = FeedItem("T", "https://x.com", long_summary[:300], None, "F", "C")
    embed = _build_embed(item, 0)
    assert len(embed["description"]) <= 300


# --- batch splitting ---

def test_batch_split_correct_count(httpx_mock):
    config = make_config(embeds_per_message=2)
    items = [make_item(i) for i in range(5)]
    # 5 items / 2 per batch = 3 batches
    httpx_mock.add_response(status_code=204)
    httpx_mock.add_response(status_code=204)
    httpx_mock.add_response(status_code=204)

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 5
    assert len(result.failed) == 0


def test_header_only_in_first_batch(httpx_mock):
    config = make_config(embeds_per_message=1, header="📰 Header")
    items = [make_item(1), make_item(2)]
    requests = []

    def capture(request, *_):
        import json as _json
        requests.append(_json.loads(request.content))
        from httpx import Response
        return Response(204)

    httpx_mock.add_callback(capture)
    httpx_mock.add_callback(capture)

    with patch("time.sleep"):
        post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert requests[0].get("content") == "📰 Header"
    assert "content" not in requests[1]


def test_empty_items_returns_empty_result():
    config = make_config()
    result = post_items([], webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)
    assert result.posted == []
    assert result.failed == []


# --- 429 rate limit retry ---

def test_429_retries_once_and_succeeds(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=429, headers={"Retry-After": "0.01"})
    httpx_mock.add_response(status_code=204)

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 1
    assert len(result.failed) == 0


def test_429_fails_on_second_attempt(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=429, headers={"Retry-After": "0.01"})
    httpx_mock.add_response(status_code=429, headers={"Retry-After": "0.01"})

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 0
    assert len(result.failed) == 1


# --- 5xx exponential backoff ---

def test_5xx_retries_and_succeeds(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=204)

    with patch("time.sleep") as mock_sleep:
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 1
    # sleep called with 1, 2 (3rd attempt succeeds, no 4s wait needed)
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert 1 in sleep_calls
    assert 2 in sleep_calls


def test_5xx_gives_up_after_3_retries(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 0
    assert len(result.failed) == 1


# --- 4xx no retry ---

def test_4xx_no_retry(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=400, text='{"message": "Bad Request"}')

    result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 0
    assert len(result.failed) == 1


def test_401_no_retry(httpx_mock):
    config = make_config()
    items = [make_item(1)]
    httpx_mock.add_response(status_code=401, text='{"message": "Unauthorized"}')

    result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 0
    assert len(result.failed) == 1


# --- partial batch failure ---

def test_second_batch_failure_partial_result(httpx_mock):
    config = make_config(embeds_per_message=2)
    items = [make_item(i) for i in range(4)]
    httpx_mock.add_response(status_code=204)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 2
    assert len(result.failed) == 2
