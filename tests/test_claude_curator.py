import json
import re

import httpx
import pytest

from src.article_fetcher import Article
from src.claude_curator import (
    API_URL, WRITE_MODEL, DEFAULT_MODEL, CurationError, MissingAPIKeyError,
    SECTION_HEADERS, WRITE_BATCH_SIZE, select_items, write_briefing,
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
    assert "Target: return 2 items" in body["system"]
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
    payload = json.loads(body["messages"][0]["content"])
    assert payload["date"] == DATE
    assert payload["articles"] == [
        {"index": 1, "title": "Title", "url": source().url, "source": "Feed",
         "body": "本文の事実", "body_available": True},
        {"index": 2, "title": "Unavailable", "url": source(1).url, "source": "Paper",
         "body": "", "body_available": False},
    ]
    assert "untrusted" in body["system"]
    assert "本文未確認" in body["system"]
    assert "3–6" in body["system"]
    assert all(header in body["system"] for header in SECTION_HEADERS)


@pytest.mark.parametrize("text", ["not json", "[]", "{}", '{"items":null}',
    '{"items":[{"id":"0"}]}',
    '{"items":[{"id":"wrong","url":"https://example.com/0"}]}',
    '{"items":[{"id":"0","url":"https://example.com/0","summary":"teaser"}]}'])
def test_invalid_selection(httpx_mock, text):
    reply(httpx_mock, text)
    with pytest.raises(CurationError):
        select_items([source()])


def test_select_accepts_rewritten_url_and_tops_up(httpx_mock):
    reply(httpx_mock, '{"items":[{"id":"0","url":"https://rewritten.example/0?x=1"}]}')
    items = [source(i) for i in range(3)]
    result = select_items(items, max_briefing_items=3)
    assert result[0] == source(0)
    assert len(result) == 3
    assert {item.url for item in result} == {source(i).url for i in range(3)}


@pytest.mark.parametrize("text", ["", "just a headline",
    BRIEFING.replace(source().url, "https://invented.test"),
    BRIEFING.replace(f"<{source().url}>", "")])
def test_invalid_briefing(httpx_mock, text):
    reply(httpx_mock, text)
    with pytest.raises(CurationError):
        write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE)



def test_drops_reused_source_link_within_batch(httpx_mock):
    second = "\n\n**2. 別話**\n背景。\n<" + source().url + ">"
    text = BRIEFING + second
    reply(httpx_mock, text)
    result = write_briefing(
        [Article("Title", source().url, "Feed", "body"),
         Article("Other", source(1).url, "Feed", "body")],
        date=DATE,
    )
    assert source().url in result
    assert result.count(source().url) == 1
    assert "**2." not in result


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


def test_accepts_multi_section_briefing(httpx_mock):
    second_story = "\n\n**💻 テクノロジー**\n\n**2. 第二の記事**\n背景。出来事。重要性。\n<https://example.com/1>"
    text = BRIEFING.replace("1本。", "2本。") + second_story
    articles = [Article("Title", source(n).url, "Feed", "body") for n in range(2)]
    reply(httpx_mock, text)
    assert write_briefing(articles, date=DATE) == text


def batch_text(indices, *, start_index=1, total=None):
    header = (f"📰 **ニュース・ブリーフィング {DATE}**\n"
              f"今日は新技術が中心。{total}本。\n\n") if total else ""
    return header + "**🤖 AI**\n\n" + "\n\n".join(
        f"**{number}. 新技術{index}の発表**\n"
        "既存の課題があった。企業が新技術を発表した。業務への導入に影響する。\n"
        f"<{source(index).url}>"
        for number, index in enumerate(indices, start=start_index)
    )


@pytest.mark.parametrize("total", [WRITE_BATCH_SIZE + 1, 50])
def test_batched_write_multiple_calls_and_continuous_numbering(httpx_mock, total):
    articles = [Article(f"Title {i}", source(i).url, "Feed", f"Body {i}")
                for i in range(total)]
    for offset in range(0, total, WRITE_BATCH_SIZE):
        # Simulate a model that restarts numbering in every continuation.
        reply(httpx_mock, batch_text(
            range(offset, min(offset + WRITE_BATCH_SIZE, total)),
            total=total if offset == 0 else None,
        ))
    text = write_briefing(articles, date=DATE, timeout=300)
    assert re.findall(r"^\*\*(\d+)\. ", text, re.MULTILINE) == [
        str(i) for i in range(1, total + 1)]
    assert text.count("📰 **ニュース・ブリーフィング") == 1
    assert f"{total}本。" in text
    requests = httpx_mock.get_requests()
    assert len(requests) == (total + WRITE_BATCH_SIZE - 1) // WRITE_BATCH_SIZE
    for offset, request in zip(range(0, total, WRITE_BATCH_SIZE), requests):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][0]["content"])
        assert body["model"] == WRITE_MODEL
        assert request.extensions["timeout"]["read"] == 300
        assert payload["total_stories"] == total
        assert payload["start_index"] == offset + 1
        assert [a["index"] for a in payload["articles"]] == list(
            range(offset + 1, min(offset + WRITE_BATCH_SIZE, total) + 1))
        if offset:
            assert "NO title line" in body["system"]
            assert payload["previous_category"] == "**🤖 AI**"
        else:
            assert "Begin exactly with:" in body["system"]


def test_batched_write_strips_continuation_title_and_uses_actual_story_count(httpx_mock):
    articles = [Article("Title", source(i).url, "Feed", "Body")
                for i in range(WRITE_BATCH_SIZE + 1)]
    # First batch skips weak evidence, so the next story should be number two.
    reply(httpx_mock, BRIEFING)
    reply(httpx_mock, f"```markdown\n📰 **ニュース・ブリーフィング {DATE}**\n\n"
          + batch_text([WRITE_BATCH_SIZE], start_index=2) + "\n```")
    text = write_briefing(articles, date=DATE)
    assert text.count("ニュース・ブリーフィング") == 1
    assert "2本。" in text
    assert "**2. 新技術" in text
    request = json.loads(httpx_mock.get_requests()[1].content)
    payload = json.loads(request["messages"][0]["content"])
    assert payload["start_index"] == 2


def test_batched_write_drops_link_reused_across_batches(httpx_mock):
    articles = [Article("Title", source(i).url, "Feed", "Body")
                for i in range(WRITE_BATCH_SIZE + 1)]
    reply(httpx_mock, batch_text(range(WRITE_BATCH_SIZE), total=len(articles)))
    continuation = batch_text([WRITE_BATCH_SIZE], start_index=WRITE_BATCH_SIZE + 1)
    reply(httpx_mock, continuation.replace(source(WRITE_BATCH_SIZE).url, source().url))
    text = write_briefing(articles, date=DATE)
    assert text.count(source().url) == 1
    assert f"**{WRITE_BATCH_SIZE + 1}." not in text


def test_batched_write_rejects_invented_links(httpx_mock):
    articles = [Article("Title", source(i).url, "Feed", "Body")
                for i in range(WRITE_BATCH_SIZE + 1)]
    reply(httpx_mock, batch_text(range(WRITE_BATCH_SIZE), total=len(articles)))
    continuation = batch_text([WRITE_BATCH_SIZE], start_index=WRITE_BATCH_SIZE + 1)
    reply(httpx_mock, continuation.replace(
        source(WRITE_BATCH_SIZE).url, "https://invented.test/story"))
    with pytest.raises(CurationError, match="invented"):
        write_briefing(articles, date=DATE)


def test_select_prompt_aims_to_fill_target():
    from src.claude_curator import SELECT_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT
    assert "Do not fill" not in SELECT_SYSTEM_PROMPT
    assert "full target count" in SELECT_SYSTEM_PROMPT
    assert "EVERY supplied article" in WRITE_SYSTEM_PROMPT
    assert "本文未確認" in WRITE_SYSTEM_PROMPT


def test_batched_write_drops_only_story_with_invented_link(httpx_mock):
    articles = [Article("Title", source(i).url, "Feed", "Body")
                for i in range(WRITE_BATCH_SIZE)]
    first = batch_text(range(WRITE_BATCH_SIZE), total=len(articles))
    reply(httpx_mock, first.replace(source(1).url, "https://invented.test/x"))
    text = write_briefing(articles, date=DATE)
    assert "invented.test" not in text
    assert source(0).url in text and source(2).url in text
    assert f"**{WRITE_BATCH_SIZE}." not in text


def test_usage_tracker_accumulates_and_costs(httpx_mock):
    from src.claude_curator import UsageTracker, usage_tracker
    usage_tracker.reset()
    httpx_mock.add_response(url=API_URL, json={
        "stop_reason": "end_turn", "content": [{"type": "text", "text": BRIEFING}],
        "usage": {"input_tokens": 1000, "output_tokens": 500}})
    write_briefing([Article("Title", source().url, "Feed", "body")], date=DATE)
    assert usage_tracker.by_model["claude-sonnet-4-5-20250929"] == {
        "input": 1000, "output": 500, "calls": 1}
    t = UsageTracker()
    t.add("claude-haiku-4-5-20251001", {"input_tokens": 1_000_000, "output_tokens": 0})
    t.add("claude-sonnet-4-5-20250929", {"input_tokens": 0, "output_tokens": 1_000_000,
                                         "cache_read_input_tokens": 0})
    t.add("claude-haiku-4-5-20251001", {"input_tokens": 0, "output_tokens": 200_000})
    assert abs(t.cost_usd() - (1 + 15 + 1)) < 1e-9
    t2 = UsageTracker()
    t2.add("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert abs(t2.cost_usd() - 12) < 1e-9
    line = t.summary_line()
    assert line.startswith("API目安: Haiku in/out 1,000,000/200,000") and line.endswith("約$17.00")
