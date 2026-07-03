"""Optional API-key auth. Enforced only when SCRAPEX_API_KEYS is set."""
from fastapi import HTTPException, Request

from app.config import settings


def _valid_keys() -> set[str]:
    if not settings.api_keys:
        return set()
    return {k.strip() for k in settings.api_keys.split(",") if k.strip()}


async def require_api_key(request: Request) -> None:
    keys = _valid_keys()
    if not keys:
        return  # auth disabled

    supplied = request.headers.get("x-api-key")
    if not supplied:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()

    if supplied not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
