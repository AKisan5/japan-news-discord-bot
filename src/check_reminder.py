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


def build_reminder_line(entry: dict[str, str], guild_id: str) -> str:
    jump_link = (
        f"https://discord.com/channels/{guild_id}/"
        f"{entry['channel_id']}/{entry['message_id']}"
    )
    return f"🔔 まだ✅が付いていないニュースがあります {jump_link} — 読んだら✅を付けてください"


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
            _request(client, "POST", f"/channels/{entry['channel_id']}/messages", json={
                "content": build_reminder_line(entry, guild_id),
                "message_reference": {
                    "message_id": entry["message_id"],
                    "channel_id": entry["channel_id"],
                    "fail_if_not_exists": False,
                },
                "allowed_mentions": {"parse": [], "replied_user": False},
            })
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Do not log exception strings, response bodies, or authentication headers.
            logger.error("Could not check/remind message %s; keeping pending", entry["message_id"])
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
