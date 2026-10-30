import json
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.article_fetcher import Article
from src.claude_curator import API_URL, CurationError, MissingAPIKeyError
from src.config_loader import Config, FeedConfig, Settings
from src.discord_poster import PostResult
from src.feed_fetcher import FeedItem
from src.main import main

WEBHOOK = "https://discord.test/wh"


def item(n=0):
    return FeedItem("Title", f"https://example.com/{n}", "RSS teaser", None, "Feed", "AI")


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "654321")
    cfg = Config(feeds=[], settings=Settings(post_interval_seconds=0, state_retention=100))
    with ExitStack() as stack:
        mocks = {}
        for name in ("load_config", "StateStore", "select_items", "fetch_article",
                     "write_briefing", "post_briefing"):
            mocks[name] = stack.enter_context(patch("src.main." + name))
        mocks["load_config"].return_value = cfg
        state = mocks["StateStore"].return_value
        state.filter_new.return_value = [item(), item(1)]
        mocks["select_items"].return_value = [item()]
        mocks["fetch_article"].return_value = Article("Title", item().url, "Feed", "Body")
        mocks["write_briefing"].return_value = "Japanese briefing"
        mocks["post_briefing"].return_value = PostResult(message_ids=["123"])
        mocks["state"] = state
        mocks["config"] = cfg
        mocks["tmp_path"] = tmp_path
        yield mocks


def test_missing_webhook(pipeline, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL")
    assert main() == 1
    pipeline["select_items"].assert_not_called()


def test_config_error(pipeline):
    pipeline["load_config"].side_effect = ValueError("bad config")
    assert main() == 1
    pipeline["select_items"].assert_not_called()


def test_no_new_items(pipeline):
    pipeline["state"].filter_new.return_value = []
    assert main() == 0
    pipeline["select_items"].assert_not_called()
    pipeline["post_briefing"].assert_not_called()
    pipeline["state"].save.assert_not_called()


def test_selection_fetch_write_post_and_seen_state(pipeline):
    assert main() == 0
    pipeline["select_items"].assert_called_once_with(
        [item(), item(1)], max_briefing_items=15, timeout=180)
    pipeline["fetch_article"].assert_called_once_with(
        item(), timeout=20, user_agent=pipeline["config"].settings.user_agent)
    pipeline["write_briefing"].assert_called_once_with(
        [pipeline["fetch_article"].return_value], timeout=180)
    pipeline["post_briefing"].assert_called_once_with(
        "Japanese briefing", webhook_url=WEBHOOK, interval_seconds=0)
    pipeline["state"].mark_posted.assert_called_once_with([item()])
    pipeline["state"].save.assert_called_once_with(retention=100)
    pending = json.loads((pipeline["tmp_path"] / "state/pending_reads.json").read_text())
    assert pending[0]["message_id"] == "123"
    assert pending[0]["channel_id"] == "654321"
    assert pending[0]["kind"] == "news"


def test_empty_selection_marks_noise_seen(pipeline):
    pipeline["select_items"].return_value = []
    assert main() == 0
    pipeline["fetch_article"].assert_not_called()
    pipeline["write_briefing"].assert_not_called()
    pipeline["post_briefing"].assert_not_called()
    pipeline["state"].mark_posted.assert_called_once_with([item(), item(1)])


@pytest.mark.parametrize("stage", ["select_items", "write_briefing"])
@pytest.mark.parametrize("error,code", [(CurationError("safe failure"), 2),
    (MissingAPIKeyError("missing"), 1)])
def test_briefing_failure_never_posts_or_saves(pipeline, stage, error, code):
    pipeline[stage].side_effect = error
    assert main() == code
    pipeline["post_briefing"].assert_not_called()
    pipeline["state"].mark_posted.assert_not_called()
    pipeline["state"].save.assert_not_called()
    assert not (pipeline["tmp_path"] / "state/pending_reads.json").exists()


def test_partial_delivery_leaves_state_unchanged_tracks_success(pipeline):
    pipeline["post_briefing"].return_value = PostResult(success=False, message_ids=["123"])
    assert main() == 2
    pipeline["state"].mark_posted.assert_not_called()
    pipeline["state"].save.assert_not_called()
    pending = json.loads((pipeline["tmp_path"] / "state/pending_reads.json").read_text())
    assert [p["message_id"] for p in pending] == ["123"]


def test_feed_failure_still_delivers_good_news(pipeline):
    good = FeedConfig(name="good", url="https://example.com/rss", category="AI")
    bad = FeedConfig(name="bad", url="https://example.com/bad", category="AI")
    pipeline["config"].feeds = [good, bad]

    def fetch(feed, **kw):
        if feed is bad:
            kw["on_error"](ValueError())
            return []
        return [item()]

    with patch("src.main.fetch_feed", side_effect=fetch):
        assert main() == 0
    pipeline["post_briefing"].assert_called_once()
    pipeline["state"].save.assert_called_once()


def test_invalid_pending_state_prevents_delivery(pipeline):
    path = pipeline["tmp_path"] / "state/pending_reads.json"
    path.parent.mkdir()
    path.write_text("invalid JSON")
    assert main() == 1
    pipeline["select_items"].assert_not_called()
    pipeline["state"].save.assert_not_called()


def test_real_select_fetch_write_content_only_pipeline(monkeypatch, tmp_path, httpx_mock):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")
    cfg = Config(feeds=[FeedConfig(name="Feed", url="https://example.com/rss", category="AI")],
                 settings=Settings(post_interval_seconds=0))
    httpx_mock.add_response(url=API_URL, json={"stop_reason": "end_turn", "content": [
        {"type": "text", "text": json.dumps({"items": [{"id": "0", "url": item().url}]})}]})
    body_text = "A research team has announced a new artificial intelligence system. " * 20
    httpx_mock.add_response(url=item().url, text=f"<article><h1>Research</h1><p>{body_text}</p></article>")
    date = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    # A substantial paragraph makes this a multiple-message delivery.
    briefing = f"📰 **ニュース・ブリーフィング {date}**\n今日はAIが中心。1本。\n\n**🤖 AI**\n\n**1. 新技術の発表**\n" + "研究チームが新技術を発表した。" * 120 + f"\n<{item().url}>"
    httpx_mock.add_response(url=API_URL, json={"stop_reason": "end_turn", "content": [
        {"type": "text", "text": briefing}]})
    httpx_mock.add_response(url=WEBHOOK + "?wait=true", json={"id": "123"})
    httpx_mock.add_response(url=WEBHOOK + "?wait=true", json={"id": "124"})
    with patch("src.main.load_config", return_value=cfg), \
         patch("src.main.fetch_feed", return_value=[item(), item(1)]):
        assert main() == 0
    requests = httpx_mock.get_requests()
    writer = json.loads(requests[2].content)
    articles = json.loads(writer["messages"][0]["content"])["articles"]
    assert "research team" in articles[0]["body"]
    assert "RSS teaser" not in writer["messages"][0]["content"]
    deliveries = [json.loads(r.content) for r in requests[3:]]
    assert len(deliveries) == 2
    assert all("embeds" not in d and len(d["content"]) <= 1900 for d in deliveries)
    assert deliveries[-1]["content"].endswith("読んだら✅を付けてください")
    pending = json.loads((tmp_path / "state/pending_reads.json").read_text())
    assert [p["message_id"] for p in pending] == ["123", "124"]
    posted = json.loads((tmp_path / "state/posted_links.json").read_text())
    assert {p["url"] for p in posted["posted"]} == {item().url}
