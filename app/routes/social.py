"""Social media routes — unified /{platform} API, multi-platform search, legacy aliases."""
import asyncio

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models import (
    MultiSearchRequest,
    MultiSearchResponse,
    RedditRequest,
    SocialQueryType,
    SocialRequest,
    SocialResponse,
    TwitterRequest,
)
from app.services.social_registry import get_platform, platform_names

router = APIRouter()

# NOTE: route order matters — literal paths (/search, /twitter, /reddit) must be
# registered before the dynamic /{platform} route.


async def _finalize(resp: SocialResponse, clean: bool, context: str) -> SocialResponse:
    """Shared clean pipeline: tidy the output and pass it through the AI for a
    summary before it goes out. Copies first — the cache holds the raw version."""
    if not clean:
        return resp
    from app.services.ai_cleaner import summarize_response, tidy_response

    resp = tidy_response(resp.model_copy(deep=True))
    resp.summary = await summarize_response(resp, context)
    return resp


@router.post("/search", response_model=MultiSearchResponse)
async def social_search(req: MultiSearchRequest):
    """Search one keyword across multiple social platforms concurrently."""
    valid = [p for p in req.platforms if p.lower() in platform_names()]
    if not valid:
        raise HTTPException(
            status_code=400,
            detail=f"No valid platforms in {req.platforms}. Available: {platform_names()}",
        )

    async def run_one(platform: str) -> SocialResponse:
        try:
            svc = get_platform(platform)
            return await asyncio.wait_for(
                svc.fetch(SocialRequest(
                    query_type=SocialQueryType.search,
                    identifier=req.query,
                    limit=req.limit,
                )),
                timeout=settings.social_timeout,
            )
        except asyncio.TimeoutError:
            return SocialResponse(
                success=False, platform=platform, status="error",
                query_type="search", error=f"Timed out after {settings.social_timeout}s",
            )
        except Exception as e:
            return SocialResponse(
                success=False, platform=platform, status="error",
                query_type="search", error=str(e),
            )

    responses = await asyncio.gather(*(run_one(p) for p in valid))
    results = {p.lower(): r for p, r in zip(valid, responses)}

    summary = None
    if req.clean:
        from app.services.ai_cleaner import summarize_multi, tidy_response

        results = {p: tidy_response(r.model_copy(deep=True)) for p, r in results.items()}
        summary = await summarize_multi(req.query, results)

    return MultiSearchResponse(
        success=any(r.success for r in results.values()),
        query=req.query,
        summary=summary,
        results=results,
    )


# --- Legacy aliases (back-compat with the original API shape) ---


@router.post("/twitter", response_model=SocialResponse)
async def scrape_twitter(req: TwitterRequest):
    """Scrape Twitter/X. Accepts the legacy shape (username / tweet_url) or the unified shape."""
    if req.identifier:
        unified = SocialRequest(
            query_type=req.query_type or SocialQueryType.posts,
            identifier=req.identifier,
            limit=req.limit,
            options=req.options,
        )
    elif req.tweet_url:
        unified = SocialRequest(query_type=SocialQueryType.post, identifier=req.tweet_url)
    elif req.username:
        unified = SocialRequest(
            query_type=SocialQueryType.posts, identifier=req.username, limit=req.max_tweets
        )
    else:
        raise HTTPException(status_code=400, detail="Provide either username, tweet_url, or identifier")
    resp = await get_platform("twitter").fetch(unified)
    return await _finalize(resp, req.clean, unified.identifier)


@router.post("/reddit", response_model=SocialResponse)
async def scrape_reddit(req: RedditRequest):
    """Scrape Reddit. Accepts the legacy shape (subreddit / post_url) or the unified shape."""
    if req.identifier:
        unified = SocialRequest(
            query_type=req.query_type or SocialQueryType.posts,
            identifier=req.identifier,
            limit=req.limit,
            options=req.options,
        )
    elif req.post_url:
        unified = SocialRequest(query_type=SocialQueryType.post, identifier=req.post_url)
    elif req.subreddit:
        unified = SocialRequest(
            query_type=SocialQueryType.posts,
            identifier=req.subreddit,
            limit=req.limit,
            options={"listing": req.listing},
        )
    else:
        raise HTTPException(status_code=400, detail="Provide either subreddit, post_url, or identifier")
    resp = await get_platform("reddit").fetch(unified)
    return await _finalize(resp, req.clean, unified.identifier)


# --- Unified endpoint ---


@router.post("/{platform}", response_model=SocialResponse)
async def social_unified(platform: str, req: SocialRequest):
    """Unified social scraping: profile, posts, single post, or search on any platform.

    With clean=true the response is tidied (HTML noise stripped, raw payloads
    dropped) and — when an AI provider is configured — passed through the AI
    for a plain-language `summary` before it comes back."""
    try:
        svc = get_platform(platform)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown platform '{platform}'. Available: {platform_names()}",
        )
    resp = await svc.fetch(req)
    return await _finalize(resp, req.clean, req.identifier)
