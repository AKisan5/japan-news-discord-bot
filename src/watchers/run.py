"""Entry point: python -m src.watchers.run --target even|sui|sui-alert [--dry-run] [--channel news|test]

Exit codes: 0 = ok (posted or nothing new), 1 = config error, 2 = failure (nothing posted).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from . import even, llm, sui
from .common import (Evidence, WatcherState, fetch_x_post, normalize_url, now_jst, parse_dt,
                     post_to_discord, since_window, url_alive, write_summary)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("watchers")

DATA_SOURCES = ("CoinGecko", "DefiLlama", "status.sui.io")
MAX_X_POSTS = 20


def _x_evidence(module, state: WatcherState, since) -> list[Evidence]:
    urls = llm.discover_x_urls(accounts=module.X_ACCOUNTS, queries=module.X_QUERIES, since=since)
    logger.info("X discovery returned %d URLs", len(urls))
    out: list[Evidence] = []
    for url in urls:
        if state.is_reported(url):
            continue
        ev = fetch_x_post(url)  # exact text / author / time from fxtwitter
        if not ev or state.is_reported(ev.url):
            continue
        when = parse_dt(ev.published_at)
        if not when or when < since:
            continue
        out.append(ev)
        if len(out) >= MAX_X_POSTS:
            break
    return out


def _render(target, module, briefing, evidence, since, now) -> str:
    if target == "sui-alert":
        return sui.render(briefing, evidence, since, now, alert=True)
    return module.render(briefing, evidence, since, now)


def _drop_dead_links(briefing, evidence: list[Evidence]) -> list[str]:
    used = {evidence[int(i)].url for it in briefing.items for i in it.evidence_ids
            if i.isdigit() and int(i) < len(evidence)}
    dead = [u for u in used if u and not url_alive(u)]
    for e in evidence:
        if e.url in dead:
            e.url = ""
    return dead


def run(target: str, *, dry_run: bool, channel: str, force: bool = False,
        window: tuple | None = None) -> int:
    """window=(since, now) replays a past period for comparison (dry-run only, state ignored)."""
    module = even if target == "even" else sui
    state = WatcherState("even" if target == "even" else "sui")
    if window:
        state = WatcherState("replay", root=Path(tempfile.mkdtemp()))
        state.data["seen"] = {}
    now = window[1] if window else now_jst()
    today = now.date().isoformat()
    if target != "sui-alert" and not dry_run and not force and state.data.get("last_posted_day") == today:
        logger.info("already posted today (%s); skipping duplicate trigger", today)
        return 0
    since = window[0] if window else since_window(state, now)
    logger.info("target=%s since=%s dry_run=%s channel=%s", target, since.isoformat(), dry_run, channel)

    alerts: list = []
    if target == "sui-alert":
        collected = sui.collect(state, since, light=True)
        alerts = sui.check_alerts(collected, state, now)
        if not alerts:
            logger.info("no alerts")
            write_summary(f"### SUI速報チェック {now:%Y-%m-%d %H:%M} JST\nALERT 0件／取得失敗: {collected.errors or 'なし'}")
            if not dry_run:
                state.save(today)
            return 0
        evidence = collected.evidence
    else:
        collected = module.collect(state, since)
        try:
            evidence = collected.evidence + _x_evidence(module, state, since)
        except llm.WatcherLLMError as exc:
            logger.warning("X discovery failed: %s (continuing without X)", exc)
            collected.errors.append(f"X 検索: {exc}")
            evidence = collected.evidence

    if window:  # replay: keep only what existed before the window end
        evidence = [e for e in evidence if not parse_dt(e.published_at) or parse_dt(e.published_at) <= now]
    fresh = [e for e in evidence if not e.source.startswith(DATA_SOURCES)]
    if not fresh and not alerts:
        logger.info("no new primary sources; not posting")
        write_summary(f"### {module.USERNAME} {today}\n新しい一次ソース0件のため投稿なし／取得失敗: {collected.errors or 'なし'}")
        if not dry_run:
            for src, keys in collected.seen.items():
                state.set_seen(src, keys)
            state.save(today)
        return 0

    system = sui.ALERT_SYSTEM if target == "sui-alert" else module.SYSTEM
    ev_payload = llm.evidence_payload(evidence)
    payload = (sui.payload(collected, since, now, ev_payload, alerts) if module is sui
               else even.payload(collected, since, now, ev_payload))
    try:
        briefing = llm.write_briefing(system=system, payload=payload)
    except llm.WatcherLLMError as exc:
        logger.error("writing failed: %s", exc)
        write_summary(f"### {module.USERNAME} {today}\n執筆に失敗: {exc}")
        return 2

    dead = _drop_dead_links(briefing, evidence)
    text = _render(target, module, briefing, evidence, since, now)
    used_urls = [evidence[int(i)].url for it in briefing.items for i in it.evidence_ids
                 if i.isdigit() and int(i) < len(evidence) and evidence[int(i)].url]
    summary = (f"### {module.USERNAME} {target} {now:%Y-%m-%d %H:%M} JST\n"
               f"- 証拠 {len(evidence)}件（X {sum(1 for e in evidence if e.source.startswith('X @'))}件）"
               f"／項目 {len(briefing.items)}件／リンク切れで除外 {len(dead)}件\n"
               f"- 取得失敗: {collected.errors or 'なし'}\n- {llm.cost_line()}\n")
    if not text:
        logger.info("writer returned no postable items")
        write_summary(summary + "\n投稿なし（項目0件）")
        return 0
    write_summary(summary + "\n<details><summary>本文</summary>\n\n```\n" + text + "\n```\n</details>")
    if dry_run:
        print(text)
        print("\n" + llm.cost_line())
        return 0

    try:
        ids = post_to_discord(text, username=module.USERNAME, channel=channel)
    except RuntimeError as exc:
        logger.error("post failed: %s", exc)
        return 2
    logger.info("posted %d messages to %s", len(ids), channel)
    # The #bot trial advances the same state as #ニュース, so the switch-over continues seamlessly.
    state.mark_reported([normalize_url(u) for u in used_urls], today)
    if target == "sui-alert":
        for key, _r, _u in alerts:
            state.data["alerts"][key] = now.isoformat()
    else:
        for src, keys in collected.seen.items():
            state.set_seen(src, keys)
        state.data["last_posted_at"] = now.isoformat()
        state.data["last_posted_day"] = today
    state.save(today)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["even", "sui", "sui-alert"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--channel", choices=["news", "test"], default="news")
    ap.add_argument("--force", action="store_true", help="post even if already posted today")
    ap.add_argument("--collect-only", action="store_true",
                    help="print collected evidence without calling Claude or Discord")
    ap.add_argument("--replay", metavar="SINCE,UNTIL",
                    help="dry-run a past window (ISO times, JST if no offset) ignoring state")
    args = ap.parse_args(argv)
    if args.collect_only:
        return collect_only(args.target)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set")
        return 1
    window = None
    if args.replay:
        a, b = (parse_dt(x if "+" in x else x + "+09:00") for x in args.replay.split(","))
        window, args.dry_run = (a, b), True
    return run(args.target, dry_run=args.dry_run, channel=args.channel, force=args.force, window=window)


def collect_only(target: str) -> int:
    module = even if target == "even" else sui
    state = WatcherState("even" if target == "even" else "sui")
    now = now_jst()
    since = since_window(state, now)
    collected = (sui.collect(state, since, light=True) if target == "sui-alert"
                 else module.collect(state, since))
    print(f"since={since.isoformat()} evidence={len(collected.evidence)} errors={collected.errors}")
    for e in collected.evidence:
        print(f"- [{e.source}] {e.published_at[:16]} {e.title[:80]} {e.url}")
    if target == "sui-alert":
        print("alerts:", sui.check_alerts(collected, state, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
