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

    # Which AI brain is plugged in (never leaks the key)
    from app.services.ai_provider import provider_info
    result["ai"] = provider_info()

    if probe:
        from app.services.cache import social_cache
        from app.services.social_registry import probe_platforms

        cached = social_cache.get("health:probes")
        if cached is None:
            cached = await probe_platforms()
            social_cache.set("health:probes", cached, ttl=300)
        result["probes"] = cached

    return result
