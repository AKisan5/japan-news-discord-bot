from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.watchers import common, even, llm, run, sui
from src.watchers.common import JST, Collected, Evidence, WatcherState, normalize_url
from src.watchers.llm import Briefing, Item

NOW = datetime(2026, 11, 4, 8, 6, tzinfo=JST)
SINCE = datetime(2026, 11, 3, 8, 16, tzinfo=JST)


def _item(**kw) -> Item:
    base = dict(section="", title="見出し", meta="11/03 19:41 JST", what="何か", why="", evidence_ids=["0"],
                unofficial_firmware=False, unverified=False)
    base.update(kw)
    return Item(**base)


def test_normalize_url_unifies_twitter_and_tracking():
    assert normalize_url("https://twitter.com/SuiNetwork/status/1?s=20") == "https://x.com/SuiNetwork/status/1"
    assert normalize_url("<https://a.example/x/?utm_source=a>") == "https://a.example/x"


def test_state_roundtrip_and_retention(tmp_path):
    st = WatcherState("t", root=tmp_path)
    st.mark_reported(["https://x.com/a/status/1"], "2026-11-04")
    st.reported["https://old.example"] = "2026-09-01"
    st.set_seen("blog", ["/a", "/b"])
    st.save("2026-11-04")
    again = WatcherState("t", root=tmp_path)
    assert again.is_reported("https://twitter.com/a/status/1")
    assert "https://old.example" not in again.reported
    assert again.seen("blog") == {"/a", "/b"}


def test_since_window_uses_last_post_and_caps_gap(tmp_path):
    st = WatcherState("t", root=tmp_path)
    assert common.since_window(st, NOW) == NOW - timedelta(hours=26)
    st.data["last_posted_at"] = (NOW - timedelta(hours=48)).isoformat()
    assert common.since_window(st, NOW) == NOW - timedelta(hours=48)
    st.data["last_posted_at"] = (NOW - timedelta(days=10)).isoformat()
    assert common.since_window(st, NOW) == NOW - timedelta(hours=72)


def test_even_render_matches_grok_format():
    ev = [Evidence(source="公式サポート（新規）", title="KB", url="https://support.example/kb"),
          Evidence(source="Reddit（RSS 抜粋）", title="r", url="https://reddit.example/r", unverified=True),
          Evidence(source="GitHub", title="fc", url="https://github.com/x/faceclaw")]
    b = Briefing(lead="今朝の中心は KB である。", items=[
        _item(title="公式サポート新規：KB", what="手順記事が公開された。", why="会議で使う。", evidence_ids=["0"]),
        _item(title="Reddit：通知が出ない", meta="11/02", what="報告。", evidence_ids=["1"], unverified=True),
        _item(title="Faceclaw 0.8.6", meta="", what="更新。", evidence_ids=["2"], unofficial_firmware=True),
        _item(title="根拠なし", evidence_ids=["9"]),  # unknown evidence id -> dropped
    ])
    text = even.render(b, ev, SINCE, NOW)
    assert text.startswith("# Even G2 新着（2026-11-04 08:06 JST・前回 11/03 08:16 JST 以降）")
    assert "## 1. 公式サポート新規：KB（11/03 19:41 JST）\n- 何が変わったか: 手順記事が公開された。\n- どんな場面で役立つか: 会議で使う。\n- https://support.example/kb" in text
    assert "- https://reddit.example/r （未確認）" in text
    assert "非公式改造ファームウェア" in text
    assert "## 3. Faceclaw 0.8.6\n" in text
    assert "根拠なし" not in text


def test_even_render_empty_when_no_items():
    assert even.render(Briefing(lead="x", items=[]), [], SINCE, NOW) == ""


def test_sui_render_sections_in_fixed_order():
    ev = [Evidence(source="X @SuiNetwork", title="t", url="https://x.com/SuiNetwork/status/1"),
          Evidence(source="DefiLlama", title="t", url="https://defillama.com/stablecoins/Sui"),
          Evidence(source="DefiLlama TVL", title="t", url="https://defillama.com/chain/Sui")]
    b = Briefing(lead="", items=[
        _item(section="stable", title="ステーブルコイン残高", meta="", what="約4.68億ドル。", evidence_ids=["1", "2"]),
        _item(section="eco", title="Samsung Wallet", meta="@SuiNetwork、2026/11/02 19:52 JST",
              what="公式が告知した。", evidence_ids=["0"]),
    ])
    text = sui.render(b, ev, SINCE, NOW)
    assert text.splitlines()[0] == "# SUIデイリー 2026/11/04 (JST)"
    assert text.index("🌊 エコシステム") < text.index("💵 ステーブルコイン")
    assert "・Samsung Wallet（@SuiNetwork、2026/11/02 19:52 JST）: 公式が告知した。\n<https://x.com/SuiNetwork/status/1>" in text
    assert "<https://defillama.com/stablecoins/Sui>\n<https://defillama.com/chain/Sui>" in text


def test_sui_alerts_thresholds_and_suppression(tmp_path):
    st = WatcherState("s", root=tmp_path)
    c = Collected(data={"price": {"ch24": -12.0, "ch24_actual": -11.0, "ch7d": -5},
                        "tvl": {"d1": -16.0},
                        "status": {"indicator": "none", "bad": [], "incidents": [], "incident_ids": []}},
                  evidence=[Evidence(source="Sui 公式ブログ", title="Sui ETF approved", url="https://sui.io/b1"),
                            Evidence(source="Sui 公式ブログ", title="Post-mortem: exploit", url="https://sui.io/b2"),
                            Evidence(source="Sui 公式ブログ", title="Community recap", url="https://sui.io/b3")])
    keys = [k for k, _r, _u in sui.check_alerts(c, st, NOW)]
    assert keys == ["A1", "A3", "A5:https://sui.io/b1", "A6:https://sui.io/b2"]
    st.data["alerts"]["A1"] = (NOW - timedelta(hours=10)).isoformat()
    st.data["alerts"]["A3"] = (NOW - timedelta(hours=50)).isoformat()
    keys = [k for k, _r, _u in sui.check_alerts(c, st, NOW)]
    assert "A1" not in keys and "A3" in keys


def test_sui_alert_status_incident(tmp_path):
    c = Collected(data={"status": {"indicator": "major", "description": "Major Outage", "bad": ["Mainnet"],
                                   "incidents": ["RPC"], "incident_ids": ["abc"]}})
    alerts = sui.check_alerts(c, WatcherState("s", root=tmp_path), NOW)
    assert alerts[0][0] == "A4:abc"


def test_drop_dead_links(monkeypatch):
    ev = [Evidence(source="a", title="a", url="https://ok.example"),
          Evidence(source="b", title="b", url="https://dead.example")]
    b = Briefing(lead="", items=[_item(evidence_ids=["0"]), _item(evidence_ids=["1"])])
    monkeypatch.setattr(run, "url_alive", lambda u: "dead" not in u)
    assert run._drop_dead_links(b, ev) == ["https://dead.example"]
    assert "dead.example" not in even.render(b, ev, SINCE, NOW)


def test_x_evidence_filters_old_and_reported(monkeypatch, tmp_path):
    st = WatcherState("e", root=tmp_path)
    st.mark_reported(["https://x.com/a/status/3"], "2026-11-03")
    monkeypatch.setattr(llm, "discover_x_urls", lambda **kw: [
        "https://x.com/a/status/1", "https://x.com/a/status/2", "https://x.com/a/status/3"])
    posts = {
        "1": Evidence(source="X @a", title="new", url="https://x.com/a/status/1",
                      published_at=(SINCE + timedelta(hours=1)).isoformat()),
        "2": Evidence(source="X @a", title="old", url="https://x.com/a/status/2",
                      published_at=(SINCE - timedelta(hours=1)).isoformat()),
    }
    monkeypatch.setattr(run, "fetch_x_post", lambda url: posts.get(url.rsplit("/", 1)[1]))
    got = run._x_evidence(even, st, SINCE)
    assert [e.title for e in got] == ["new"]


def test_run_skips_post_when_no_fresh_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sui, "collect", lambda state, since, light=False: Collected(
        evidence=[Evidence(source="CoinGecko", title="p", url="https://www.coingecko.com/en/coins/sui")]))
    monkeypatch.setattr(run, "_x_evidence", lambda *a: [])
    called = []
    monkeypatch.setattr(llm, "write_briefing", lambda **kw: called.append(1))
    assert run.run("sui", dry_run=False, channel="news") == 0
    assert called == []


def test_run_posts_and_records_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ev = [Evidence(source="公式ブログ", title="b", url="https://evenrealities.example/blog/1")]
    monkeypatch.setattr(even, "collect", lambda state, since: Collected(evidence=list(ev), seen={"blog": ["/1"]}))
    monkeypatch.setattr(run, "_x_evidence", lambda *a: [])
    monkeypatch.setattr(llm, "write_briefing", lambda **kw: Briefing(lead="中心。", items=[_item(evidence_ids=["0"])]))
    monkeypatch.setattr(run, "url_alive", lambda u: True)
    posted = []
    monkeypatch.setattr(run, "post_to_discord", lambda text, **kw: posted.append((text, kw)) or ["1"])
    assert run.run("even", dry_run=False, channel="news") == 0
    assert posted[0][1] == {"username": "Even G2ウォッチャー", "channel": "news"}
    st = WatcherState("even")
    assert st.is_reported("https://evenrealities.example/blog/1")
    assert st.seen("blog") == {"/1"}
    # second trigger the same day (GitHub schedule backup) does nothing
    assert run.run("even", dry_run=False, channel="news") == 0
    assert len(posted) == 1


def test_run_test_channel_advances_state_but_dry_run_does_not(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ev = [Evidence(source="公式ブログ", title="b", url="https://evenrealities.example/blog/1")]
    monkeypatch.setattr(even, "collect", lambda state, since: Collected(evidence=list(ev)))
    monkeypatch.setattr(run, "_x_evidence", lambda *a: [])
    monkeypatch.setattr(llm, "write_briefing", lambda **kw: Briefing(lead="", items=[_item()]))
    monkeypatch.setattr(run, "url_alive", lambda u: True)
    sent = []
    monkeypatch.setattr(run, "post_to_discord", lambda text, **kw: sent.append(kw) or ["1"])
    assert run.run("even", dry_run=True, channel="test") == 0
    assert sent == [] and not (tmp_path / "state/watchers/even.json").exists()
    assert run.run("even", dry_run=False, channel="test") == 0
    assert sent == [{"username": "Even G2ウォッチャー", "channel": "test"}]
    assert WatcherState("even").is_reported("https://evenrealities.example/blog/1")


def test_writer_failure_returns_2(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(even, "collect", lambda state, since: Collected(
        evidence=[Evidence(source="公式ブログ", title="b", url="https://e.example/1")]))
    monkeypatch.setattr(run, "_x_evidence", lambda *a: [])

    def boom(**kw):
        raise llm.WatcherLLMError("writing refused")
    monkeypatch.setattr(llm, "write_briefing", boom)
    assert run.run("even", dry_run=False, channel="news") == 2


@pytest.mark.parametrize("value,expected", [
    ("Mon Nov 02 13:00:03 +0000 2026", "2026-11-02T13:00:03+00:00"),
    ("2026-11-02T10:41:00Z", "2026-11-02T10:41:00+00:00"),
    ("", None),
])
def test_parse_dt(value, expected):
    got = common.parse_dt(value)
    assert (got.isoformat() if got else None) == expected
