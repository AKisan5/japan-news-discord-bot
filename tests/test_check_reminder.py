import json

import httpx
import pytest

from src.check_reminder import API_BASE, build_summary_line, check_pending, has_human_check, main
from src.pending_reads import DEFAULT_GUILD_ID, PendingReadStore


@pytest.fixture
def store(tmp_path):
    pending = PendingReadStore(tmp_path / "pending.json")
    pending.append(["123"], channel_id="456")
    pending.save()
    return pending


def message_url(entry):
    return f"{API_BASE}/channels/{entry['channel_id']}/messages/{entry['message_id']}"


def add_check(httpx_mock, entry, users):
    url = message_url(entry)
    httpx_mock.add_response(url=url, json={"id": entry["message_id"]})
    httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=0", json=users)


def test_summary_line(store):
    assert build_summary_line(store.entries, DEFAULT_GUILD_ID) == (
        "🔔 未読ニュースが 1 件あります。各投稿に✅を付けると消えます。\n"
        "https://discord.com/channels/1173405237390868610/456/123"
    )


@pytest.mark.parametrize("users", [[{"id": "7", "bot": False}], [{"id": "7"}],
                                  [{"id": "8", "bot": True}, {"id": "9"}]])
def test_human_check_removes_pending(httpx_mock, store, users):
    add_check(httpx_mock, store.entries[0], users)
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 0
    assert store.entries == []
    assert json.loads(store.path.read_text()) == []
    assert all(r.method == "GET" for r in httpx_mock.get_requests())


@pytest.mark.parametrize("users", [[], [{"id": "8", "bot": True}]])
def test_unread_reminded_once_per_run_and_retained(httpx_mock, store, users):
    entry = store.entries[0].copy()
    store.entries.append(entry.copy())  # Even duplicate pending records cannot double-remind.
    for _ in range(2):
        add_check(httpx_mock, entry, users)
        httpx_mock.add_response(method="POST", url=f"{API_BASE}/channels/456/messages", json={"id": "999"})
        with httpx.Client() as client:
            assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 0
    assert store.entries == [entry, entry]
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 2  # One reminder per run; no daily suppression.
    for post in posts:
        payload = json.loads(post.content)
        assert "message_reference" not in payload
        assert payload["content"] == build_summary_line([entry], DEFAULT_GUILD_ID)
        assert payload["allowed_mentions"]["replied_user"] is False


@pytest.mark.parametrize("failure", ["message", "reactions", "send"])
def test_api_failures_keep_pending(httpx_mock, store, failure):
    entry = store.entries[0].copy()
    url = message_url(entry)
    httpx_mock.add_response(url=url, status_code=403 if failure == "message" else 200, json={})
    if failure != "message":
        httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=0",
                                status_code=500 if failure == "reactions" else 200, json=[])
    if failure == "send":
        httpx_mock.add_response(method="POST", status_code=403)
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 1
    assert store.entries == [entry]
    assert json.loads(store.path.read_text()) == [entry]


def test_reaction_pagination_finds_human(httpx_mock, store):
    url = message_url(store.entries[0])
    httpx_mock.add_response(url=url, json={})
    httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=0",
                            json=[{"id": str(i), "bot": True} for i in range(1, 101)])
    httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=0&after=100",
                            json=[{"id": "101"}])
    with httpx.Client() as client:
        assert has_human_check(client, store.entries[0])


def test_burst_human_check(httpx_mock, store):
    url = message_url(store.entries[0])
    httpx_mock.add_response(url=url, json={"reactions": [
        {"emoji": {"name": "✅"}, "count_details": {"burst": 1}}
    ]})
    httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=0", json=[])
    httpx_mock.add_response(url=url + "/reactions/%E2%9C%85?limit=100&type=1", json=[{"id": "7"}])
    with httpx.Client() as client:
        assert has_human_check(client, store.entries[0])


def test_pending_oldest_first(httpx_mock, store):
    store.entries[0]["posted_at"] = "2026-10-30T00:00:00+00:00"
    store.append(["124"], channel_id="456")
    store.entries[1]["posted_at"] = "2026-10-29T00:00:00+00:00"
    for entry in reversed(store.entries):
        add_check(httpx_mock, entry, [])
    httpx_mock.add_response(method="POST", json={"id": "999"})
    with httpx.Client() as client:
        check_pending(client, store, guild_id=DEFAULT_GUILD_ID)
    posts = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 1
    assert "message_reference" not in posts[0]
    content = posts[0]["content"]
    assert "未読ニュースが 2 件" in content
    assert content.index("/456/124") < content.index("/456/123")
    requests = httpx_mock.get_requests()
    assert [r.url.path.split("/")[-1] for r in requests if r.method == "GET"
            and "/reactions/" not in r.url.path] == ["124", "123"]
    assert requests[-1].method == "POST"


def test_pending_roundtrip_deduplication(store):
    store.append(["123", "124", "124"], channel_id="456")
    store.save()
    loaded = PendingReadStore(store.path)
    loaded.load()
    assert loaded.entries == store.entries
    assert len(loaded.entries) == 2
    assert all(e["kind"] == "news" for e in loaded.entries)
    assert not list(store.path.parent.glob("*.tmp"))


def test_corrupt_pending_state_is_not_reset(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        PendingReadStore(path).load()
    assert path.read_text() == "broken"


def test_missing_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert main() == 1


def test_token_only_in_authorization(httpx_mock, monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-only-secret")
    pending = PendingReadStore()
    pending.append(["123"], channel_id="456")
    pending.save()
    add_check(httpx_mock, pending.entries[0], [])
    httpx_mock.add_response(method="POST", status_code=403, text="test-only-secret")
    assert main() == 2
    assert "test-only-secret" not in caplog.text
    for request in httpx_mock.get_requests():
        assert request.headers["Authorization"] == "Bot test-only-secret"
        assert "test-only-secret" not in str(request.url)
        assert b"test-only-secret" not in request.content
    assert "test-only-secret" not in pending.path.read_text()


def test_rate_limit_retries_without_double_reminder(httpx_mock, store, mocker):
    add_check(httpx_mock, store.entries[0], [])
    httpx_mock.add_response(method="POST", status_code=429, json={"retry_after": 0.1})
    httpx_mock.add_response(method="POST", status_code=200, json={"id": "999"})
    sleep = mocker.patch("src.check_reminder.time.sleep")
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 0
    sleep.assert_called_once_with(0.1)
    assert len(store.entries) == 1


def test_one_failed_entry_does_not_block_others(httpx_mock, store):
    store.append(["124"], channel_id="456")
    httpx_mock.add_response(url=message_url(store.entries[0]), status_code=404)
    add_check(httpx_mock, store.entries[1], [{"id": "7"}])
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 1
    assert [e["message_id"] for e in store.entries] == ["123"]
    assert all(r.method == "GET" for r in httpx_mock.get_requests())


@pytest.mark.parametrize("count", [3, 5])
def test_unread_summary_count_and_link_limit(httpx_mock, store, count):
    store.append([str(124 + i) for i in range(count - 1)], channel_id="456")
    store.save()
    original = store.entries[:]
    for entry in store.entries:
        add_check(httpx_mock, entry, [])
    httpx_mock.add_response(method="POST", json={"id": "999"})
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 0
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    assert "message_reference" not in payload
    content = payload["content"]
    assert f"未読ニュースが {count} 件" in content
    assert content.count("https://discord.com/channels/") == 3
    if count > 3:
        assert content.endswith(f"ほか {count - 3} 件")
    assert store.entries == original
    assert json.loads(store.path.read_text()) == original


def test_summary_failure_keeps_all_unread_and_continues_channels(httpx_mock, store):
    store.append(["124"], channel_id="456")
    store.append(["123"], channel_id="789")
    store.save()
    original = store.entries[:]
    for entry in store.entries:
        add_check(httpx_mock, entry, [])
    httpx_mock.add_response(method="POST", url=f"{API_BASE}/channels/456/messages", status_code=403)
    httpx_mock.add_response(method="POST", url=f"{API_BASE}/channels/789/messages", json={"id": "999"})
    with httpx.Client() as client:
        assert check_pending(client, store, guild_id=DEFAULT_GUILD_ID) == 1
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 2
    for post, count in zip(posts, [2, 1]):
        payload = json.loads(post.content)
        assert f"未読ニュースが {count} 件" in payload["content"]
        assert "message_reference" not in payload
    assert store.entries == original
    assert json.loads(store.path.read_text()) == original
