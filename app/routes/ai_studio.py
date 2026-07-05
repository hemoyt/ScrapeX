"""AI Studio route — a direct prompt/response console for the configured AI provider."""
from fastapi import APIRouter

from app.models import AIStudioRequest, AIStudioResponse
from app.services.ai_studio import run_prompt

router = APIRouter()


@router.post("/ai/studio", response_model=AIStudioResponse)
async def studio(req: AIStudioRequest):
    """Send one prompt straight to the configured AI provider — no tools, no
    loop. Useful to confirm a provider/model works, or to try one before
    pointing the research agent, competitor discovery, or extraction at it."""
    return await run_prompt(req)
