"""Profile Finder — one username in, every platform checked concurrently.

POST /profiles/find {"username": "mkbhd"} fans out a profile lookup to all
profile-capable platforms (or the subset you pass) and reports, per platform,
whether that handle exists and what its public profile says.
"""
import asyncio

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models import (
    ProfileFindRequest,
    ProfileFindResponse,
    SocialQueryType,
    SocialRequest,
    SocialResponse,
)
from app.services.social_base import UNSUPPORTED
from app.services.social_registry import get_platform, platform_matrix

router = APIRouter()


def profile_capable_platforms() -> list[str]:
    return sorted(name for name, caps in platform_matrix().items() if "profile" in caps)


def _identifier_for(platform: str, username: str) -> str:
    """Light per-platform normalization of a bare handle."""
    handle = username.strip().lstrip("@")
    if platform == "youtube":
        return f"@{handle}"          # Innertube channel lookup wants the @handle form
    if platform == "linkedin":
        return f"in/{handle}"        # people live under /in/<handle>
    return handle


@router.post("/profiles/find", response_model=ProfileFindResponse)
async def find_profiles(req: ProfileFindRequest):
    """Search a username across social platforms and return every public profile."""
    capable = profile_capable_platforms()
    if req.platforms:
        targets = [p.lower() for p in req.platforms if p.lower() in capable]
        unknown = [p for p in req.platforms if p.lower() not in capable]
        if not targets:
            raise HTTPException(
                status_code=400,
                detail=f"None of {req.platforms} support profile lookup. Available: {capable}",
            )
    else:
        targets, unknown = capable, []

    async def check(platform: str) -> SocialResponse:
        try:
            svc = get_platform(platform)
            if svc.capabilities.get(SocialQueryType.profile, UNSUPPORTED) == UNSUPPORTED:
                return SocialResponse(
                    success=False, platform=platform, status="error",
                    query_type="profile", error=f"{platform} does not support profile lookup",
                )
            return await asyncio.wait_for(
                svc.fetch(SocialRequest(
                    query_type=SocialQueryType.profile,
                    identifier=_identifier_for(platform, req.username),
                )),
                timeout=settings.social_timeout,
            )
        except asyncio.TimeoutError:
            return SocialResponse(
                success=False, platform=platform, status="error",
                query_type="profile", error=f"Timed out after {settings.social_timeout}s",
            )
        except Exception as e:
            return SocialResponse(
                success=False, platform=platform, status="error",
                query_type="profile", error=str(e),
            )

    responses = await asyncio.gather(*(check(p) for p in targets))
    results = {p: r for p, r in zip(targets, responses)}
    for p in unknown:
        results[p] = SocialResponse(
            success=False, platform=p, status="error", query_type="profile",
            error=f"Unknown or profile-incapable platform. Available: {capable}",
        )

    found = [p for p, r in results.items() if r.success and r.profile is not None]
    return ProfileFindResponse(
        success=bool(found),
        username=req.username.strip().lstrip("@"),
        found=found,
        checked=targets,
        results=results,
    )
