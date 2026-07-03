"""Health check route with per-platform capability matrix."""
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.config import settings
from app.ratelimit import limiter

router = APIRouter()


@router.get("/health")
@limiter.exempt
async def health(request: Request, probe: bool = False):
    result = {
        "status": "healthy",
        "version": settings.app_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Platform capability matrix (added once the social registry exists)
    try:
        from app.services.social_registry import platform_matrix
        result["platforms"] = platform_matrix()
    except ImportError:
        pass

    if probe:
        try:
            from app.services.social_registry import probe_platforms
            result["probes"] = await probe_platforms()
        except ImportError:
            pass

    return result
