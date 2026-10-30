import base64

import httpx
import pytest

from src.article_fetcher import MAX_BODY_CHARS, fetch_article
from src.feed_fetcher import FeedItem

URL = "https://publisher.example/story"
GOOGLE = "https://news.google.com/rss/articles/opaque-token"
TEXT = "An established research team has announced a new artificial intelligence system. " * 15
HTML = f"<html><body><nav>Subscribe</nav><article><h1>Research announcement</h1><p>{TEXT}</p></article></body></html>"


def item(url=URL):
    return FeedItem("Research", url, "teaser", None, "Publisher", "AI")


def test_html_extraction(httpx_mock):
    httpx_mock.add_response(url=URL, text=HTML)
    article = fetch_article(item(), timeout=7, user_agent="test-agent")
    assert "established research team" in article.body
    assert "<p>" not in article.body
    assert "Subscribe" not in article.body
    assert article.url == URL
    assert article.title == "Research"
    assert article.source == "Publisher"
    request = httpx_mock.get_request()
    assert request.headers["User-Agent"] == "test-agent"
    assert request.extensions["timeout"]["read"] == 7


def test_body_cap(httpx_mock):
    httpx_mock.add_response(text=HTML)
    from unittest.mock import patch
    with patch("src.article_fetcher.trafilatura.extract", return_value="a" * 8000):
        assert len(fetch_article(item()).body) == MAX_BODY_CHARS


@pytest.mark.parametrize("kind", ["short", "http", "timeout", "extraction"])
def test_unavailable_body_keeps_item(httpx_mock, kind):
    if kind == "timeout":
        httpx_mock.add_exception(httpx.ReadTimeout("secret must not leak"))
    elif kind == "http":
        httpx_mock.add_response(status_code=403)
    else:
        httpx_mock.add_response(text="<p>too short</p>" if kind == "short" else HTML)
    from unittest.mock import patch
    if kind == "extraction":
        with patch("src.article_fetcher.trafilatura.extract", side_effect=ValueError):
            article = fetch_article(item())
    else:
        article = fetch_article(item())
    assert article.body == ""
    assert article.title == "Research"
    assert article.url == URL


def test_google_http_redirect(httpx_mock):
    httpx_mock.add_response(url=GOOGLE, status_code=302, headers={"Location": URL})
    httpx_mock.add_response(url=URL, text=HTML)
    article = fetch_article(item(GOOGLE))
    assert article.url == URL
    assert article.body


@pytest.mark.parametrize("link", [f'<a href="{URL}">Publisher</a>',
    f'<link rel="canonical" href="{URL}">', f'<meta property="og:url" content="{URL}">'])
def test_google_publisher_link(httpx_mock, link):
    httpx_mock.add_response(url=GOOGLE, text=link)
    httpx_mock.add_response(url=URL, text=HTML)
    article = fetch_article(item(GOOGLE))
    assert article.url == URL
    assert article.body


def test_google_legacy_encoded_url(httpx_mock):
    token = base64.urlsafe_b64encode(b"\x08\x13\x22" + URL.encode() + b"\xd2\x01\x00").decode().rstrip("=")
    url = "https://news.google.com/rss/articles/" + token
    httpx_mock.add_response(url=url, text="<html>Google News</html>")
    httpx_mock.add_response(url=URL, text=HTML)
    article = fetch_article(item(url))
    assert article.url == URL
    assert article.body


def test_unresolved_google_does_not_summarize_aggregator(httpx_mock):
    httpx_mock.add_response(url=GOOGLE, text=f'<a href="https://accounts.google.com/login">Login</a>{HTML}')
    article = fetch_article(item(GOOGLE))
    assert article.url == GOOGLE
    assert article.body == ""


def test_resolved_publisher_url_survives_paywall(httpx_mock):
    httpx_mock.add_response(url=GOOGLE, text=f'<a href="{URL}">Publisher</a>')
    httpx_mock.add_response(url=URL, status_code=403)
    article = fetch_article(item(GOOGLE))
    assert article.url == URL
    assert article.body == ""
