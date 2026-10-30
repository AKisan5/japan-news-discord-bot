from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class FeedConfig(BaseModel):
    name: str
    url: str
    category: str
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://: {v!r}")
        return v


class Settings(BaseModel):
    max_briefing_items: int = Field(default=15, ge=1, le=50)
    claude_timeout: float = Field(default=180, gt=0)
    article_timeout: float = Field(default=20, gt=0)
    max_items_per_feed: int = 5
    state_retention: int = 1000
    http_timeout: int = 15
    user_agent: str = "news-discord-bot/1.0"
    post_interval_seconds: float = 1.5


class DiscordConfig(BaseModel):
    # Retained for config compatibility; the briefing supplies its own header.
    header: str | None = None


class Config(BaseModel):
    settings: Settings = Settings()
    discord: DiscordConfig = DiscordConfig()
    feeds: list[FeedConfig]

    @model_validator(mode="after")
    def filter_disabled(self) -> "Config":
        self.feeds = [f for f in self.feeds if f.enabled]
        return self


def load_config(path: Path = Path("config/feeds.yml")) -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Config(**data)
