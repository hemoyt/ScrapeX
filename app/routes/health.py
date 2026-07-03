"""Health check route."""
from fastapi import APIRouter
from datetime import datetime, timezone
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": settings.app_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
