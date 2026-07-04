"""Hacker News scraper — official Algolia search API, no auth required."""
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from app.models import SocialPost, SocialProfile, SocialQueryType, SocialRequest, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import RELIABLE, SocialPlatform


class HackerNewsService(SocialPlatform):
    """Fully keyless via hn.algolia.com.

    Identifiers:
        profile -> username
        posts   -> front page ("front" or anything) or a username's submissions
                   (options: {"by_user": true} treats identifier as username)
        post    -> HN item URL or numeric id (returns story + top comments)
        search  -> search query (options: {"sort": "date"} for newest-first)
    """

    name = "hackernews"
    capabilities = {
        SocialQueryType.profile: RELIABLE,
        SocialQueryType.posts: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.search: RELIABLE,
    }

    BASE = "https://hn.algolia.com/api/v1"

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
            resp = await self.client.get(f"{self.BASE}/search?query=test&hitsPerPage=1", timeout=5)
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        resp = await self.client.get(f"{self.BASE}/{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        user = await self._get(f"users/{identifier.lstrip('@')}")
        profile = SocialProfile(
            username=user.get("username", identifier),
            bio=user.get("about"),
            url=f"https://news.ycombinator.com/user?id={user.get('username', identifier)}",
            extra={"karma": user.get("karma"), "created_at": user.get("created_at")},
        )
        return self.ok(profile=profile, data=[user], source="hn_algolia")

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        if options.get("by_user"):
            params = {"tags": f"story,author_{identifier}", "hitsPerPage": limit}
        else:
            params = {"tags": "front_page", "hitsPerPage": limit}
        data = await self._get("search", params)
        hits = data.get("hits", [])[:limit]
        return self.ok(posts=[self._to_post(h) for h in hits], data=hits, source="hn_algolia")

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        m = re.search(r"id=(\d+)", identifier)
        item_id = m.group(1) if m else identifier.strip()
        if not item_id.isdigit():
            raise ValueError(f"Cannot parse HN item id from: {identifier}")

        item = await self._get(f"items/{item_id}")
        post = SocialPost(
            id=str(item.get("id", item_id)),
            url=f"https://news.ycombinator.com/item?id={item_id}",
            author=item.get("author"),
            text=item.get("title") or item.get("text"),
            created_at=item.get("created_at"),
            stats={"points": item.get("points") or 0},
            extra={
                "story_url": item.get("url"),
                "comments": self._flatten_comments(item.get("children", []), limit),
            },
        )
        return self.ok(posts=[post], data=[item], source="hn_algolia")

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        path = "search_by_date" if options.get("sort") == "date" else "search"
        data = await self._get(path, {"query": identifier, "tags": "story", "hitsPerPage": limit})
        hits = data.get("hits", [])[:limit]
        return self.ok(posts=[self._to_post(h) for h in hits], data=hits, source="hn_algolia")

    async def fetch_page(
        self, req: SocialRequest, cursor: Optional[str] = None
    ) -> Tuple[SocialResponse, Optional[str]]:
        """Algolia pages through results natively via the `page` param."""
        if req.query_type not in (SocialQueryType.posts, SocialQueryType.search):
            return await super().fetch_page(req, cursor)

        page = int(cursor) if cursor else 0
        if req.query_type == SocialQueryType.posts:
            path = "search"
            if req.options.get("by_user"):
                params = {"tags": f"story,author_{req.identifier}", "hitsPerPage": req.limit}
            else:
                params = {"tags": "front_page", "hitsPerPage": req.limit}
        else:
            path = "search_by_date" if req.options.get("sort") == "date" else "search"
            params = {"query": req.identifier, "tags": "story", "hitsPerPage": req.limit}
        params["page"] = page

        data = await self._get(path, params)
        hits = data.get("hits", [])
        resp = self.ok(posts=[self._to_post(h) for h in hits], data=hits, source="hn_algolia")
        resp.query_type = req.query_type.value
        has_more = hits and page + 1 < (data.get("nbPages") or 0)
        return resp, str(page + 1) if has_more else None

    # --- helpers ---

    def _to_post(self, hit: dict) -> SocialPost:
        object_id = hit.get("objectID", "")
        return SocialPost(
            id=object_id or None,
            url=f"https://news.ycombinator.com/item?id={object_id}" if object_id else None,
            author=hit.get("author"),
            text=hit.get("title") or hit.get("story_text") or hit.get("comment_text"),
            created_at=hit.get("created_at"),
            stats={
                "points": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
            },
            extra={"story_url": hit.get("url")},
        )

    def _flatten_comments(self, children: list, limit: int) -> list:
        """Top-level comments only, text stripped of HTML tags."""
        comments = []
        for c in children[:limit]:
            text = c.get("text") or ""
            text = re.sub(r"<[^>]+>", " ", text).strip()
            if text:
                comments.append({
                    "author": c.get("author"),
                    "text": text[:1500],
                    "created_at": c.get("created_at"),
                })
        return comments
