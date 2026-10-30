from unittest.mock import MagicMock

import pytest

from src import reddit_fetcher
from src.config_loader import FeedConfig
from src.reddit_fetcher import RedditClient


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


@pytest.fixture
def feed():
    return FeedConfig(name="Reddit r/ML", type="reddit", subreddit="MachineLearning", category="AI")


LISTING = {"data": {"children": [
    {"data": {"title": "Sticky", "stickied": True, "permalink": "/r/x/1/"}},
    {"data": {"title": "Link post", "permalink": "/r/MachineLearning/comments/a/",
              "url_overridden_by_dest": "https://arxiv.org/abs/1", "selftext": "",
              "is_self": False, "created_utc": 1700000000, "subreddit": "MachineLearning"}},
    {"data": {"title": "Self post", "permalink": "/r/MachineLearning/comments/b/",
              "selftext": "Hello  world " * 100, "is_self": True, "created_utc": 1700000100}},
]}}


def test_fetch_maps_posts(monkeypatch, feed):
    post = MagicMock(return_value=_resp({"access_token": "tok"}))
    get = MagicMock(return_value=_resp(LISTING))
    monkeypatch.setattr(reddit_fetcher.httpx, "post", post)
    monkeypatch.setattr(reddit_fetcher.httpx, "get", get)
    client = RedditClient("id", "sec", user_agent="ua", timeout=5)
    items = client.fetch(feed, max_items=10)

    assert [i.title for i in items] == ["Link post", "Self post"]
    assert items[0].url == "https://arxiv.org/abs/1"
    assert items[1].url == "https://www.reddit.com/r/MachineLearning/comments/b/"
    assert len(items[1].summary) <= 300 and items[1].summary.startswith("Hello world")
    assert items[0].feed_category == "AI" and items[0].published_at is not None
    assert post.call_args.kwargs["data"] == {"grant_type": "client_credentials"}
    args, kwargs = get.call_args
    assert args[0] == "https://oauth.reddit.com/r/MachineLearning/top"
    assert kwargs["params"]["t"] == "day"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    client.fetch(feed, max_items=10)
    assert post.call_count == 1  # token cached


def test_fetch_error_returns_empty(monkeypatch, feed):
    monkeypatch.setattr(reddit_fetcher.httpx, "post", MagicMock(side_effect=RuntimeError("boom")))
    errors = []
    items = RedditClient("id", "sec", user_agent="ua", timeout=5).fetch(
        feed, max_items=5, on_error=errors.append)
    assert items == [] and len(errors) == 1


def test_from_env_missing_secrets(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    assert RedditClient.from_env(timeout=5, default_user_agent="ua") is None


def test_from_env_present(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "sec")
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    c = RedditClient.from_env(timeout=5, default_user_agent="ua")
    assert c is not None and c.user_agent == "ua"


def test_reddit_config_requires_subreddit():
    with pytest.raises(ValueError):
        FeedConfig(name="x", type="reddit", category="AI")


def test_rss_config_requires_url():
    with pytest.raises(ValueError):
        FeedConfig(name="x", category="AI")


def test_real_config_loads():
    from src.config_loader import load_config
    cfg = load_config()
    subs = {f.subreddit for f in cfg.feeds if f.type == "reddit"}
    assert {"MachineLearning", "technology", "japannews", "CryptoCurrency"} <= subs
