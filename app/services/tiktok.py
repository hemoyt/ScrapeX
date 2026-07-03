"""TikTok scraper — best-effort via embedded page JSON.

TikTok server-renders a `__UNIVERSAL_DATA_FOR_REHYDRATION__` JSON blob into
profile and video pages that plain httpx can fetch. Profile info is reliable
this way; the initial HTML usually contains no video list (it hydrates
client-side), so `posts` falls back to a Playwright render when available and
otherwise degrades honestly.
"""
import json
import re
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.models import SocialPost, SocialProfile, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import BEST_EFFORT, PlatformBlocked, SocialPlatform

UNIVERSAL_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json"[^>]*>(.*?)</script>',
    re.S,
)
SIGI_RE = re.compile(r'<script id="SIGI_STATE" type="application/json"[^>]*>(.*?)</script>', re.S)


def extract_embedded_json(html: str) -> dict:
    """Return {'universal': ..., 'sigi': ...} for whichever blobs are present."""
    out: dict = {}
    m = UNIVERSAL_RE.search(html)
    if m:
        try:
            out["universal"] = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = SIGI_RE.search(html)
    if m:
        try:
            out["sigi"] = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return out


class TikTokService(SocialPlatform):
    """Identifiers:
        profile -> username (with or without @) or profile URL
        posts   -> username (needs a working browser fallback; otherwise partial)
        post    -> video URL
    Search requires signed params — honestly unsupported keyless.
    """

    name = "tiktok"
    capabilities = {
        SocialQueryType.profile: BEST_EFFORT,
        SocialQueryType.posts: BEST_EFFORT,
        SocialQueryType.post: BEST_EFFORT,
    }

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = make_async_client(headers={"Accept": "text/html"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> str:
        try:
            html = await self._fetch_html("https://www.tiktok.com/@tiktok")
            return "ok" if "universal" in extract_embedded_json(html) else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    @staticmethod
    def _clean_username(identifier: str) -> str:
        ident = identifier.strip().lstrip("@")
        m = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", ident)
        if m:
            ident = m.group(1)
        return ident

    async def _fetch_html(self, url: str) -> str:
        resp = await self.client.get(url)
        if resp.status_code in (401, 403, 429):
            raise PlatformBlocked(f"TikTok is blocking this IP (HTTP {resp.status_code}).")
        resp.raise_for_status()
        return resp.text

    async def _render_html(self, url: str) -> Optional[str]:
        """Playwright fallback — returns None when no browser is available."""
        try:
            from app.services.browser import BrowserService
            browser = BrowserService(headless=settings.browser_headless, timeout=settings.browser_timeout)
            try:
                data = await browser.render(url, wait_until="domcontentloaded")
                return data.get("html")
            finally:
                await browser.stop()
        except Exception:
            return None

    # --- profile ---

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        username = self._clean_username(identifier)
        html = await self._fetch_html(f"https://www.tiktok.com/@{username}")
        blobs = extract_embedded_json(html)
        detail = (
            blobs.get("universal", {}).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
        )
        user_info = detail.get("userInfo") or {}
        user = user_info.get("user") or {}
        stats = user_info.get("stats") or {}
        if not user.get("uniqueId"):
            msg = detail.get("statusMsg") or "no user data in page (blocked or nonexistent account)"
            raise PlatformBlocked(f"TikTok profile unavailable: {msg}")
        profile = SocialProfile(
            username=user.get("uniqueId", username),
            display_name=user.get("nickname"),
            bio=user.get("signature"),
            followers=stats.get("followerCount"),
            following=stats.get("followingCount"),
            posts_count=stats.get("videoCount"),
            avatar_url=user.get("avatarLarger") or user.get("avatarMedium"),
            url=f"https://www.tiktok.com/@{user.get('uniqueId', username)}",
            verified=user.get("verified"),
            extra={"hearts": stats.get("heartCount"), "is_private": user.get("privateAccount")},
        )
        return self.ok(profile=profile, data=[{"user": {
            k: user.get(k) for k in ("id", "uniqueId", "nickname", "signature", "verified")
        }, "stats": stats}], source="embedded_json")

    # --- posts (needs hydration -> browser fallback) ---

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        username = self._clean_username(identifier)
        url = f"https://www.tiktok.com/@{username}"

        html = await self._fetch_html(url)
        posts = self._items_from_html(html, limit)
        source = "embedded_json"
        if not posts:
            rendered = await self._render_html(url)
            if rendered:
                posts = self._items_from_html(rendered, limit)
                source = "playwright"

        if posts:
            return self.ok(posts=posts, data=[p.model_dump(exclude_none=True) for p in posts], source=source)

        # The profile itself may still be fine — degrade honestly.
        return self.partial(
            error=(
                "TikTok video lists hydrate client-side and no browser rendering "
                "succeeded from this host. Profile data (query_type=profile) and "
                "single videos (query_type=post) still work."
            ),
            source="embedded_json",
        )

    # --- single video ---

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        url = identifier.strip()
        if not url.startswith("http"):
            raise ValueError("TikTok post identifier must be a video URL")
        html = await self._fetch_html(url)
        item = self._video_detail_from_html(html)
        if not item:
            rendered = await self._render_html(url)
            if rendered:
                item = self._video_detail_from_html(rendered)
        if not item:
            raise PlatformBlocked("TikTok video data unavailable (removed, region-locked, or blocked).")
        post = self._to_post(item)
        return self.ok(posts=[post], data=[post.model_dump(exclude_none=True)], source="embedded_json")

    # --- parsers ---

    def _items_from_html(self, html: str, limit: int) -> List[SocialPost]:
        blobs = extract_embedded_json(html)
        items: List[dict] = []

        sigi = blobs.get("sigi", {})
        if isinstance(sigi.get("ItemModule"), dict):
            items.extend(sigi["ItemModule"].values())

        universal = blobs.get("universal", {}).get("__DEFAULT_SCOPE__", {})
        item_list = universal.get("webapp.user-post", {}).get("itemList") or []
        items.extend(i for i in item_list if isinstance(i, dict))

        return [self._to_post(i) for i in items[:limit]]

    @staticmethod
    def _video_detail_from_html(html: str) -> Optional[dict]:
        blobs = extract_embedded_json(html)
        universal = blobs.get("universal", {}).get("__DEFAULT_SCOPE__", {})
        item = (universal.get("webapp.video-detail", {}).get("itemInfo", {}) or {}).get("itemStruct")
        if item and item.get("id"):
            return item
        sigi = blobs.get("sigi", {})
        module = sigi.get("ItemModule")
        if isinstance(module, dict) and module:
            first = next(iter(module.values()))
            if first.get("id"):
                return first
        return None

    @staticmethod
    def _to_post(item: dict) -> SocialPost:
        author = item.get("author")
        if isinstance(author, dict):
            author_name = author.get("uniqueId")
        else:
            author_name = author
        stats = item.get("stats") or {}
        video = item.get("video") or {}
        return SocialPost(
            id=item.get("id"),
            url=f"https://www.tiktok.com/@{author_name}/video/{item.get('id')}" if author_name else None,
            author=author_name,
            text=item.get("desc"),
            created_at=str(item.get("createTime", "")) or None,
            stats={
                "likes": stats.get("diggCount", 0),
                "comments": stats.get("commentCount", 0),
                "shares": stats.get("shareCount", 0),
                "views": stats.get("playCount", 0),
            },
            media=[{"type": "video_cover", "url": video.get("cover")}] if video.get("cover") else [],
            extra={"duration": video.get("duration"), "music": (item.get("music") or {}).get("title")},
        )
