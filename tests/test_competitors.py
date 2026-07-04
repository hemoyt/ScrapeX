"""Competitor discovery tests — mocked LLM + mocked enrichment HTTP."""
import json
from types import SimpleNamespace

import respx
from httpx import Response

from app.services import competitors as comp_module
from app.services.competitors import CompetitorFinder
from app.models import CompetitorRequest

DDG_HTML = """
<html><body>
<div class="result">
  <h2 class="result__title"><a href="https://example.com/vs">Firecrawl vs alternatives</a></h2>
  <a class="result__snippet">Comparison of scraping APIs.</a>
</div>
</body></html>
"""

LLM_JSON = {
    "competitors": [
        {
            "name": "Firecrawl",
            "website": "https://firecrawl.dev",
            "description": "Open-source web scraping API for LLMs.",
            "twitter": "firecrawl_dev",
            "youtube": None,
        },
        {
            "name": "Tavily",
            "website": "https://tavily.com",
            "description": "Search API for AI agents.",
            "twitter": None,
            "youtube": None,
        },
    ]
}

FX_USER = {
    "code": 200,
    "user": {
        "screen_name": "firecrawl_dev", "name": "Firecrawl", "followers": 20000,
        "following": 10, "tweets": 500, "description": "Web data for LLMs",
        "url": "https://x.com/firecrawl_dev", "verification": {"verified": False},
    },
}

HN_HITS = {"hits": [{
    "objectID": "1", "title": "Show HN: Firecrawl", "author": "someone",
    "points": 500, "num_comments": 100, "created_at": "2026-01-01T00:00:00Z",
    "url": "https://firecrawl.dev",
}]}


class FakeLLM:
    def __init__(self, payload):
        async def create(**kwargs):
            self.last_kwargs = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _mock_network():
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
        return_value=Response(200, text=DDG_HTML)
    )
    respx.get(url__startswith="https://api.fxtwitter.com/").mock(
        return_value=Response(200, json=FX_USER)
    )
    respx.get(url__startswith="https://hn.algolia.com/api/v1/search").mock(
        return_value=Response(200, json=HN_HITS)
    )
    respx.get(url__startswith="https://old.reddit.com/search").mock(
        return_value=Response(200, text="<html></html>")
    )


@respx.mock
async def test_discover_and_enrich():
    _mock_network()
    finder = CompetitorFinder()
    finder.client = FakeLLM(LLM_JSON)

    result = await finder.find(CompetitorRequest(product="ScrapeX", max_competitors=5))

    assert result.success is True
    assert result.status == "ok"
    names = [c.name for c in result.competitors]
    assert names == ["Firecrawl", "Tavily"]

    fc = result.competitors[0]
    assert fc.handles["twitter"] == "firecrawl_dev"
    assert fc.profiles["twitter"].followers == 20000
    assert fc.mentions["hackernews"][0].stats["points"] == 500
    # grounding sources included
    assert result.sources and "example.com" in result.sources[0]["url"]


@respx.mock
async def test_no_llm_degrades():
    _mock_network()
    finder = CompetitorFinder()
    finder.client = None

    result = await finder.find(CompetitorRequest(product="ScrapeX"))

    assert result.success is False
    assert result.status == "no_llm"
    assert result.competitors == []
    assert result.sources  # grounding search still returned
    assert "OPENROUTER" in result.error.upper()


@respx.mock
async def test_enrich_disabled_skips_social(client):
    _mock_network()
    finder = CompetitorFinder()
    finder.client = FakeLLM(LLM_JSON)

    result = await finder.find(CompetitorRequest(product="ScrapeX", enrich=False))
    assert result.success is True
    assert result.competitors[0].profiles == {}
    assert result.competitors[0].mentions == {}


@respx.mock
def test_route_no_llm(client, monkeypatch):
    _mock_network()
    resp = client.post("/api/v1/competitors", json={"product": "ScrapeX"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] in ("no_llm", "ok")  # no key in test env -> no_llm


def test_ui_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ScrapeX" in resp.text


def test_api_index(client):
    resp = client.get("/api")
    body = resp.json()
    assert body["endpoints"]["competitors"] == "/api/v1/competitors"
