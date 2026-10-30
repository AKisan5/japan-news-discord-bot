from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CHANNEL_ID = "1290456657653928048"
DEFAULT_GUILD_ID = "1173405237390868610"


class PendingReadStore:
    def __init__(self, path: Path = Path("state/pending_reads.json")) -> None:
        self.path = path
        self.entries: list[dict[str, str]] = []

    def load(self) -> None:
        if not self.path.exists():
            self.entries = []
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # Never silently reset corrupt state: that would lose unread messages.
        if not isinstance(data, list):
            raise ValueError("Pending reads must be a list")
        for entry in data:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str)
                for key in ("message_id", "channel_id", "posted_at", "kind")
            ):
                raise ValueError("Invalid pending read entry")
            if not entry["message_id"].isdigit() or not entry["channel_id"].isdigit():
                raise ValueError("Invalid pending read IDs")
            timestamp = datetime.fromisoformat(entry["posted_at"])
            if timestamp.tzinfo is None:
                raise ValueError("Pending read timestamp must include timezone")
        self.entries = data

    def append(self, message_ids: list[str], *, channel_id: str) -> None:
        seen = {(entry["channel_id"], entry["message_id"]) for entry in self.entries}
        now = datetime.now(timezone.utc).isoformat()
        for message_id in message_ids:
            key = (channel_id, message_id)
            if key not in seen:
                self.entries.append({
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "posted_at": now,
                    "kind": "news",
                })
                seen.add(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.entries, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
