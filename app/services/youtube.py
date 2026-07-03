"""YouTube scraper — keyless Innertube API (youtubei/v1).

YouTube HTML pages are captcha-walled for datacenter IPs, but the Innertube
JSON API (what youtube.com itself calls) works keyless with a WEB client
context. Layouts drift, so all traversal is defensive.
"""
import re
from typing import Any, Dict, List, Optional

import httpx

from app.models import SocialPost, SocialProfile, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import RELIABLE, SocialPlatform

INNERTUBE = "https://www.youtube.com/youtubei/v1"
WEB_CONTEXT = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00"}}}
# browse params for a channel's Videos tab (protobuf, stable for years)
VIDEOS_TAB_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


def _find_all(node: Any, key: str, out: list, cap: int = 100) -> None:
    """Collect every value of `key` in a nested dict/list structure."""
    if len(out) >= cap:
        return
    if isinstance(node, dict):
        if key in node:
            out.append(node[key])
        for v in node.values():
            _find_all(v, key, out, cap)
    elif isinstance(node, list):
        for v in node:
            _find_all(v, key, out, cap)


def _parse_count(text: str) -> Optional[int]:
    """'54,395 views' / '1.6M views' / '685K' -> int."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*([KMB])?", text.replace(",", ""))
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(m.group(2) or "", 1)
    return int(value * mult)


class YouTubeService(SocialPlatform):
    """Identifiers:
        profile -> @handle, channel URL, or channel id (UC...)
        posts   -> same as profile (returns the channel's recent videos)
        post    -> video URL or 11-char video id
        search  -> search query
    """

    name = "youtube"
    capabilities = {
        SocialQueryType.profile: RELIABLE,
        SocialQueryType.posts: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.search: RELIABLE,
    }

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = make_async_client(
                headers={"Content-Type": "application/json", "Accept": "application/json"}
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> str:
        try:
            data = await self._call("search", {"query": "test"})
            return "ok" if data else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    async def _call(self, endpoint: str, payload: dict) -> dict:
        body = {**WEB_CONTEXT, **payload}
        resp = await self.client.post(f"{INNERTUBE}/{endpoint}?prettyPrint=false", json=body)
        resp.raise_for_status()
        return resp.json()

    # --- search ---

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        data = await self._call("search", {"query": identifier})
        renderers: list = []
        _find_all(data, "videoRenderer", renderers, cap=limit * 3)
        posts = [self._from_video_renderer(v) for v in renderers[:limit]]
        return self.ok(
            posts=posts,
            data=[p.model_dump(exclude_none=True) for p in posts],
            source="innertube",
        )

    # --- single video ---

    @staticmethod
    def _extract_video_id(identifier: str) -> str:
        ident = identifier.strip()
        for pattern in (r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})", r"/shorts/([\w-]{11})"):
            m = re.search(pattern, ident)
            if m:
                return m.group(1)
        if re.fullmatch(r"[\w-]{11}", ident):
            return ident
        raise ValueError(f"Cannot parse YouTube video id from: {identifier}")

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        video_id = self._extract_video_id(identifier)
        data = await self._call("player", {"videoId": video_id})
        details = data.get("videoDetails") or {}
        if not details.get("videoId"):
            status = (data.get("playabilityStatus") or {}).get("status", "UNKNOWN")
            raise ValueError(f"No video details returned (playability: {status})")

        views = _parse_count(details.get("viewCount", ""))
        post = SocialPost(
            id=details.get("videoId"),
            url=f"https://www.youtube.com/watch?v={details.get('videoId')}",
            author=details.get("author"),
            text=details.get("title"),
            stats={"views": views or 0},
            media=[{
                "type": "thumbnail",
                "url": (details.get("thumbnail", {}).get("thumbnails") or [{}])[-1].get("url"),
            }],
            extra={
                "description": (details.get("shortDescription") or "")[:3000],
                "length_seconds": details.get("lengthSeconds"),
                "keywords": (details.get("keywords") or [])[:20],
                "channel_id": details.get("channelId"),
                "is_live": details.get("isLiveContent"),
            },
        )
        return self.ok(posts=[post], data=[details], source="innertube")

    # --- channel profile & videos ---

    async def _resolve_channel(self, identifier: str) -> str:
        ident = identifier.strip()
        if re.fullmatch(r"UC[\w-]{22}", ident):
            return ident
        if not ident.startswith("http"):
            ident = ident.lstrip("@")
            ident = f"https://www.youtube.com/@{ident}"
        data = await self._call("navigation/resolve_url", {"url": ident})
        browse_id = (data.get("endpoint") or {}).get("browseEndpoint", {}).get("browseId")
        if not browse_id:
            raise ValueError(f"Could not resolve YouTube channel: {identifier}")
        return browse_id

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        browse_id = await self._resolve_channel(identifier)
        data = await self._call("browse", {"browseId": browse_id})
        md = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}

        # Subscriber count lives in the header view models; hunt defensively.
        subscribers = None
        texts: list = []
        _find_all(data.get("header", {}), "content", texts, cap=200)
        for t in texts:
            if isinstance(t, str) and "subscriber" in t:
                subscribers = _parse_count(t)
                break

        profile = SocialProfile(
            username=md.get("vanityChannelUrl", "").split("/")[-1] or browse_id,
            display_name=md.get("title"),
            bio=(md.get("description") or "")[:2000],
            followers=subscribers,
            avatar_url=(md.get("avatar", {}).get("thumbnails") or [{}])[0].get("url"),
            url=md.get("vanityChannelUrl") or f"https://www.youtube.com/channel/{browse_id}",
            extra={"channel_id": browse_id, "keywords": md.get("keywords", "")[:200]},
        )
        return self.ok(profile=profile, data=[md], source="innertube")

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        browse_id = await self._resolve_channel(identifier)
        data = await self._call("browse", {"browseId": browse_id, "params": VIDEOS_TAB_PARAMS})

        items: list = []
        _find_all(data, "richItemRenderer", items, cap=limit * 3)
        posts = []
        channel_name = ((data.get("metadata") or {}).get("channelMetadataRenderer") or {}).get("title")
        for item in items:
            content = item.get("content", {})
            if "videoRenderer" in content:
                posts.append(self._from_video_renderer(content["videoRenderer"]))
            elif "lockupViewModel" in content:
                post = self._from_lockup(content["lockupViewModel"])
                if post:
                    post.author = post.author or channel_name
                    posts.append(post)
            if len(posts) >= limit:
                break
        return self.ok(
            posts=posts,
            data=[p.model_dump(exclude_none=True) for p in posts],
            source="innertube",
        )

    # --- renderer parsers ---

    @staticmethod
    def _runs_text(node: dict) -> Optional[str]:
        if not isinstance(node, dict):
            return None
        if "simpleText" in node:
            return node["simpleText"]
        runs = node.get("runs") or []
        return "".join(r.get("text", "") for r in runs) or None

    def _from_video_renderer(self, v: dict) -> SocialPost:
        video_id = v.get("videoId")
        views_text = self._runs_text(v.get("viewCountText", {})) or ""
        return SocialPost(
            id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            author=self._runs_text(v.get("ownerText", {})),
            text=self._runs_text(v.get("title", {})),
            created_at=self._runs_text(v.get("publishedTimeText", {})),
            stats={"views": _parse_count(views_text) or 0},
            media=[{
                "type": "thumbnail",
                "url": (v.get("thumbnail", {}).get("thumbnails") or [{}])[-1].get("url"),
            }],
            extra={
                "length": self._runs_text(v.get("lengthText", {})),
                "snippet": self._runs_text(
                    (v.get("detailedMetadataSnippets") or [{}])[0].get("snippetText", {})
                ),
            },
        )

    def _from_lockup(self, lockup: dict) -> Optional[SocialPost]:
        """Channel Videos tab now uses lockupViewModel instead of videoRenderer."""
        if lockup.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            return None
        video_id = lockup.get("contentId")
        if not video_id:
            return None
        meta = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = (meta.get("title") or {}).get("content")

        # Metadata rows carry strings like "685K views" / "20 hours ago" / channel name
        parts: List[str] = []
        rows = (meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", []))
        for row in rows:
            for part in row.get("metadataParts", []):
                text = (part.get("text") or {}).get("content")
                if text:
                    parts.append(text)
        views = next((_parse_count(p) for p in parts if "view" in p), None)
        published = next((p for p in parts if re.search(r"\bago\b|\bStreamed\b", p)), None)
        author = next((p for p in parts if "view" not in p and not re.search(r"\bago\b", p)), None)

        length = None
        badges: list = []
        _find_all(lockup.get("contentImage", {}), "thumbnailBadgeViewModel", badges, cap=5)
        for b in badges:
            if re.fullmatch(r"[\d:]+", b.get("text", "")):
                length = b["text"]
                break

        return SocialPost(
            id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            author=author,
            text=title,
            created_at=published,
            stats={"views": views or 0},
            media=[{
                "type": "thumbnail",
                "url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            }],
            extra={"length": length},
        )
