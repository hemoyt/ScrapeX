"""AI extraction route."""
from fastapi import APIRouter
from app.models import ExtractRequest
from app.services import ScraperService, AIExtractor

router = APIRouter()


@router.post("/extract")
async def extract_data(req: ExtractRequest):
    """Scrape a URL and extract structured data using AI.

    Example prompt: 'Extract all product names, prices, and ratings'
    """
    # Scrape first
    scraper = ScraperService()
    try:
        data = scraper.scrape(req.url)
    except Exception as e:
        return {"success": False, "url": req.url, "error": f"Scrape failed: {e}"}
    finally:
        scraper.close()

    # Extract with AI
    extractor = AIExtractor()
    result = extractor.extract(
        content=data["content"],
        prompt=req.prompt,
        url=req.url,
    )

    return {
        "success": "error" not in result,
        "url": req.url,
        "title": data["title"],
        "prompt": req.prompt,
        "extracted": result,
    }
