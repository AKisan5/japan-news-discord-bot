import json

import httpx
import pytest

from src.article_fetcher import Article
from src.claude_curator import (
    API_URL, WRITE_MODEL, DEFAULT_MODEL, CurationError, MissingAPIKeyError,
    SECTION_HEADERS, select_items, write_briefing,
)
from src.feed_fetcher import FeedItem

DATE = "2026-10-30"
BRIEFING = f"""📰 **ニュース・ブリーフィング {DATE}**
今日は新技術が中心。1本。

**🤖 AI**

**1. 新技術の発表**
既存の課題があった。企業が新技術を発表した。業務への導入に影響する。
<https://example.com/0>"""


def source(n=0):
    return FeedItem(f"English {n}", f"https://example.com/{n}", "RSS teaser", None, "Feed", "AI")


def reply(httpx_mock, text, **kwargs):
    httpx_mock.add_response(url=API_URL, json={"stop_reason": "end_turn", "content": [
        {"type": "text", "text": text}], **kwargs})


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


def test_selection_metadata_only_and_global_cap(httpx_mock, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "custom-model")
    items = [source(i) for i in range(80)]
    reply(httpx_mock, json.dumps({"items": [
        {"id": str(i), "url": source(i).url} for i in [65, 65, 0, 79]
    ]}))
    result = select_items(items, max_briefing_items=2, timeout=12)
    assert result == [items[65], items[0]]
    request = httpx_mock.get_request()
    assert request.extensions["timeout"]["read"] == 12
    assert request.headers["x-api-key"] == "test-only-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "custom-model"
    assert "at most 2 items" in body["system"]
    payload = json.loads(body["messages"][0]["content"])
    assert len(payload) == 80  # One global selection, including late feeds.
    assert payload[0] == {"id": "0", "title": "English 0", "rss_summary": "RSS teaser",
                          "url": source().url, "feed": "Feed"}


def test_fence_stripping_for_both_calls(httpx_mock):
    reply(httpx_mock, '```json\n{"items":[{"id":"0","url":"https://example.com/0"}]}\n```')
    assert select_items([source()]) == [source()]
    reply(httpx_mock, f"```markdown\n{BRIEFING}\n```")
    assert write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE) == BRIEFING


def test_write_receives_bodies_and_missing_body_instruction(httpx_mock):
    reply(httpx_mock, BRIEFING)
    articles = [Article("Title", source().url, "Feed", "本文の事実"),
                Article("Unavailable", source(1).url, "Paper", "")]
    assert write_briefing(articles, date=DATE) == BRIEFING
    body = json.loads(httpx_mock.get_request().content)
    assert body["model"] == WRITE_MODEL
    assert json.loads(body["messages"][0]["content"]) == {
        "date": DATE, "articles": [vars(a) for a in articles]}
    assert "untrusted" in body["system"]
    assert "本文未確認" in body["system"]
    assert "3–6" in body["system"]
    assert all(header in body["system"] for header in SECTION_HEADERS)


@pytest.mark.parametrize("text", ["not json", "[]", "{}", '{"items":null}',
    '{"items":[{"id":"0"}]}',
    '{"items":[{"id":"0","url":"https://invented.test"}]}',
    '{"items":[{"id":"wrong","url":"https://example.com/0"}]}',
    '{"items":[{"id":"0","url":"https://example.com/0","summary":"teaser"}]}'])
def test_invalid_selection(httpx_mock, text):
    reply(httpx_mock, text)
    with pytest.raises(CurationError):
        select_items([source()])


@pytest.mark.parametrize("text", ["", "just a headline", BRIEFING.replace("1本。", "2本。"),
    BRIEFING.replace("**1.", "**2."), BRIEFING.replace("**🤖 AI**", "**Other**"),
    BRIEFING.replace(source().url, "https://invented.test"),
    BRIEFING.replace(DATE, "2026-01-01"), BRIEFING.replace(f"<{source().url}>", "")])
def test_invalid_briefing(httpx_mock, text):
    reply(httpx_mock, text)
    with pytest.raises(CurationError):
        write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE)


def test_empty_inputs_and_selection(httpx_mock):
    assert select_items([]) == []
    reply(httpx_mock, '{"items":[]}')
    assert select_items([source()]) == []


@pytest.mark.parametrize("operation", [lambda: select_items([source()]),
    lambda: write_briefing([Article("Title", source().url, "Feed", "body")])])
def test_missing_key(monkeypatch, httpx_mock, operation):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(MissingAPIKeyError):
        operation()
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_safe(httpx_mock, status):
    httpx_mock.add_response(status_code=status, text="test-only-key")
    with pytest.raises(CurationError) as exc:
        select_items([source()])
    assert "test-only-key" not in str(exc.value)


def test_timeout_safe(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("test-only-key"))
    with pytest.raises(CurationError, match="request failed"):
        select_items([source()])


def test_truncated_response(httpx_mock):
    reply(httpx_mock, BRIEFING, stop_reason="max_tokens")
    with pytest.raises(CurationError, match="did not complete"):
        write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE)


def test_normal_markdown_spacing_after_title(httpx_mock):
    text = BRIEFING.replace(f"{DATE}**\n", f"{DATE}**\n\n")
    reply(httpx_mock, text)
    assert write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE) == text


def test_sections_are_in_fixed_order(httpx_mock):
    second_story = "\n\n**💻 テクノロジー**\n\n**2. 第二の記事**\n背景。出来事。重要性。\n<https://example.com/1>"
    text = BRIEFING.replace("1本。", "2本。") + second_story
    articles = [Article("Title", source(n).url, "Feed", "body") for n in range(2)]
    reply(httpx_mock, text)
    assert write_briefing(articles, date=DATE) == text
    reversed_sections = text.replace("**🤖 AI**", "**TEMP**").replace("**💻 テクノロジー**", "**🤖 AI**").replace("**TEMP**", "**💻 テクノロジー**")
    reply(httpx_mock, reversed_sections)
    with pytest.raises(CurationError):
        write_briefing(articles, date=DATE)
