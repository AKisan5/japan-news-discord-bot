from __future__ import annotations

import logging
import os
import sys

from .claude_curator import CurationError, MissingAPIKeyError, curate_items
from .config_loader import load_config
from .deduplicator import StateStore
from .discord_poster import PostResult, post_items
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

    if not new_items:
        logger.info("No new items today, skipping Discord post")
        return 2 if feed_error_count else 0

    try:
        curated_items = curate_items(
            new_items,
            max_curated=config.settings.max_curated,
            timeout=config.settings.claude_timeout,
        )
    except MissingAPIKeyError:
        logger.error("ANTHROPIC_API_KEY environment variable is not set")
        return 1
    except CurationError:
        logger.error("Claude curation failed; no news posted and state unchanged")
        return 2

    logger.info("Claude selected %d important items", len(curated_items))
    result = PostResult()
    if curated_items:
        result = post_items(
            curated_items,
            webhook_url=webhook_url,
            discord_config=config.discord,
            interval_seconds=config.settings.post_interval_seconds,
        )

    if result.message_ids:
        pending.append(
            result.message_ids,
            channel_id=os.environ.get("DISCORD_CHANNEL_ID") or DEFAULT_CHANNEL_ID,
        )
        pending.save()

    # Remember noise too, but leave failed Discord deliveries retryable.
    failed_urls = {item.url for item in result.failed}
    processed = [item for item in new_items if item.url not in failed_urls]
    if processed:
        state.mark_posted(processed)
        state.save(retention=config.settings.state_retention)

    logger.info(
        "Posted %d items, failed %d items", len(result.posted), len(result.failed)
    )

    if feed_error_count > 0 or result.failed:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
