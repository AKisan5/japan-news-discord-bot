from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from .article_fetcher import fetch_article
from .claude_curator import CurationError, MissingAPIKeyError, select_items, write_briefing
from .config_loader import load_config
from .deduplicator import StateStore
from .discord_poster import PostResult, post_briefing
from .feed_fetcher import fetch_feed
from .pending_reads import DEFAULT_CHANNEL_ID, PendingReadStore

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> int:
    """終了コード: 0=正常, 1=設定エラー, 2=部分失敗"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL environment variable is not set")
        return 1

    try:
        config = load_config()
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    state = StateStore()
    state.load()

    pending = PendingReadStore()
    try:
        pending.load()  # Fail before posting if unread state cannot be loaded safely.
    except (OSError, ValueError):
        logger.error("Could not load pending reads; leaving state unchanged")
        return 1

    all_items = []
    feed_error_count = 0

    def on_feed_error(exc: Exception) -> None:
        nonlocal feed_error_count
        feed_error_count += 1

    for feed in config.feeds:
        items = fetch_feed(
            feed,
            timeout=config.settings.http_timeout,
            user_agent=config.settings.user_agent,
            max_items=config.settings.max_items_per_feed,
            on_error=on_feed_error,
        )
        logger.info("Feed %r: fetched %d items", feed.name, len(items))
        all_items.extend(items)

    new_items = state.filter_new(all_items)
    logger.info("Total: %d fetched, %d new", len(all_items), len(new_items))
    # Keep Claude select payload small (Cooper-style cap).
    SELECT_CANDIDATE_CAP = 150
    if len(new_items) > SELECT_CANDIDATE_CAP:
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        new_items = sorted(
            new_items,
            key=lambda item: item.published_at or epoch,
            reverse=True,
        )[:SELECT_CANDIDATE_CAP]
        logger.info("Capped select candidates to %d", len(new_items))

    if not new_items:
        logger.info("No new items today, skipping Discord post")
        return 2 if feed_error_count else 0

    result = PostResult()
    selected = []
    try:
        selected = select_items(
            new_items,
            max_briefing_items=config.settings.max_briefing_items,
            timeout=config.settings.claude_timeout,
        )
        logger.info("Claude selected %d articles", len(selected))
        if selected:
            logger.info("Fetching article bodies for %d selected items", len(selected))
            articles = [fetch_article(
                item, timeout=config.settings.article_timeout,
                user_agent=config.settings.user_agent,
            ) for item in selected]
            with_body = [a for a in articles if a.body]
            logger.info("Fetched bodies: %d/%d usable", len(with_body), len(articles))
            # Prefer evidence-backed stories; keep a few thin ones only if needed.
            if len(with_body) >= 5:
                articles = with_body
                logger.info("Writing from %d articles with usable bodies", len(articles))
            else:
                logger.warning(
                    "Only %d usable bodies; including thin articles for coverage",
                    len(with_body),
                )
            logger.info("Writing Cooper-style briefing with Sonnet")
            briefing = write_briefing(articles, timeout=config.settings.claude_timeout)
            logger.info("Posting briefing (%d chars) to Discord", len(briefing))
            result = post_briefing(
                briefing, webhook_url=webhook_url,
                interval_seconds=config.settings.post_interval_seconds,
            )
            logger.info(
                "Discord post done: success=%s messages=%d",
                result.success, len(result.message_ids),
            )
    except MissingAPIKeyError:
        logger.error("ANTHROPIC_API_KEY environment variable is not set")
        return 1
    except CurationError as exc:
        logger.error("Claude briefing failed (%s); no news posted and state unchanged", exc)
        return 2

    if result.message_ids:
        pending.append(
            result.message_ids,
            channel_id=os.environ.get("DISCORD_CHANNEL_ID") or DEFAULT_CHANNEL_ID,
        )
        pending.save()

    # A briefing is one delivery: on any failed chunk keep all URLs retryable.
    # Successful chunks are still tracked above for read reminders.
    if not result.success:
        logger.error("Briefing delivery incomplete; news state unchanged")
        return 2

    # Mark only selected stories so unselected candidates can still run later.
    to_mark = selected if selected else new_items
    state.mark_posted(to_mark)
    state.save(retention=config.settings.state_retention)
    if feed_error_count:
        logger.warning(
            "Briefing posted; %d feed fetch warning(s) ignored for exit code",
            feed_error_count,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
