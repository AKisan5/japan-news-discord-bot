import pytest
from pydantic import ValidationError

from src.config_loader import Settings, load_config


def test_curated_limits():
    assert Settings().max_curated == 50
    assert Settings(max_curated=50).max_curated == 50
    for limit in (0, 51):
        with pytest.raises(ValidationError):
            Settings(max_curated=limit)


def test_expanded_feeds_enabled_and_unique():
    config = load_config()
    feeds = {feed.name: feed for feed in config.feeds}
    required = {
        "BBC World", "Bloomberg Politics", "CNBC World", "NYT Technology",
        "WSJ World", "Reuters via GN", "CNN via GN", "日経 via GN",
        "読売 via GN", "朝日 via GN", "Yahoo 国内", "Yahoo 国際",
        "Yahoo 経済・市場", "Yahoo テクノロジー", "Wired AI", "MIT Tech Review",
    }
    assert required <= feeds.keys()
    assert all(feeds[name].enabled for name in required)
    assert len({feed.url for feed in config.feeds}) == len(config.feeds)
    assert "OpenAI+OR+Anthropic+OR+Google+DeepMind+when:1d" in feeds["Google News AI EN"].url
    # Existing sources remain available alongside major papers.
    assert {"NHK 主要ニュース", "COINPOST", "OpenAI", "Google DeepMind"} <= feeds.keys()
