"""Competitor discovery + analysis.

Given a product name: an LLM (grounded on live web search results) identifies
the direct competitors, then ScrapeX's own platform scrapers enrich each one
concurrently — social profiles (Twitter/YouTube) and recent mentions
(Reddit / Hacker News). Degrades to status="no_llm" without an OpenRouter key.
"""
import asyncio
import json
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.models import (
    Competitor,
    CompetitorRequest,
    CompetitorResponse,
    SocialQueryType,
    SocialRequest,
)
from app.services.search import SearchService
from app.services.social_registry import get_platform

DISCOVERY_PROMPT = """You are a market analyst. Identify the direct competitors of the product below, grounded in the search results.

PRODUCT: {product}

SEARCH RESULTS:
{results}

Return ONLY valid JSON:
{{"competitors": [{{"name": "...", "website": "https://... or null", "description": "one sentence on what it is and how it competes", "twitter": "handle without @ or null", "youtube": "@handle or channel URL or null"}}]}}

Rules: at most {limit} competitors, most direct first. Real companies/products only — if the search results don't support a competitor, leave it out. Use null for unknown handles; never guess handles."""


class CompetitorFinder:
    def __init__(self):
        from app.services.ai_provider import get_ai_client

        self.client: Optional[AsyncOpenAI] = get_ai_client()

    async def find(self, req: CompetitorRequest) -> CompetitorResponse:
        # Ground the LLM on live search either way
        search = SearchService()
        try:
            results = await search.search(f"{req.product} competitors alternatives comparison", 6)
        except Exception:
            results = []
        finally:
            await search.aclose()

        if self.client is None:
            return CompetitorResponse(
                success=False,
                product=req.product,
                sources=results,
                status="no_llm",
                error=(
                    "Competitor discovery needs an LLM. Set SCRAPEX_AI_PROVIDER + "
                    "SCRAPEX_AI_API_KEY (any of anthropic/openai/deepseek/xai/openrouter/...), "
                    "or run a local one (ollama/lmstudio). The grounding search results are included."
                ),
            )

        try:
            competitors = await self._discover(req.product, results, req.max_competitors)
        except Exception as e:
            return CompetitorResponse(
                success=False, product=req.product, sources=results,
                status="error", error=f"Discovery failed: {type(e).__name__}: {e}",
            )

        status = "ok"
        if req.enrich and competitors:
            enriched = await asyncio.gather(
                *(self._enrich(c) for c in competitors), return_exceptions=True
            )
            competitors = [
                c if isinstance(c, Competitor) else orig
                for c, orig in zip(enriched, competitors)
            ]
            if any(isinstance(c, Exception) for c in enriched):
                status = "partial"

        return CompetitorResponse(
            success=True,
            product=req.product,
            competitors=competitors,
            sources=results,
            status=status,
        )

    async def _discover(self, product: str, results: List[dict], limit: int) -> List[Competitor]:
        blocks = "\n\n".join(
            f"- {r['title']} ({r['url']})\n  {r['snippet']}" for r in results
        ) or "(no search results available — rely on well-known facts only)"

        response = await self.client.chat.completions.create(
            model=settings.agent_model or settings.ai_model,
            messages=[{
                "role": "user",
                "content": DISCOVERY_PROMPT.format(product=product, results=blocks, limit=limit),
            }],
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        competitors = []
        for item in (data.get("competitors") or [])[:limit]:
            if not item.get("name"):
                continue
            handles = {}
            if item.get("twitter"):
                handles["twitter"] = str(item["twitter"]).lstrip("@")
            if item.get("youtube"):
                handles["youtube"] = str(item["youtube"])
            competitors.append(Competitor(
                name=item["name"],
                website=item.get("website"),
                description=item.get("description"),
                handles=handles,
            ))
        return competitors

    async def _enrich(self, competitor: Competitor) -> Competitor:
        """Profiles + mentions, all best-effort and concurrent."""

        async def profile(platform: str, identifier: str):
            try:
                resp = await asyncio.wait_for(
                    get_platform(platform).fetch(SocialRequest(
                        query_type=SocialQueryType.profile, identifier=identifier, limit=1,
                    )),
                    timeout=settings.social_timeout,
                )
                if resp.success and resp.profile:
                    competitor.profiles[platform] = resp.profile
            except Exception:
                pass

        async def mentions(platform: str):
            try:
                resp = await asyncio.wait_for(
                    get_platform(platform).fetch(SocialRequest(
                        query_type=SocialQueryType.search, identifier=competitor.name, limit=3,
                    )),
                    timeout=settings.social_timeout,
                )
                if resp.posts:
                    competitor.mentions[platform] = resp.posts
            except Exception:
                pass

        tasks = [mentions("reddit"), mentions("hackernews")]
        if competitor.handles.get("twitter"):
            tasks.append(profile("twitter", competitor.handles["twitter"]))
        if competitor.handles.get("youtube"):
            tasks.append(profile("youtube", competitor.handles["youtube"]))
        await asyncio.gather(*tasks)
        return competitor
