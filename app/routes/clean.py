"""Data Viewer clean route — reshape/clean any list of scraped rows with a
free-text prompt (see app.services.ai_cleaner.clean_with_prompt)."""
from fastapi import APIRouter

from app.models import CleanRequest, CleanResponse
from app.services.ai_cleaner import clean_with_prompt

router = APIRouter()


@router.post("/clean", response_model=CleanResponse)
async def clean_data(req: CleanRequest):
    """Clean/reshape `items` per `prompt` — used by the Data Viewer tab. Works
    with no AI configured (deterministic tidy only); with one configured it
    reshapes, filters, dedupes, or summarizes per the instructions."""
    result = await clean_with_prompt(req.items, req.prompt, req.context or "")
    return CleanResponse(success=True, **result)
