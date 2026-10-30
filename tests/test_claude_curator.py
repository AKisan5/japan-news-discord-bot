import json

import httpx
import pytest

from src.claude_curator import (
    API_URL, DEFAULT_MODEL, CurationError, MissingAPIKeyError, curate_items,
)
from src.feed_fetcher import FeedItem


def source(n=0):
    return FeedItem(f"English {n}", f"https://example.com/{n}", "Source summary", None, "Feed", "AI")


def selection(n=0, importance=4, **kwargs):
    return {"id": str(n), "url": source(n).url, "title_ja": "重要な発表",
            "summary_ja": "新技術が発表されました。業界に影響があります。",
            "category": "技術", "importance": importance, **kwargs}


def reply(httpx_mock, items, **kwargs):
    httpx_mock.add_response(url=API_URL, json={"stop_reason": "end_turn", "content": [
        {"type": "text", "text": json.dumps({"items": items}, ensure_ascii=False)}
    ], **kwargs})


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


def test_request_and_japanese_mapping(httpx_mock):
    reply(httpx_mock, [selection()])
    result = curate_items([source()], timeout=12)
    assert result[0].title == "重要な発表"
    assert result[0].summary == selection()["summary_ja"]
    assert result[0].feed_category == "技術"
    assert result[0].feed_name == "Feed"
    assert result[0].importance == 4
    request = httpx_mock.get_request()
    assert request.headers["x-api-key"] == "test-only-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.extensions["timeout"]["read"] == 12
    body = json.loads(request.content)
    assert body["model"] == DEFAULT_MODEL
    assert json.loads(body["messages"][0]["content"])[0] == {
        "id": "0", "title": "English 0", "summary": "Source summary",
        "url": source().url, "feed": "Feed", "category": "AI",
    }


def test_validation_sort_dedup_and_cap(httpx_mock):
    reply(httpx_mock, [selection(0, 2), selection(1, 5), selection(2, 4),
                       selection(1, 3), selection(0, url="https://invented.test"),
                       selection(2, id="wrong"), selection(0, url=source(2).url)])
    result = curate_items([source(i) for i in range(3)], max_curated=2)
    assert [item.url for item in result] == [source(1).url, source(2).url]
    assert [item.importance for item in result] == [5, 4]


def test_chunking_global_cap_and_model_override(httpx_mock, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "custom-model")
    reply(httpx_mock, [selection(0, 2)])
    reply(httpx_mock, [selection(40, 5)])
    result = curate_items([source(i) for i in range(41)], max_curated=1)
    assert [item.url for item in result] == [source(40).url]
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    for request, count in zip(requests, [40, 1]):
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert len(json.loads(body["messages"][0]["content"])) == count


def test_accepts_markdown_fenced_json(httpx_mock):
    fenced = "```json\n" + json.dumps({"items": [selection()]}, ensure_ascii=False) + "\n```"
    httpx_mock.add_response(json={"stop_reason": "end_turn", "content": [{"type": "text", "text": fenced}]})
    result = curate_items([source()])
    assert result[0].title == "重要な発表"


@pytest.mark.parametrize("text", ["not json", '[]', '{}',
                                     '{"items":null}', '{"items":[{"url":"x"}]}'])
def test_invalid_json(httpx_mock, text):
    httpx_mock.add_response(json={"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]})
    with pytest.raises(CurationError, match="invalid JSON"):
        curate_items([source()])


@pytest.mark.parametrize("fields", [{"importance": 6}, {"importance": True}, {"importance": "4"},
                                     {"title_ja": " "}, {"summary_ja": ""}, {"category": ""}])
def test_invalid_fields(httpx_mock, fields):
    reply(httpx_mock, [selection(**fields)])
    with pytest.raises(CurationError):
        curate_items([source()])


def test_missing_key(monkeypatch, httpx_mock):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(MissingAPIKeyError):
        curate_items([source()])
    assert httpx_mock.get_requests() == []


def test_empty_inputs_and_selection(httpx_mock):
    assert curate_items([]) == []
    reply(httpx_mock, [])
    assert curate_items([source()]) == []


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_safe(httpx_mock, status):
    httpx_mock.add_response(status_code=status, text="test-only-key")
    with pytest.raises(CurationError) as exc:
        curate_items([source()])
    assert "test-only-key" not in str(exc.value)


def test_timeout_safe(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("test-only-key"))
    with pytest.raises(CurationError, match="request failed"):
        curate_items([source()])


def test_truncated_response(httpx_mock):
    reply(httpx_mock, [], stop_reason="max_tokens")
    with pytest.raises(CurationError, match="did not complete"):
        curate_items([source()])


def test_later_chunk_failure_discards_all(httpx_mock):
    reply(httpx_mock, [selection()])
    httpx_mock.add_response(status_code=503)
    with pytest.raises(CurationError):
        curate_items([source(i) for i in range(41)])
