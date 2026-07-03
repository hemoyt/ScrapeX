from app.routes.health import router
from app.routes.scrape import router as scrape_router
from app.routes.social import router as social_router
from app.routes.extract import router as extract_router

__all__ = ["router", "scrape_router", "social_router", "extract_router"]
