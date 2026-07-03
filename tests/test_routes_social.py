import pathlib

import respx
from httpx import Response

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _reddit_html():
    return (FIXTURES / "old_reddit_listing.html").read_text()


def test_unknown_platform_404(client):
    resp = client.post("/api/v1/social/myspace", json={"identifier": "tom"})
    assert resp.status_code == 404
    assert "Unknown platform" in resp.json()["error"]


def test_unsupported_query_type(client):
    # reddit does not support profile
    resp = client.post(
        "/api/v1/social/reddit",
        json={"query_type": "profile", "identifier": "spez"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "error"
    assert "does not support" in body["error"]


@respx.mock
def test_unified_reddit_posts(client):
    respx.get("https://old.reddit.com/r/python/hot/").mock(
        return_value=Response(200, text=_reddit_html())
    )
    resp = client.post(
        "/api/v1/social/reddit",
        json={"query_type": "posts", "identifier": "python", "limit": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["platform"] == "reddit"
    assert body["source"] == "old.reddit.com"
    assert len(body["posts"]) == 2

    post = body["posts"][0]
    assert post["author"] == "guido"
    assert "Python 4.0" in post["text"]
    assert post["stats"]["score"] == 4242
    assert post["stats"]["comments"] == 512
    assert post["extra"]["domain"] == "python.org"
    # legacy raw payload still present
    assert body["data"][0]["title"].startswith("Python 4.0")


@respx.mock
def test_legacy_reddit_alias(client):
    respx.get("https://old.reddit.com/r/python/top/").mock(
        return_value=Response(200, text=_reddit_html())
    )
    resp = client.post(
        "/api/v1/social/reddit",
        json={"subreddit": "python", "listing": "top", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # legacy field kept populated
    assert len(body["data"]) == 2
    assert body["data"][0]["subreddit"] == "python"


@respx.mock
def test_reddit_search(client):
    route = respx.get(url__startswith="https://old.reddit.com/search").mock(
        return_value=Response(200, text=(FIXTURES / "old_reddit_search.html").read_text())
    )
    resp = client.post(
        "/api/v1/social/reddit",
        json={"query_type": "search", "identifier": "python release"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert route.called
    assert len(body["posts"]) == 1
    post = body["posts"][0]
    assert post["author"] == "Rare-Assignment-8474"
    assert post["stats"] == {"score": 4, "comments": 18}
    assert post["created_at"] == "2026-06-08T15:16:19+00:00"
    assert post["extra"]["subreddit"] == "cscareerquestions"


@respx.mock
def test_caching(client):
    route = respx.get("https://old.reddit.com/r/python/hot/").mock(
        return_value=Response(200, text=_reddit_html())
    )
    payload = {"query_type": "posts", "identifier": "python"}
    first = client.post("/api/v1/social/reddit", json=payload).json()
    second = client.post("/api/v1/social/reddit", json=payload).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert route.call_count == 1


@respx.mock
def test_multi_search_partial_failure(client):
    respx.get(url__startswith="https://old.reddit.com/search").mock(
        return_value=Response(500)
    )
    resp = client.post(
        "/api/v1/social/search",
        json={"query": "python", "platforms": ["reddit", "nosuchplatform"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "reddit" in body["results"]
    # reddit errored (500 upstream) but the call itself did not blow up
    assert body["results"]["reddit"]["status"] == "error"


def test_multi_search_no_valid_platforms(client):
    resp = client.post(
        "/api/v1/social/search",
        json={"query": "python", "platforms": ["nosuchplatform"]},
    )
    assert resp.status_code == 400
