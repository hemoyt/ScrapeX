"""Bluesky scraper — official public AppView API, no auth required."""
import re
from typing import Any, Dict, List, Optional

import httpx

from app.models import SocialPost, SocialProfile, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import RELIABLE, SocialPlatform


class BlueskyService(SocialPlatform):
    """Fully keyless via public.api.bsky.app (the public AppView).

    Identifiers:
        profile / posts -> handle, e.g. "bsky.app" or "jay.bsky.team"
        post            -> bsky.app post URL or at:// URI
        search          -> search query
    """

    name = "bluesky"
    capabilities = {
        SocialQueryType.profile: RELIABLE,
        SocialQueryType.posts: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.search: RELIABLE,
    }

    BASE = "https://public.api.bsky.app/xrpc"
    # public.api.bsky.app 403s searchPosts from some (datacenter) IPs while
    # api.bsky.app serves it keyless — chain them.
    SEARCH_BASES = ["https://public.api.bsky.app/xrpc", "https://api.bsky.app/xrpc"]

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = make_async_client(headers={"Accept": "application/json"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> str:
        try:
            resp = await self.client.get(
                f"{self.BASE}/app.bsky.actor.getProfile", params={"actor": "bsky.app"}, timeout=5
            )
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    async def _get(self, endpoint: str, params: dict) -> dict:
        resp = await self.client.get(f"{self.BASE}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        user = await self._get("app.bsky.actor.getProfile", {"actor": self._clean_handle(identifier)})
        return self.ok(profile=self._to_profile(user), data=[user], source="bsky_appview")

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        data = await self._get(
            "app.bsky.feed.getAuthorFeed",
            {"actor": self._clean_handle(identifier), "limit": min(limit, 50)},
        )
        feed = data.get("feed", [])
        posts = [self._to_post(item.get("post", {})) for item in feed[:limit]]
        return self.ok(posts=posts, data=feed[:limit], source="bsky_appview")

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        uri = await self._resolve_post_uri(identifier)
        data = await self._get(
            "app.bsky.feed.getPostThread", {"uri": uri, "depth": min(limit, 20)}
        )
        thread = data.get("thread", {})
        main = self._to_post(thread.get("post", {}))
        replies = [
            self._to_post(r.get("post", {}))
            for r in thread.get("replies", [])[:limit]
            if isinstance(r, dict) and r.get("post")
        ]
        main.extra["replies"] = [r.model_dump(exclude_none=True) for r in replies]
        return self.ok(posts=[main], data=[thread], source="bsky_appview")

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        params = {"q": identifier, "limit": min(limit, 50)}
        last_error: Optional[Exception] = None
        for base in self.SEARCH_BASES:
            try:
                resp = await self.client.get(f"{base}/app.bsky.feed.searchPosts", params=params)
                resp.raise_for_status()
                raw = resp.json().get("posts", [])[:limit]
                return self.ok(
                    posts=[self._to_post(p) for p in raw],
                    data=raw,
                    source=base.split("//")[1].split("/")[0],
                )
            except Exception as e:
                last_error = e
        raise last_error  # type: ignore[misc]

    # --- helpers ---

    @staticmethod
    def _clean_handle(identifier: str) -> str:
        handle = identifier.strip().lstrip("@")
        m = re.search(r"bsky\.app/profile/([^/?#]+)", handle)
        if m:
            handle = m.group(1)
        return handle

    async def _resolve_post_uri(self, identifier: str) -> str:
        if identifier.startswith("at://"):
            return identifier
        m = re.search(r"bsky\.app/profile/([^/]+)/post/([^/?#]+)", identifier)
        if not m:
            raise ValueError(f"Cannot parse Bluesky post identifier: {identifier}")
        actor, rkey = m.group(1), m.group(2)
        did = actor
        if not actor.startswith("did:"):
            user = await self._get("app.bsky.actor.getProfile", {"actor": actor})
            did = user.get("did", actor)
        return f"at://{did}/app.bsky.feed.post/{rkey}"

    def _to_profile(self, user: dict) -> SocialProfile:
        return SocialProfile(
            username=user.get("handle", ""),
            display_name=user.get("displayName"),
            bio=user.get("description"),
            followers=user.get("followersCount"),
            following=user.get("followsCount"),
            posts_count=user.get("postsCount"),
            avatar_url=user.get("avatar"),
            url=f"https://bsky.app/profile/{user.get('handle', '')}",
            extra={"did": user.get("did")},
        )

    def _to_post(self, post: dict) -> SocialPost:
        record = post.get("record", {})
        author = post.get("author", {})
        uri = post.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        handle = author.get("handle", "")

        media = []
        embed = post.get("embed", {})
        for img in embed.get("images", []) or []:
            media.append({"type": "image", "url": img.get("fullsize"), "alt": img.get("alt")})

        return SocialPost(
            id=rkey or None,
            url=f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None,
            author=handle,
            text=record.get("text"),
            created_at=record.get("createdAt"),
            stats={
                "likes": post.get("likeCount", 0),
                "reposts": post.get("repostCount", 0),
                "replies": post.get("replyCount", 0),
                "quotes": post.get("quoteCount", 0),
            },
            media=media,
            extra={"uri": uri, "author_display_name": author.get("displayName")},
        )
