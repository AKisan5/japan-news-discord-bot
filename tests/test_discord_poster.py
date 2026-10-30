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


def test_embed_description_truncated_at_900():
    long_summary = "B" * 1000
    item = FeedItem("T", "https://x.com", long_summary, None, "F", "C")
    embed = _build_embed(item, 0)
    assert len(embed["description"]) == 900


# --- batch splitting ---

def test_batch_split_correct_count(httpx_mock):
    config = make_config(embeds_per_message=2)
    items = [make_item(i) for i in range(5)]
    # 5 items / 2 per batch = 3 batches
    httpx_mock.add_response(status_code=200, json={"id": "123456"})
    httpx_mock.add_response(status_code=200, json={"id": "123456"})
    httpx_mock.add_response(status_code=200, json={"id": "123456"})

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 5
    assert len(result.failed) == 0


def test_category_header_and_continuation_override_config(httpx_mock):
    config = make_config(embeds_per_message=1, header="📰 Header")
    items = [make_item(1), make_item(2)]
    requests = []

    def capture(request, *_):
        import json as _json
        requests.append(_json.loads(request.content))
        from httpx import Response
        return Response(200, json={"id": "123456"})

    httpx_mock.add_callback(capture)
    httpx_mock.add_callback(capture)

    with patch("time.sleep"):
        post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert requests[0]["content"] == "📰 **テクノロジー**（本日の要約・2件）\n読んだら✅を付けてください"
    assert requests[1]["content"] == "📰 **テクノロジー**（続き）\n読んだら✅を付けてください"


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
    httpx_mock.add_response(status_code=200, json={"id": "123456"})

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
    httpx_mock.add_response(status_code=200, json={"id": "123456"})

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
    httpx_mock.add_response(status_code=200, json={"id": "123456"})
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=500)

    with patch("time.sleep"):
        result = post_items(items, webhook_url=WEBHOOK, discord_config=config, interval_seconds=0)

    assert len(result.posted) == 2
    assert len(result.failed) == 2


@pytest.mark.parametrize("query", ["", "?thread_id=999", "?wait=false&thread_id=999"])
def test_wait_true_captures_message_id(httpx_mock, query):
    httpx_mock.add_response(status_code=200, json={"id": "987654321"})
    result = post_items([make_item(1)], webhook_url=WEBHOOK + query,
                        discord_config=make_config(), interval_seconds=0)
    assert result.message_ids == ["987654321"]
    request = httpx_mock.get_request()
    assert request.url.params.get_list("wait") == ["true"]
    if query:
        assert request.url.params["thread_id"] == "999"


@pytest.mark.parametrize("response", [{}, {"id": None}, {"id": "bad"}, []])
def test_missing_id_is_failure(httpx_mock, response):
    httpx_mock.add_response(status_code=200, json=response)
    result = post_items([make_item(1)], webhook_url=WEBHOOK,
                        discord_config=make_config(), interval_seconds=0)
    assert result.message_ids == []
    assert result.posted == []
    assert len(result.failed) == 1


def test_204_without_id_is_failure(httpx_mock):
    httpx_mock.add_response(status_code=204)
    result = post_items([make_item(1)], webhook_url=WEBHOOK,
                        discord_config=make_config(), interval_seconds=0)
    assert not result.message_ids
    assert len(result.failed) == 1


def test_partial_failure_only_returns_successful_ids(httpx_mock):
    httpx_mock.add_response(status_code=200, json={"id": "123"})
    httpx_mock.add_response(status_code=403)
    result = post_items([make_item(1), make_item(2)], webhook_url=WEBHOOK,
                        discord_config=make_config(embeds_per_message=1), interval_seconds=0)
    assert result.message_ids == ["123"]
    assert [item.title for item in result.posted] == ["Article 1"]
    assert [item.title for item in result.failed] == ["Article 2"]


def test_japanese_curated_embed_and_reminder(httpx_mock):
    import json
    from src.claude_curator import CuratedItem
    item = CuratedItem("新技術の発表", "https://example.com/1", "要約です。" * 100,
                       None, "Primary source", "テクノロジー", 5)
    httpx_mock.add_response(json={"id": "12345"})
    result = post_items([item], webhook_url=WEBHOOK, discord_config=DiscordConfig(), interval_seconds=0)
    request = httpx_mock.get_request()
    payload = json.loads(request.content)
    assert payload["content"] == "📰 **テクノロジー**（本日の要約・1件）\n読んだら✅を付けてください"
    assert payload["embeds"][0]["title"] == item.title
    assert payload["embeds"][0]["description"] == item.summary
    assert payload["embeds"][0]["footer"]["text"] == "Primary source / テクノロジー"
    assert request.url.params["wait"] == "true"
    assert result.message_ids == ["12345"]

def test_category_order_importance_and_separate_batches(httpx_mock):
    import json
    from src.categories import CATEGORIES
    from src.claude_curator import CuratedItem

    items = [
        CuratedItem(f"{category}-{importance}", f"https://example.com/{i}/{importance}",
                    "要約", None, "Mixed feed", category, importance)
        for i, category in enumerate(reversed(CATEGORIES))
        for importance in (1, 5, 3)
    ]
    for i in range(12):
        httpx_mock.add_response(json={"id": str(i + 1)})
    with patch("time.sleep") as sleep:
        result = post_items(items, webhook_url=WEBHOOK,
                            discord_config=make_config(embeds_per_message=2),
                            interval_seconds=1.5)
    requests = httpx_mock.get_requests()
    assert result.message_ids == [str(i + 1) for i in range(12)]
    assert len(result.posted) == 18
    assert not result.failed
    assert sleep.call_count == 11
    for i, category in enumerate(CATEGORIES):
        first = json.loads(requests[2 * i].content)
        rest = json.loads(requests[2 * i + 1].content)
        assert first["content"] == f"📰 **{category}**（本日の要約・3件）\n読んだら✅を付けてください"
        assert rest["content"] == f"📰 **{category}**（続き）\n読んだら✅を付けてください"
        assert [e["title"] for e in first["embeds"]] == [f"{category}-5", f"{category}-3"]
        assert [e["title"] for e in rest["embeds"]] == [f"{category}-1"]
        assert first["allowed_mentions"] == {"parse": []}
