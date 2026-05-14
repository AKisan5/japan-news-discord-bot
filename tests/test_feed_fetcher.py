from __future__ import annotations

import pytest
import httpx

from src.config_loader import FeedConfig
from src.feed_fetcher import fetch_feed, _clean_html

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Test Article 1</title>
      <link>https://example.com/article1</link>
      <description>&lt;p&gt;This is a &lt;b&gt;test&lt;/b&gt; summary.&lt;/p&gt;</description>
      <pubDate>Wed, 14 May 2026 01:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Test Article 2</title>
      <link>https://example.com/article2</link>
      <description>Another summary without a date.</description>
    </item>
  </channel>
</rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom Entry 1</title>
    <link href="https://example.com/atom1"/>
    <summary>Atom summary text.</summary>
    <published>2026-05-14T01:00:00Z</published>
  </entry>
</feed>"""


@pytest.fixture
def feed_config() -> FeedConfig:
    return FeedConfig(
        name="Test Feed",
        url="https://example.com/feed.rss",
        category="テスト",
    )


def test_fetch_rss_success(httpx_mock, feed_config):
    httpx_mock.add_response(text=RSS_SAMPLE)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert len(items) == 2
    assert items[0].title == "Test Article 1"
    assert items[0].url == "https://example.com/article1"
    assert items[0].feed_name == "Test Feed"
    assert items[0].feed_category == "テスト"


def test_fetch_atom_success(httpx_mock, feed_config):
    httpx_mock.add_response(text=ATOM_SAMPLE)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert len(items) == 1
    assert items[0].title == "Atom Entry 1"
    assert items[0].url == "https://example.com/atom1"


def test_html_tag_removal(httpx_mock, feed_config):
    httpx_mock.add_response(text=RSS_SAMPLE)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    summary = items[0].summary
    assert "<p>" not in summary
    assert "<b>" not in summary
    assert "test" in summary


def test_clean_html():
    assert _clean_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _clean_html("No tags here") == "No tags here"
    assert _clean_html("  multiple   spaces  ") == "multiple spaces"


def test_missing_published_parsed(httpx_mock, feed_config):
    httpx_mock.add_response(text=RSS_SAMPLE)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    no_date = [i for i in items if i.url == "https://example.com/article2"]
    assert len(no_date) == 1
    assert no_date[0].published_at is None


def test_published_parsed_is_utc(httpx_mock, feed_config):
    from datetime import timezone
    httpx_mock.add_response(text=RSS_SAMPLE)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    dated = [i for i in items if i.published_at is not None]
    assert len(dated) >= 1
    assert dated[0].published_at.tzinfo == timezone.utc


def test_sorted_published_desc(httpx_mock, feed_config):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
  <item><title>Old</title><link>https://example.com/old</link>
    <pubDate>Mon, 12 May 2026 01:00:00 +0000</pubDate></item>
  <item><title>New</title><link>https://example.com/new</link>
    <pubDate>Wed, 14 May 2026 01:00:00 +0000</pubDate></item>
</channel></rss>"""
    httpx_mock.add_response(text=rss)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert items[0].title == "New"
    assert items[1].title == "Old"


def test_none_dates_sorted_last(httpx_mock, feed_config):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
  <item><title>No date</title><link>https://example.com/nodate</link></item>
  <item><title>Has date</title><link>https://example.com/dated</link>
    <pubDate>Wed, 14 May 2026 01:00:00 +0000</pubDate></item>
</channel></rss>"""
    httpx_mock.add_response(text=rss)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert items[0].title == "Has date"
    assert items[-1].title == "No date"


def test_max_items_limit(httpx_mock, feed_config):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
""" + "\n".join(
        f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
        for i in range(10)
    ) + """
</channel></rss>"""
    httpx_mock.add_response(text=rss)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=3)
    assert len(items) == 3


def test_skips_entry_without_link(httpx_mock, feed_config):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
  <item><title>No link</title></item>
  <item><title>Has link</title><link>https://example.com/ok</link></item>
</channel></rss>"""
    httpx_mock.add_response(text=rss)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert len(items) == 1
    assert items[0].url == "https://example.com/ok"


def test_404_returns_empty(httpx_mock, feed_config):
    httpx_mock.add_response(status_code=404)
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert items == []


def test_timeout_returns_empty(httpx_mock, feed_config):
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))
    items = fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10)
    assert items == []


def test_on_error_callback_called(httpx_mock, feed_config):
    httpx_mock.add_response(status_code=500)
    errors = []
    fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10, on_error=errors.append)
    assert len(errors) == 1


def test_no_error_callback_on_success(httpx_mock, feed_config):
    httpx_mock.add_response(text=RSS_SAMPLE)
    errors = []
    fetch_feed(feed_config, timeout=10, user_agent="test-agent", max_items=10, on_error=errors.append)
    assert len(errors) == 0
