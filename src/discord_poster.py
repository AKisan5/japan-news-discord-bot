from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from .config_loader import DiscordConfig
from .feed_fetcher import FeedItem

logger = logging.getLogger(__name__)


@dataclass
class PostResult:
    posted: list[FeedItem] = field(default_factory=list)
    failed: list[FeedItem] = field(default_factory=list)


def _build_embed(item: FeedItem, color: int) -> dict:
    embed: dict = {
        "title": item.title[:256],
        "url": item.url,
        "description": item.summary[:300],
        "color": color,
        "footer": {"text": f"{item.feed_name} / {item.feed_category}"},
    }
    if item.published_at:
        embed["timestamp"] = item.published_at.isoformat()
    return embed


def _post_with_retry(client: httpx.Client, webhook_url: str, payload: dict) -> bool:
    try:
        resp = client.post(webhook_url, json=payload)
    except httpx.RequestError as exc:
        logger.error("Request error posting to Discord: %s", exc)
        return False

    if resp.status_code == 204:
        return True

    if resp.status_code == 429:
        retry_after = float(resp.headers.get("Retry-After", "1"))
        logger.warning("Rate limited (429), retrying after %.1fs", retry_after)
        time.sleep(retry_after)
        try:
            resp = client.post(webhook_url, json=payload)
            return resp.status_code == 204
        except httpx.RequestError as exc:
            logger.error("Request error on 429 retry: %s", exc)
            return False

    if 500 <= resp.status_code < 600:
        for wait in [1, 2, 4]:
            logger.warning("5xx error %d, retrying in %ds", resp.status_code, wait)
            time.sleep(wait)
            try:
                resp = client.post(webhook_url, json=payload)
            except httpx.RequestError as exc:
                logger.error("Request error during 5xx retry: %s", exc)
                continue
            if resp.status_code == 204:
                return True
            if not (500 <= resp.status_code < 600):
                logger.error("Unexpected status during 5xx retry: %d", resp.status_code)
                return False
        logger.error("5xx persisted after 3 retries: %d", resp.status_code)
        return False

    logger.error("Discord API error %d: %s", resp.status_code, resp.text[:200])
    return False


def post_items(
    items: list[FeedItem],
    *,
    webhook_url: str,
    discord_config: DiscordConfig,
    interval_seconds: float,
) -> PostResult:
    result = PostResult()
    if not items:
        return result

    n = discord_config.embeds_per_message
    batches = [items[i : i + n] for i in range(0, len(items), n)]

    with httpx.Client(timeout=30) as client:
        for idx, batch in enumerate(batches):
            embeds = [_build_embed(item, discord_config.embed_color) for item in batch]
            payload: dict = {"embeds": embeds}
            if idx == 0 and discord_config.header:
                payload["content"] = discord_config.header

            success = _post_with_retry(client, webhook_url, payload)

            if success:
                result.posted.extend(batch)
            else:
                result.failed.extend(batch)
                logger.error("Failed to post batch %d/%d", idx + 1, len(batches))

            if idx < len(batches) - 1:
                time.sleep(interval_seconds)

    return result
