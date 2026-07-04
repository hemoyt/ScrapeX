"""Competitor discovery route."""
from fastapi import APIRouter

from app.models import CompetitorRequest, CompetitorResponse
from app.services.competitors import CompetitorFinder

router = APIRouter()


@router.post("/competitors", response_model=CompetitorResponse)
async def find_competitors(req: CompetitorRequest):
    """Discover a product's competitors (LLM grounded on live web search) and
    enrich each with social profiles and recent Reddit/Hacker News mentions.

    Requires SCRAPEX_OPENROUTER_API_KEY; without it returns the grounding
    search results with status="no_llm"."""
    return await CompetitorFinder().find(req)
