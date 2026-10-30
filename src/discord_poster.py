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
    message_ids: list[str] = field(default_factory=list)


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


def _post_with_retry(client: httpx.Client, webhook_url: str, payload: dict) -> str | None:
    # Replace an existing wait=false while preserving other query parameters.
    url = httpx.URL(webhook_url).copy_set_param("wait", "true")
    waits = iter([1, 2, 4])
    rate_limit_retried = False
    while True:
        try:
            resp = client.post(url, json=payload)
        except httpx.RequestError:
            # Exception strings can contain the secret webhook URL.
            logger.error("Request error posting to Discord")
            return None
        if resp.status_code == 200:
            try:
                message_id = resp.json()["id"]
                if not isinstance(message_id, str) or not message_id.isdigit():
                    raise ValueError("Invalid message ID")
                return message_id
            except (ValueError, KeyError, TypeError):
                logger.error("Discord webhook response has no valid message ID")
                return None
        if resp.status_code == 429 and not rate_limit_retried:
            rate_limit_retried = True
            try:
                delay = float(resp.headers.get("Retry-After", "1"))
            except ValueError:
                delay = 1
            time.sleep(max(0, delay))
            continue
        if 500 <= resp.status_code < 600:
            delay = next(waits, None)
            if delay is not None:
                time.sleep(delay)
                continue
        logger.error("Discord webhook returned status %d", resp.status_code)
        return None


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
            payload: dict = {
                "embeds": embeds,
                "content": "読んだら✅を付けてください",
                "allowed_mentions": {"parse": []},
            }
            if idx == 0 and discord_config.header:
                payload["content"] = discord_config.header + "\n" + payload["content"]

            message_id = _post_with_retry(client, webhook_url, payload)

            if message_id:
                result.message_ids.append(message_id)
                result.posted.extend(batch)
            else:
                result.failed.extend(batch)
                logger.error("Failed to post batch %d/%d", idx + 1, len(batches))

            if idx < len(batches) - 1:
                time.sleep(interval_seconds)

    return result
