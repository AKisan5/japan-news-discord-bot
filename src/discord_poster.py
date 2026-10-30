from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class PostResult:
    success: bool = True
    message_ids: list[str] = field(default_factory=list)


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


REACTION_PROMPT = "読んだら✅を付けてください"


def _discord_length(text: str) -> int:
    # Count UTF-16 code units too, so astral emoji cannot exceed Discord's cap.
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = 1900) -> list[str]:
    """Prefer blank-line boundaries; split oversized paragraphs only as needed."""
    if limit < 2:
        raise ValueError("Message limit must be at least 2")
    chunks: list[str] = []
    current = ""
    for paragraph in text.strip().split("\n\n"):
        if not paragraph:
            continue
        candidate = current + "\n\n" + paragraph if current else paragraph
        if _discord_length(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while _discord_length(paragraph) > limit:
            units = 0
            cut = 0
            for char in paragraph:
                size = _discord_length(char)
                if units + size > limit:
                    break
                units += size
                cut += 1
            # Prefer a line/sentence boundary within an oversized paragraph.
            boundary = max(paragraph.rfind("\n", 0, cut),
                           paragraph.rfind("。", 0, cut))
            if boundary >= cut // 2:
                cut = boundary + 1
            chunks.append(paragraph[:cut])
            paragraph = paragraph[cut:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def post_briefing(briefing: str, *, webhook_url: str,
                  interval_seconds: float = 1.5) -> PostResult:
    result = PostResult()
    if not briefing.strip():
        result.success = False
        return result
    chunks = split_message(briefing.rstrip() + "\n\n" + REACTION_PROMPT)
    with httpx.Client(timeout=30) as client:
        for index, chunk in enumerate(chunks):
            message_id = _post_with_retry(client, webhook_url, {
                "content": chunk, "allowed_mentions": {"parse": []},
            })
            if not message_id:
                result.success = False
                break
            result.message_ids.append(message_id)
            if index < len(chunks) - 1:
                time.sleep(interval_seconds)
    return result
