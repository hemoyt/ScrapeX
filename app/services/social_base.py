"""Base class for all social platform scrapers."""
import hashlib
from abc import ABC
from typing import Any, Dict

from app.config import settings
from app.models import SocialQueryType, SocialRequest, SocialResponse
from app.services.cache import social_cache

RELIABLE = "reliable"
BEST_EFFORT = "best_effort"
UNSUPPORTED = "unsupported"


class PlatformBlocked(Exception):
    """Raised by platform services when the platform is blocking us (login wall,
    captcha, rate limit). Carries any partial data worth returning."""

    def __init__(self, message: str, partial: dict | None = None):
        super().__init__(message)
        self.partial = partial or {}


class SocialPlatform(ABC):
    """Template for platform scrapers.

    Subclasses set `name` + `capabilities` and implement whichever of
    get_profile / get_posts / get_post / search they support. Implementations
    return SocialResponse and may raise — fetch() normalizes everything so a
    platform error never escapes as an exception.
    """

    name: str = ""
    # query_type -> RELIABLE | BEST_EFFORT | UNSUPPORTED
    capabilities: Dict[SocialQueryType, str] = {}

    async def fetch(self, req: SocialRequest) -> SocialResponse:
        cap = self.capabilities.get(req.query_type, UNSUPPORTED)
        if cap == UNSUPPORTED:
            supported = [qt.value for qt, c in self.capabilities.items() if c != UNSUPPORTED]
            return SocialResponse(
                success=False,
                platform=self.name,
                status="error",
                query_type=req.query_type.value,
                error=f"{self.name} does not support query_type={req.query_type.value}. Supported: {supported}",
            )

        cache_key = self._cache_key(req)
        cached = social_cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

        handler = {
            SocialQueryType.profile: self.get_profile,
            SocialQueryType.posts: self.get_posts,
            SocialQueryType.post: self.get_post,
            SocialQueryType.search: self.search,
        }[req.query_type]

        try:
            resp = await handler(req.identifier, req.limit, req.options)
        except PlatformBlocked as e:
            return SocialResponse(
                success=False,
                platform=self.name,
                status="blocked",
                query_type=req.query_type.value,
                data=[e.partial] if e.partial else [],
                error=str(e),
            )
        except Exception as e:
            return SocialResponse(
                success=False,
                platform=self.name,
                status="error",
                query_type=req.query_type.value,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            await self.aclose()

        resp.platform = resp.platform or self.name
        resp.query_type = req.query_type.value
        if resp.success:
            social_cache.set(cache_key, resp, ttl=settings.cache_ttl)
        return resp

    def _cache_key(self, req: SocialRequest) -> str:
        opts = hashlib.md5(repr(sorted(req.options.items())).encode()).hexdigest()[:8]
        return f"{self.name}:{req.query_type.value}:{req.identifier}:{req.limit}:{opts}"

    # --- override in subclasses (only the supported ones) ---

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        raise NotImplementedError

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        raise NotImplementedError

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        raise NotImplementedError

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        raise NotImplementedError

    async def probe(self) -> str:
        """Cheap live check for /health?probe=true. Returns ok | degraded | down."""
        return "ok"

    async def aclose(self) -> None:
        """Release any clients. Called by fetch() after every request."""

    # --- helpers ---

    def ok(self, **kwargs) -> SocialResponse:
        return SocialResponse(success=True, platform=self.name, status="ok", **kwargs)

    def partial(self, error: str, **kwargs) -> SocialResponse:
        return SocialResponse(success=True, platform=self.name, status="partial", error=error, **kwargs)
