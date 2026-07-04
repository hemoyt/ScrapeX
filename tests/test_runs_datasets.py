"""Tests for the Apify-style runs + datasets layer."""
import json

import pytest
import respx
from httpx import Response

from app.config import settings
from app.services.datasets import run_store

ALGOLIA = "https://hn.algolia.com/api/v1"


@pytest.fixture(autouse=True)
def _clear_runs():
    run_store.clear()
    delay = settings.run_page_delay
    settings.run_page_delay = 0
    yield
    settings.run_page_delay = delay
    run_store.clear()


def _hn_page(page: int, per_page: int, nb_pages: int):
    # page * 1000 keeps objectIDs globally unique even when per_page varies
    hits = [
        {
            "objectID": str(page * 1000 + i),
            "title": f"Story {page * 1000 + i}",
            "author": "pg",
            "points": 100 + i,
            "num_comments": 3,
            "created_at": "2026-01-01T00:00:00Z",
            "url": f"https://example.com/{page * 1000 + i}",
        }
        for i in range(per_page)
    ]
    return {"hits": hits, "page": page, "nbPages": nb_pages}


def _mock_hn_search():
    def responder(request):
        params = dict(request.url.params)
        page = int(params.get("page", 0))
        per_page = int(params.get("hitsPerPage", 20))
        return Response(200, json=_hn_page(page, per_page, nb_pages=3))

    respx.get(url__startswith=f"{ALGOLIA}/search").mock(side_effect=responder)


@respx.mock
def test_run_paginates_until_max_items(client):
    _mock_hn_search()
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "llm", "max_items": 120},
    )
    assert resp.status_code == 202
    run_id = resp.json()["id"]
    dataset_id = resp.json()["dataset_id"]

    # TestClient executes background tasks before returning, so the run is done.
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "SUCCEEDED"
    assert run["item_count"] == 120
    assert run["pages_fetched"] == 3  # 50 + 50 + 20
    assert run["source"] == "hn_algolia"

    page = client.get(f"/api/v1/datasets/{dataset_id}/items?limit=1000").json()
    assert page["total"] == 120
    assert page["count"] == 120
    assert page["items"][0]["text"] == "Story 0"


@respx.mock
def test_run_stops_when_platform_exhausted(client):
    # Only 1 Algolia page exists -> run ends with fewer items than requested.
    def responder(request):
        page = int(dict(request.url.params).get("page", 0))
        return Response(200, json=_hn_page(page, 10, nb_pages=1))

    respx.get(url__startswith=f"{ALGOLIA}/search").mock(side_effect=responder)
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "niche topic", "max_items": 500},
    )
    run = client.get(f"/api/v1/runs/{resp.json()['id']}").json()
    assert run["status"] == "SUCCEEDED"
    assert run["item_count"] == 10
    assert run["pages_fetched"] == 1


@respx.mock
def test_run_fails_honestly_on_first_page_error(client):
    respx.get(url__startswith=f"{ALGOLIA}/search").mock(return_value=Response(500))
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "x", "max_items": 10},
    )
    run = client.get(f"/api/v1/runs/{resp.json()['id']}").json()
    assert run["status"] == "FAILED"
    assert run["error"]
    assert run["item_count"] == 0


@respx.mock
def test_dataset_export_ndjson_and_csv(client):
    _mock_hn_search()
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "llm", "max_items": 5},
    )
    dataset_id = resp.json()["dataset_id"]

    nd = client.get(f"/api/v1/datasets/{dataset_id}/items?format=ndjson")
    assert nd.status_code == 200
    lines = [json.loads(l) for l in nd.text.strip().splitlines()]
    assert len(lines) == 5
    assert lines[0]["author"] == "pg"

    cs = client.get(f"/api/v1/datasets/{dataset_id}/items?format=csv")
    assert cs.status_code == 200
    assert cs.headers["content-type"].startswith("text/csv")
    rows = cs.text.strip().splitlines()
    assert len(rows) == 6  # header + 5 items
    assert "author" in rows[0]


@respx.mock
def test_dataset_pagination_offset_limit(client):
    _mock_hn_search()
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "llm", "max_items": 60},
    )
    dataset_id = resp.json()["dataset_id"]
    page = client.get(f"/api/v1/datasets/{dataset_id}/items?offset=50&limit=100").json()
    assert page["total"] == 60
    assert page["offset"] == 50
    assert page["count"] == 10


def test_run_validation(client):
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "myspace", "identifier": "x"},
    )
    assert resp.status_code == 404

    # reddit has no profile support -> 400 with the supported list
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "reddit", "query_type": "profile", "identifier": "x"},
    )
    assert resp.status_code == 400
    assert "does not support" in resp.json()["error"]


def test_run_and_dataset_404s(client):
    assert client.get("/api/v1/runs/nope").status_code == 404
    assert client.get("/api/v1/datasets/nope").status_code == 404
    assert client.get("/api/v1/datasets/nope/items").status_code == 404


@respx.mock
async def test_reddit_fetch_page_builds_after_cursor():
    """The reddit override paginates old.reddit with after=<fullname>."""
    from pathlib import Path

    from app.models import SocialQueryType, SocialRequest
    from app.services.reddit import RedditService

    html = (Path(__file__).parent / "fixtures" / "old_reddit_listing.html").read_text()
    route = respx.get(url__startswith="https://old.reddit.com/r/python/hot/").mock(
        return_value=Response(200, text=html)
    )

    svc = RedditService()
    req = SocialRequest(query_type=SocialQueryType.posts, identifier="python", limit=25)
    resp, cursor = await svc.fetch_page(req, None)
    assert resp.success
    assert resp.posts
    assert cursor and cursor.startswith("t3_")

    # Second page must carry the cursor in the URL
    await svc.fetch_page(req, cursor)
    await svc.aclose()
    assert f"after={cursor}" in str(route.calls[-1].request.url)


@respx.mock
async def test_bluesky_fetch_page_passes_cursor():
    from app.models import SocialQueryType, SocialRequest
    from app.services.bluesky import BlueskyService

    post = {
        "uri": "at://did:plc:x/app.bsky.feed.post/abc",
        "author": {"handle": "bsky.app"},
        "record": {"text": "hi", "createdAt": "2026-01-01T00:00:00Z"},
        "likeCount": 1,
    }
    route = respx.get(
        url__startswith="https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    ).mock(return_value=Response(200, json={"feed": [{"post": post}], "cursor": "page2"}))

    svc = BlueskyService()
    req = SocialRequest(query_type=SocialQueryType.posts, identifier="bsky.app", limit=50)
    resp, cursor = await svc.fetch_page(req, None)
    assert resp.success and cursor == "page2"

    await svc.fetch_page(req, cursor)
    await svc.aclose()
    assert "cursor=page2" in str(route.calls[-1].request.url)
