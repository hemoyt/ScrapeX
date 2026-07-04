"""Tests for the clean pipeline (tidy + AI summary before output)."""
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from app.services import ai_cleaner
from app.services.datasets import run_store

ALGOLIA = "https://hn.algolia.com/api/v1"


@pytest.fixture(autouse=True)
def _clear_runs():
    run_store.clear()
    yield
    run_store.clear()


@pytest.fixture
def fake_ai(monkeypatch):
    """Stand-in AI client returning a fixed summary."""
    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="- people like python\n- overall positive"))]
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(ai_cleaner, "get_ai_client", lambda: fake)
    return fake


def _mock_hn(title='Python <b>rocks</b>   a   lot'):
    respx.get(url__startswith=f"{ALGOLIA}/search").mock(
        return_value=Response(200, json={"hits": [{
            "objectID": "1", "title": title, "author": "pg", "points": 10,
            "num_comments": 2, "created_at": "2026-01-01T00:00:00Z",
        }], "page": 0, "nbPages": 1})
    )


def test_tidy_text_strips_tags_and_whitespace():
    assert ai_cleaner.tidy_text("Hello <b>world</b>\n\n\n\n  spaced   out ") == "Hello world\n\nspaced out"
    assert ai_cleaner.tidy_text(None) is None
    assert len(ai_cleaner.tidy_text("x" * 10000)) == ai_cleaner.MAX_TEXT


def test_tidy_item_cleans_and_drops_empties():
    item = {"text": "a <i>b</i>", "title": "  t  ", "empty": "", "none": None, "list": [], "keep": 0}
    out = ai_cleaner.tidy_item(item)
    assert out["text"] == "a b"
    assert out["title"] == "t"
    assert "empty" not in out and "none" not in out and "list" not in out
    assert out["keep"] == 0  # falsy but meaningful values survive


@respx.mock
def test_clean_without_ai_tidies_and_drops_raw(client):
    _mock_hn()
    resp = client.post(
        "/api/v1/social/hackernews",
        json={"query_type": "search", "identifier": "python", "limit": 5, "clean": True},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == []                       # raw payloads dropped
    assert body["posts"][0]["text"] == "Python rocks a lot"  # HTML + whitespace gone
    assert body["summary"] is None                  # no AI configured -> tidy only


@respx.mock
def test_clean_with_ai_adds_summary(client, fake_ai):
    _mock_hn()
    resp = client.post(
        "/api/v1/social/hackernews",
        json={"query_type": "search", "identifier": "python", "limit": 5, "clean": True},
    )
    body = resp.json()
    assert "people like python" in body["summary"]


@respx.mock
def test_clean_does_not_corrupt_the_cache(client):
    _mock_hn()
    first = client.post(
        "/api/v1/social/hackernews",
        json={"query_type": "search", "identifier": "python", "limit": 5, "clean": True},
    ).json()
    assert first["data"] == []

    # same request without clean is served from cache — raw payloads intact
    second = client.post(
        "/api/v1/social/hackernews",
        json={"query_type": "search", "identifier": "python", "limit": 5},
    ).json()
    assert second["cached"] is True
    assert second["data"], "cache must keep the raw version"
    assert second["data"][0]["title"] == 'Python <b>rocks</b>   a   lot'


@respx.mock
def test_clean_works_on_legacy_reddit_route(client):
    """/social/reddit hits the legacy literal route, not /{platform} — it must
    honor clean too (regression: the flag was silently ignored there)."""
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "old_reddit_listing.html").read_text()
    respx.get(url__startswith="https://old.reddit.com/r/python/").mock(
        return_value=Response(200, text=html)
    )
    body = client.post(
        "/api/v1/social/reddit",
        json={"query_type": "posts", "identifier": "python", "limit": 3, "clean": True},
    ).json()
    assert body["success"] is True
    assert body["data"] == []


@respx.mock
def test_multi_search_clean_summary(client, fake_ai):
    _mock_hn()
    resp = client.post(
        "/api/v1/social/search",
        json={"query": "python", "platforms": ["hackernews"], "limit": 5, "clean": True},
    )
    body = resp.json()
    assert body["success"] is True
    assert "overall positive" in body["summary"]
    assert body["results"]["hackernews"]["data"] == []


@respx.mock
def test_run_clean_tidies_items_and_summarizes(client, fake_ai):
    _mock_hn()
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "python", "max_items": 5, "clean": True},
    )
    run = client.get(f"/api/v1/runs/{resp.json()['id']}").json()
    assert run["status"] == "SUCCEEDED"
    assert "people like python" in run["summary"]

    items = client.get(f"/api/v1/datasets/{resp.json()['dataset_id']}/items").json()["items"]
    assert items[0]["text"] == "Python rocks a lot"


@respx.mock
def test_run_without_clean_has_no_summary(client, fake_ai):
    _mock_hn()
    resp = client.post(
        "/api/v1/runs",
        json={"platform": "hackernews", "query_type": "search",
              "identifier": "python", "max_items": 5},
    )
    run = client.get(f"/api/v1/runs/{resp.json()['id']}").json()
    assert run["summary"] is None
