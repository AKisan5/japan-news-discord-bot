from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from .pending_reads import DEFAULT_GUILD_ID, PendingReadStore

logger = logging.getLogger(__name__)
API_BASE = "https://discord.com/api/v10"
CHECK = "✅"


def build_summary_line(entries: list[dict[str, str]], guild_id: str) -> str:
    lines = [f"🔔 未読ニュースが {len(entries)} 件あります。各投稿に✅を付けると消えます。"]
    oldest_first = sorted(entries, key=lambda e: datetime.fromisoformat(e["posted_at"]))
    for entry in oldest_first[:3]:
        lines.append(
            f"https://discord.com/channels/{guild_id}/"
            f"{entry['channel_id']}/{entry['message_id']}"
        )
    if len(entries) > 3:
        lines.append(f"ほか {len(entries) - 3} 件")
    return "\n".join(lines)


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    response = client.request(method, API_BASE + path, **kwargs)
    # Retry only explicit rate limits; retrying ambiguous POST failures may double-remind.
    if response.status_code == 429:
        delay = float(response.json().get("retry_after", 1))
        time.sleep(max(0, delay))
        response = client.request(method, API_BASE + path, **kwargs)
    response.raise_for_status()
    return response


def has_human_check(client: httpx.Client, entry: dict[str, str]) -> bool:
    path = f"/channels/{entry['channel_id']}/messages/{entry['message_id']}"
    message = _request(client, "GET", path).json()
    types = [0]
    # Burst reactions use a separate user list in Discord's API.
    if any(
        reaction.get("emoji", {}).get("name") == CHECK
        and reaction.get("count_details", {}).get("burst", 0) > 0
        for reaction in message.get("reactions", [])
    ):
        types.append(1)
    for reaction_type in types:
        after = None
        while True:
            params = {"limit": 100, "type": reaction_type}
            if after is not None:
                params["after"] = after
            users = _request(
                client, "GET", path + "/reactions/" + quote(CHECK, safe=""), params=params
            ).json()
            if not isinstance(users, list):
                raise ValueError("Invalid Discord reaction response")
            if any(not user.get("bot", False) for user in users):
                return True
            if len(users) < 100:
                break
            cursor = max(int(user["id"]) for user in users)
            if after is not None and cursor <= after:
                raise ValueError("Discord reaction pagination did not advance")
            after = cursor
    return False


def check_pending(client: httpx.Client, store: PendingReadStore, *, guild_id: str) -> int:
    failures = 0
    seen = set()
    unread_by_channel: dict[str, list[dict[str, str]]] = {}
    for entry in sorted(store.entries[:], key=lambda e: datetime.fromisoformat(e["posted_at"])):
        key = (entry["channel_id"], entry["message_id"])
        if key in seen:
            continue
        seen.add(key)
        try:
            if has_human_check(client, entry):
                store.entries = [
                    item for item in store.entries
                    if (item["channel_id"], item["message_id"]) != key
                ]
                store.save()
                continue
            unread_by_channel.setdefault(entry["channel_id"], []).append(entry)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Do not log exception strings, response bodies, or authentication headers.
            logger.error("Could not check message %s; keeping pending", entry["message_id"])
            failures += 1

    for channel_id, entries in unread_by_channel.items():
        try:
            _request(client, "POST", f"/channels/{channel_id}/messages", json={
                "content": build_summary_line(entries, guild_id),
                "allowed_mentions": {"parse": [], "replied_user": False},
            })
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.error("Could not send summary to channel %s; keeping pending", channel_id)
            failures += 1
    return failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN environment variable is not set")
        return 1
    store = PendingReadStore()
    try:
        store.load()
    except (OSError, ValueError):
        logger.error("Could not load pending reads; leaving state unchanged")
        return 1
    with httpx.Client(timeout=30, headers={"Authorization": f"Bot {token}"}) as client:
        failures = check_pending(
            client, store, guild_id=os.environ.get("DISCORD_GUILD_ID") or DEFAULT_GUILD_ID
        )
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
