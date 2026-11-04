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
    st.data["last_posted_at"] = (NOW - timedelta(hours=13)).isoformat()  # an extra post must not shrink the window
    assert common.since_window(st, NOW) == NOW - timedelta(hours=26)
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
    assert text.startswith("# Even G2 新着（2026-11-04 08:06 JST・11/03 08:16 JST 以降）")
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


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_even_hub_new_update_and_unknown(monkeypatch, tmp_path):
    recent, old = (NOW - timedelta(hours=3)).isoformat(), (NOW - timedelta(days=9)).isoformat()
    disc = {"data": {"plugins": [{"package_id": "a.known"}], "rankings": {"fresh": [{"package_id": "b.fresh"}],
                                                                       "trending": [{"package_id": "a.known"}]},
                     "config": {"sections": [{"entries": [{"package_id": "c.other"}]},
                                             {"folders": [{"featured_items": [{"package_id": "d.old"}]}]}]}}}
    details = {"a.known": ("1.1.0", recent), "b.fresh": ("1.0.0", recent), "c.other": ("2.0.0", recent),
               "d.old": ("3.0.0", old)}

    def fake_get(url, params=None, **kw):
        if url.endswith("/discovery"):
            return _Resp(disc)
        ver, when = details[params["package_id"]]
        return _Resp({"data": {"name": params["package_id"], "creator_name": "dev", "tagline": "t",
                               "version": {"version": ver, "updated_at": when, "changelog": "c"}}})

    monkeypatch.setattr(even, "get", fake_get)
    st = WatcherState("even", root=tmp_path)
    st.set_seen("hub", ["a.known@1.0.0", "d.old@2.0.0"])
    out = Collected()
    even._hub(out, st, NOW - timedelta(hours=26))
    kinds = {e.url.rsplit("/", 1)[1]: e.source for e in out.evidence if e.source != even.WEEKLY_SOURCE}
    assert kinds == {"a.known": "Even Hub アプリ更新", "b.fresh": "Even Hub 掲載",
                     "c.other": "Even Hub 掲載"}  # d.old: version outside the window
    assert "Fresh 欄" in next(e.summary for e in out.evidence if e.url.endswith("b.fresh"))
    assert "a.known@1.1.0" in out.seen["hub"]
    assert all(e.url.startswith("https://hub.evenrealities.com/catalogue/") for e in out.evidence)


def test_sui_media_topics_window_and_cap(monkeypatch):
    fresh_t, old_t = (NOW - timedelta(hours=2)).isoformat(), (NOW - timedelta(days=3)).isoformat()
    rows = ([{"title": f"Tether USDT news {i}", "link": f"https://m.example/s{i}", "published": fresh_t,
              "summary": ""} for i in range(20)]
            + [{"title": "Sui DeepBook upgrade", "link": "https://m.example/sui", "published": fresh_t, "summary": ""},
               {"title": "Coinbase x402 adds agents", "link": "https://m.example/x402", "published": fresh_t,
                "summary": ""},
               {"title": "Bitcoin price", "link": "https://m.example/btc", "published": fresh_t, "summary": ""},
               {"title": "Circle USDC old", "link": "https://m.example/old", "published": old_t, "summary": ""}])
    monkeypatch.setattr(sui, "MEDIA_FEEDS", [("M1", "u1"), ("M2", "u2")])
    monkeypatch.setattr(sui, "feed_entries", lambda url: rows)  # same rows in both feeds -> deduped by link
    out = Collected()
    sui._media(out, NOW - timedelta(hours=26))
    srcs = [e.source for e in out.evidence]
    assert srcs.count("M1（報道・stable）") == sui.MEDIA_CAP["stable"]
    assert "M1（報道・sui）" in srcs and "M1（報道・agentpay）" in srcs
    urls = {e.url for e in out.evidence}
    assert "https://m.example/btc" not in urls and "https://m.example/old" not in urls
    assert not any(s.startswith("M2") for s in srcs)


def test_sui_stable_market_summary(monkeypatch):
    assets = {"peggedAssets": [
        {"symbol": "USDT", "pegType": "peggedUSD", "circulating": {"peggedUSD": 178e9},
         "circulatingPrevDay": {"peggedUSD": 178.5e9}, "circulatingPrevWeek": {"peggedUSD": 181.7e9}},
        {"symbol": "USDC", "pegType": "peggedUSD", "circulating": {"peggedUSD": 70.9e9},
         "circulatingPrevDay": {"peggedUSD": 71.2e9}, "circulatingPrevWeek": {"peggedUSD": 71.3e9}},
        {"symbol": "JPYC", "pegType": "peggedJPY", "circulating": {"peggedJPY": 18_748_509},
         "circulatingPrevDay": {"peggedJPY": 18_912_062}, "circulatingPrevWeek": {"peggedJPY": 19_456_597}}]}
    monkeypatch.setattr(sui, "get", lambda url, **kw: _Resp(assets))
    out = Collected()
    sui._stable_market(out)
    s = out.evidence[0].summary
    assert "USDT 約1,780億ドル" in s and "USDC 約709億ドル" in s and "JPYC 約1,875万円" in s
    assert "米ドル建て全体 約2,489億ドル" in s
    assert out.evidence[0].source.startswith(run.DATA_SOURCES)  # numbers alone never trigger a post


@pytest.fixture(autouse=True)
def _no_news_search(monkeypatch):
    monkeypatch.setenv("WATCHER_NEWS_SEARCH", "0")  # run() tests must not call Claude / the web


class _Page:
    def __init__(self, html, url="https://news.example/a", status=200):
        self.text, self.url, self.status_code = html, url, status
        self.headers = {"content-type": "text/html; charset=utf-8"}


def test_fetch_article_reads_date_and_meta(monkeypatch):
    html = ('<html><head><meta property="og:title" content="Mastercard and OpenAI sign agentic deal">'
            '<meta property="og:description" content="Agent Pay comes to ChatGPT.">'
            '<meta property="article:published_time" content="2026-11-03T08:56:00Z"></head>'
            '<body><p>Body text.</p></body></html>')
    monkeypatch.setattr(common, "get", lambda url, **kw: _Page(html, "https://www.news.example/a"))
    ev = common.fetch_article("https://www.news.example/a", label="Web 記事・報道")
    assert ev.source == "Web 記事・報道（news.example）"
    assert ev.title.startswith("Mastercard") and "Agent Pay" in ev.summary
    assert ev.published_at.startswith("2026-11-03T08:56")
    monkeypatch.setattr(common, "get", lambda url, **kw: _Page("<html><p>no date</p></html>"))
    assert common.fetch_article("https://news.example/b") is None  # cannot prove it is new
    assert common.fetch_article("https://news.example/b", page_age="November 3, 2026") is not None


def test_news_evidence_keeps_only_fresh_unreported(monkeypatch, tmp_path):
    cands = [{"url": f"https://n.example/{k}", "title": k, "page_age": ""} for k in ("new", "old", "rep", "dup")]
    monkeypatch.setattr(llm, "discover_news", lambda **kw: cands)
    times = {"new": NOW - timedelta(hours=2), "old": NOW - timedelta(days=5), "rep": NOW, "dup": NOW}
    monkeypatch.setattr(run, "fetch_article", lambda url, **kw: Evidence(
        source="Web 記事・報道（n.example）", title="t", url=url,
        published_at=times[url.rsplit("/", 1)[1]].isoformat()))
    st = WatcherState("sui", root=tmp_path)
    st.mark_reported(["https://n.example/rep"], "2026-11-03")
    out = run._news_evidence(sui, st, NOW - timedelta(hours=26), {"https://n.example/dup"})
    assert [e.url for e in out] == ["https://n.example/new"]
