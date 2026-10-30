import json
from unittest.mock import patch

import httpx
import pytest

from src.discord_poster import REACTION_PROMPT, post_briefing, split_message

WEBHOOK = "https://discord.com/api/webhooks/000/test-token"


def test_split_prefers_paragraphs():
    paragraphs = ["あ" * 900, "い" * 900, "う" * 900]
    chunks = split_message("\n\n".join(paragraphs))
    assert chunks == ["\n\n".join(paragraphs[:2]), paragraphs[2]]


@pytest.mark.parametrize("text", ["a" * 6000, "日本語。" * 1900, "🤖" * 2000,
    "a" * 1899 + "\n\n" + "b" * 1900, "", "a" * 1900])
def test_split_respects_limit_and_preserves_text(text):
    chunks = split_message(text)
    assert all(len(c) <= 1900 and len(c.encode("utf-16-le")) // 2 <= 1900 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


@pytest.mark.parametrize("query", ["", "?thread_id=999", "?wait=false&thread_id=999"])
def test_content_only_and_wait_true(httpx_mock, query):
    httpx_mock.add_response(json={"id": "123"})
    result = post_briefing("📰 briefing", webhook_url=WEBHOOK + query, interval_seconds=0)
    assert result.success
    assert result.message_ids == ["123"]
    request = httpx_mock.get_request()
    payload = json.loads(request.content)
    assert payload == {"content": "📰 briefing\n\n" + REACTION_PROMPT,
                       "allowed_mentions": {"parse": []}}
    assert "embeds" not in payload
    assert request.url.params.get_list("wait") == ["true"]
    if query:
        assert request.url.params["thread_id"] == "999"


def test_reminder_only_last_chunk(httpx_mock):
    for i in range(3):
        httpx_mock.add_response(json={"id": str(i + 1)})
    text = "\n\n".join(["a" * 1800] * 3)
    with patch("time.sleep"):
        result = post_briefing(text, webhook_url=WEBHOOK)
    assert result.message_ids == ["1", "2", "3"]
    contents = [json.loads(r.content)["content"] for r in httpx_mock.get_requests()]
    assert all(len(c) <= 1900 for c in contents)
    assert all(REACTION_PROMPT not in c for c in contents[:-1])
    assert contents[-1].endswith(REACTION_PROMPT)


@pytest.mark.parametrize("statuses,success", [([429, 200], True), ([429, 429], False),
    ([500, 502, 200], True), ([500] * 4, False), ([400], False), ([401], False)])
def test_retry_rules(httpx_mock, statuses, success):
    for status in statuses:
        httpx_mock.add_response(status_code=status, json={"id": "123"}, headers={"Retry-After": "0"})
    with patch("time.sleep"):
        result = post_briefing("briefing", webhook_url=WEBHOOK)
    assert result.success == success
    assert result.message_ids == (["123"] if success else [])


@pytest.mark.parametrize("response", [{}, {"id": None}, {"id": "bad"}, []])
def test_missing_id_is_failure(httpx_mock, response):
    httpx_mock.add_response(json=response)
    result = post_briefing("briefing", webhook_url=WEBHOOK)
    assert not result.success
    assert not result.message_ids


def test_204_is_failure(httpx_mock):
    httpx_mock.add_response(status_code=204)
    assert not post_briefing("briefing", webhook_url=WEBHOOK).success


def test_partial_failure_stops_and_tracks_success(httpx_mock):
    httpx_mock.add_response(json={"id": "123"})
    httpx_mock.add_response(status_code=403)
    result = post_briefing("\n\n".join(["a" * 1800] * 3), webhook_url=WEBHOOK, interval_seconds=0)
    assert not result.success
    assert result.message_ids == ["123"]
    assert len(httpx_mock.get_requests()) == 2


def test_request_error_does_not_leak_url(httpx_mock, caplog):
    httpx_mock.add_exception(httpx.ConnectError(WEBHOOK))
    assert not post_briefing("briefing", webhook_url=WEBHOOK).success
    assert "test-token" not in caplog.text


def test_empty_briefing_is_failure(httpx_mock):
    assert not post_briefing("", webhook_url=WEBHOOK).success
    assert not httpx_mock.get_requests()
