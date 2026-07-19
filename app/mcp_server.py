"""ScrapeX as an MCP server — `python -m app.mcp_server` (or `npx scrapx mcp`).

Exposes the same engine that powers the HTTP API as Model Context Protocol
tools over stdio, so MCP clients (Claude Desktop, Claude Code, Cursor, ...)
can search the web, scrape pages, query 10 social platforms, and start
dataset runs natively. Tools call the service layer in-process — the HTTP
server does not need to be running.

Runs started here land in the same SQLite store as the API's
(SCRAPEX_DB_FILE), so a run started from an MCP client can be exported
from the web UI later, and vice versa.

Claude Code:    claude mcp add scrapex -- npx scrapx mcp
Claude Desktop: {"mcpServers": {"scrapex": {"command": "npx", "args": ["scrapx", "mcp"]}}}
"""
import asyncio
import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from app.models import (
    AgentRequest,
    ProfileFindRequest,
    RunRequest,
    SocialQueryType,
    SocialRequest,
)
from app.services.social_registry import get_platform, platform_names

mcp = FastMCP("scrapex_mcp")

QUERY_TYPES = "profile | posts | post | search"


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _social_payload(resp) -> Dict[str, Any]:
    """SocialResponse → context-friendly dict: keep the normalized posts and
    profile plus honest status, drop the bulky raw data[] payloads."""
    out = resp.model_dump(exclude_none=True)
    out.pop("data", None)
    return out


async def _fetch_social(platform: str, query_type: str, identifier: str,
                        limit: int, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    svc = get_platform(platform.lower())
    try:
        resp = await svc.fetch(SocialRequest(
            query_type=SocialQueryType(query_type),
            identifier=identifier,
            limit=limit,
            options=options or {},
        ))
        return _social_payload(resp)
    finally:
        await svc.aclose()


@mcp.tool(
    name="scrapex_web_search",
    annotations={"title": "Web Search", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_web_search(query: str, num_results: int = 5) -> str:
    """Search the live web (DuckDuckGo with Startpage fallback), keyless.

    Args:
        query: The search query.
        num_results: How many results to return (1-10).

    Returns JSON: {"results": [{"title", "url", "snippet"}, ...]}
    """
    from app.services.search import SearchService

    results = await SearchService().search(query, num_results=max(1, min(num_results, 10)))
    return _json({"results": results})


@mcp.tool(
    name="scrapex_scrape_url",
    annotations={"title": "Scrape URL", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_scrape_url(url: str, max_chars: int = 20000) -> str:
    """Scrape any web page into clean markdown plus metadata and links.

    Args:
        url: The page URL to scrape.
        max_chars: Truncate the markdown content to this many characters
            (default 20000) to keep responses context-friendly.

    Returns JSON: {"title", "content" (markdown), "truncated", "links": [...]}
    On a fetch error returns {"error": "..."} — the page may be down or
    blocking; try scrapex_web_search to find an alternative source.
    """
    from app.services.scraper import ScraperService

    scraper = ScraperService()
    try:
        data = await asyncio.to_thread(scraper.scrape, url)
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}", "url": url})
    finally:
        scraper.close()
    content = data.get("content") or ""
    return _json({
        "url": url,
        "title": data.get("title"),
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
        "links": (data.get("links") or [])[:50],
    })


@mcp.tool(
    name="scrapex_social",
    annotations={"title": "Social Platform Query", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_social(platform: str, query_type: str, identifier: str, limit: int = 10) -> str:
    """Query one social platform, keyless: a profile, a user/community's
    posts, a single post, or a keyword search.

    Args:
        platform: One of reddit, hackernews, bluesky, mastodon, youtube,
            twitter, instagram, tiktok, linkedin, facebook.
        query_type: profile | posts | post | search (support varies by
            platform — an unsupported combination returns the supported list).
        identifier: Username/handle, subreddit, URL, or search query.
        limit: Max posts to return (1-50).

    Returns JSON with an honest "status" field: ok | partial | blocked |
    error — blocked means the platform walls anonymous access; that is
    reported, never faked.
    """
    if platform.lower() not in platform_names():
        return _json({"error": f"Unknown platform '{platform}'. Available: {platform_names()}"})
    try:
        SocialQueryType(query_type)
    except ValueError:
        return _json({"error": f"Invalid query_type '{query_type}'. Use one of: {QUERY_TYPES}"})
    return _json(await _fetch_social(platform, query_type, identifier, max(1, min(limit, 50))))


@mcp.tool(
    name="scrapex_social_search",
    annotations={"title": "Cross-Platform Social Search", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_social_search(query: str, platforms: Optional[List[str]] = None, limit: int = 5) -> str:
    """Search one keyword across several social platforms concurrently.

    Args:
        query: The search keyword(s).
        platforms: Which platforms to search (default: reddit, hackernews,
            bluesky — the reliable keyless search platforms).
        limit: Max posts per platform (1-20).

    Returns JSON: {"results": {platform: {status, posts: [...]}, ...}}
    """
    targets = [p.lower() for p in (platforms or ["reddit", "hackernews", "bluesky"])]
    unknown = [p for p in targets if p not in platform_names()]
    if unknown:
        return _json({"error": f"Unknown platforms {unknown}. Available: {platform_names()}"})
    limit = max(1, min(limit, 20))
    responses = await asyncio.gather(
        *(_fetch_social(p, "search", query, limit) for p in targets),
        return_exceptions=True,
    )
    results = {
        p: (r if isinstance(r, dict) else {"status": "error", "error": str(r)})
        for p, r in zip(targets, responses)
    }
    return _json({"query": query, "results": results})


@mcp.tool(
    name="scrapex_find_profiles",
    annotations={"title": "Find Username Across Platforms", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_find_profiles(username: str, platforms: Optional[List[str]] = None) -> str:
    """Check every profile-capable social platform concurrently for a
    username and return each public profile found.

    Args:
        username: The bare handle to look for (no @ needed).
        platforms: Optional subset to check (default: all profile-capable).

    Returns JSON: {"found": [...], "checked": [...], "results": {platform: profile-or-status}}
    """
    from fastapi import HTTPException

    from app.routes.profiles import find_profiles

    try:
        resp = await find_profiles(ProfileFindRequest(username=username, platforms=platforms))
    except HTTPException as e:
        return _json({"error": str(e.detail)})
    out = resp.model_dump(exclude_none=True)
    for r in out.get("results", {}).values():
        if isinstance(r, dict):
            r.pop("data", None)
    return _json(out)


@mcp.tool(
    name="scrapex_research",
    annotations={"title": "Research Agent", "readOnlyHint": True, "openWorldHint": True},
)
async def scrapex_research(query: str, depth: str = "basic", max_sources: int = 5) -> str:
    """Run the full ScrapeX research agent: an LLM tool-loop over web search,
    scraping, and social platforms that returns a cited answer.

    Requires an AI provider configured in ScrapeX (Settings tab or
    SCRAPEX_AI_* env vars). Without one it degrades to search-only results
    with status "no_llm" instead of failing.

    Args:
        query: Natural-language research question.
        depth: "basic" (3 tool steps) or "advanced" (8 steps, slower).
        max_sources: Cap on cited sources (1-20).

    Returns JSON: {"answer" (markdown with [n] citations), "sources", "status"}
    """
    from app.services.agent import ResearchAgent

    if depth not in ("basic", "advanced"):
        depth = "basic"
    resp = await ResearchAgent().run(AgentRequest(
        query=query, depth=depth, max_sources=max(1, min(max_sources, 20)),
    ))
    out = resp.model_dump(exclude_none=True)
    out.pop("steps", None)  # the full trace is API-only; keep MCP output lean
    return _json(out)


def _run_store():
    """Runs share the API server's SQLite store; hydrate it once on first use
    so runs started over HTTP are visible here too."""
    from app.services.datasets import run_store

    run_store.load()
    return run_store


@mcp.tool(
    name="scrapex_start_run",
    annotations={"title": "Start Dataset Run", "readOnlyHint": False,
                 "destructiveHint": False, "openWorldHint": True},
)
async def scrapex_start_run(platform: str, query_type: str, identifier: str, max_items: int = 100) -> str:
    """Start a background dataset run that paginates a platform with real
    cursors until it has max_items (up to the server cap) — the way to get
    hundreds of items instead of one page. Poll scrapex_get_run until the
    status is terminal, then read scrapex_dataset_items.

    Args:
        platform: Platform to paginate (reddit, hackernews, bluesky, and
            mastodon paginate natively; others collect a single page).
        query_type: profile | posts | post | search.
        identifier: Username/handle, subreddit, URL, or search query.
        max_items: Stop after this many items (1-5000, server may cap lower).

    Returns JSON: the run — {"id", "dataset_id", "status": "READY", ...}
    """
    from app.services.datasets import execute_run

    if platform.lower() not in platform_names():
        return _json({"error": f"Unknown platform '{platform}'. Available: {platform_names()}"})
    try:
        qt = SocialQueryType(query_type)
    except ValueError:
        return _json({"error": f"Invalid query_type '{query_type}'. Use one of: {QUERY_TYPES}"})
    store = _run_store()
    run = store.create(RunRequest(
        platform=platform, query_type=qt, identifier=identifier,
        max_items=max(1, min(max_items, 5000)),
    ))
    asyncio.create_task(execute_run(run.id))
    return _json(run.model_dump(exclude_none=True))


@mcp.tool(
    name="scrapex_get_run",
    annotations={"title": "Get Run Status", "readOnlyHint": True},
)
async def scrapex_get_run(run_id: str) -> str:
    """Get a dataset run's status. Terminal states: SUCCEEDED, TIMED_OUT
    (partial data kept), ABORTED, FAILED (error explains why).

    Args:
        run_id: The id returned by scrapex_start_run.

    Returns JSON: the run, including item_count and dataset_id.
    """
    run = _run_store().runs.get(run_id)
    if run is None:
        return _json({"error": f"Run '{run_id}' not found."})
    return _json(run.model_dump(exclude_none=True))


@mcp.tool(
    name="scrapex_dataset_items",
    annotations={"title": "Read Dataset Items", "readOnlyHint": True},
)
async def scrapex_dataset_items(dataset_id: str, offset: int = 0, limit: int = 50) -> str:
    """Page through the items a dataset run collected.

    Args:
        dataset_id: The dataset_id from the run.
        offset: Items to skip (for paging).
        limit: Items to return (1-200).

    Returns JSON: {"total", "offset", "count", "items": [...]}
    """
    ds = _run_store().datasets.get(dataset_id)
    if ds is None:
        return _json({"error": f"Dataset '{dataset_id}' not found."})
    limit = max(1, min(limit, 200))
    page = ds.items[offset:offset + limit]
    return _json({"dataset_id": dataset_id, "total": len(ds.items),
                  "offset": offset, "count": len(page), "items": page})


if __name__ == "__main__":
    mcp.run()
