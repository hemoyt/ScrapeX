"""Research agent route — Tavily-style query -> cited answer + sources."""
from fastapi import APIRouter

from app.models import AgentRequest, AgentResponse
from app.services.agent import ResearchAgent

router = APIRouter()


@router.post("/agent", response_model=AgentResponse)
async def research(req: AgentRequest):
    """Run the research agent: searches the web and social platforms, scrapes
    pages, and returns a markdown answer with [n] citations plus the sources.

    Requires SCRAPEX_OPENROUTER_API_KEY for answer synthesis; without it the
    endpoint still returns search results (status="no_llm")."""
    agent = ResearchAgent(model=req.model)
    return await agent.run(req)
