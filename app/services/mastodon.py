"""Mastodon scraper — public instance REST API, no auth for public data."""
from typing import Any, Dict, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from app.models import SocialPost, SocialProfile, SocialQueryType, SocialRequest, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import BEST_EFFORT, RELIABLE, SocialPlatform


class MastodonService(SocialPlatform):
    """Keyless via any Mastodon instance's public API.

    Identifiers:
        profile / posts -> "user@instance" or plain "user" (default instance:
                           mastodon.social, override with options={"instance": ...})
        post            -> status URL, e.g. https://mastodon.social/@user/123456
        search          -> query; NOTE: large instances (incl. mastodon.social)
                           require auth for full-text search -> returns status=partial

    Reliability: profile/posts reliable; search best_effort (auth-gated on
    flagship instances — verified 401/422 from mastodon.social).
    """

    name = "mastodon"
    capabilities = {
        SocialQueryType.profile: RELIABLE,
        SocialQueryType.posts: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.search: BEST_EFFORT,
    }

    DEFAULT_INSTANCE = "mastodon.social"

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
                f"https://{self.DEFAULT_INSTANCE}/api/v1/accounts/lookup",
                params={"acct": "Mastodon"},
                timeout=5,
            )
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    def _split_identifier(self, identifier: str, options: Dict[str, Any]) -> Tuple[str, str]:
        """Return (username, instance)."""
        ident = identifier.strip().lstrip("@")
        if "@" in ident:
            user, instance = ident.split("@", 1)
        else:
            user, instance = ident, options.get("instance", self.DEFAULT_INSTANCE)
        return user, instance

    async def _lookup_account(self, user: str, instance: str) -> dict:
        resp = await self.client.get(
            f"https://{instance}/api/v1/accounts/lookup", params={"acct": user}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        user, instance = self._split_identifier(identifier, options)
        account = await self._lookup_account(user, instance)
        return self.ok(
            profile=self._to_profile(account, instance),
            data=[account],
            source=instance,
        )

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        user, instance = self._split_identifier(identifier, options)
        account = await self._lookup_account(user, instance)
        resp = await self.client.get(
            f"https://{instance}/api/v1/accounts/{account['id']}/statuses",
            params={"limit": min(limit, 40)},
        )
        resp.raise_for_status()
        statuses = resp.json()[:limit]
        return self.ok(
            posts=[self._to_post(s) for s in statuses],
            data=statuses,
            source=instance,
        )

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        # Status URL like https://instance/@user/113861234567 or /statuses/{id}
        import re
        m = re.search(r"https?://([^/]+)/(?:@[^/]+|statuses)/(\d+)", identifier)
        if not m:
            raise ValueError(f"Cannot parse Mastodon status URL: {identifier}")
        instance, status_id = m.group(1), m.group(2)
        resp = await self.client.get(f"https://{instance}/api/v1/statuses/{status_id}")
        resp.raise_for_status()
        status = resp.json()
        return self.ok(posts=[self._to_post(status)], data=[status], source=instance)

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        instance = options.get("instance", self.DEFAULT_INSTANCE)
        resp = await self.client.get(
            f"https://{instance}/api/v2/search",
            params={"q": identifier, "type": "statuses", "limit": min(limit, 40)},
        )
        # Large instances gate full-text search behind auth: some respond
        # 401/403/422, others (incl. mastodon.social) return 200 with empty
        # results. Fall back to the public hashtag timeline for one-word queries.
        gated = resp.status_code in (401, 403, 422) or (
            resp.status_code == 200 and not resp.json().get("statuses")
        )
        if gated:
            tag = identifier.strip().lstrip("#")
            if tag and " " not in tag:
                tag_resp = await self.client.get(
                    f"https://{instance}/api/v1/timelines/tag/{tag}",
                    params={"limit": min(limit, 40)},
                )
                if tag_resp.status_code == 200:
                    statuses = tag_resp.json()[:limit]
                    return self.partial(
                        error=f"Full-text search requires auth on {instance}; served #{tag} hashtag timeline instead.",
                        posts=[self._to_post(s) for s in statuses],
                        data=statuses,
                        source=instance,
                    )
            return self.partial(
                error=(
                    f"Full-text search requires auth on {instance}. "
                    "Try a single-word query (served via hashtag timeline) or "
                    "options={'instance': '<permissive instance>'}."
                ),
                source=instance,
            )
        resp.raise_for_status()
        statuses = resp.json().get("statuses", [])[:limit]
        return self.ok(posts=[self._to_post(s) for s in statuses], data=statuses, source=instance)

    async def fetch_page(
        self, req: SocialRequest, cursor: Optional[str] = None
    ) -> Tuple[SocialResponse, Optional[str]]:
        """Mastodon paginates timelines with `max_id=<last status id>`."""
        if req.query_type != SocialQueryType.posts:
            return await super().fetch_page(req, cursor)

        user, instance = self._split_identifier(req.identifier, req.options)
        account = await self._lookup_account(user, instance)
        params: Dict[str, Any] = {"limit": min(req.limit, 40)}
        if cursor:
            params["max_id"] = cursor
        resp = await self.client.get(
            f"https://{instance}/api/v1/accounts/{account['id']}/statuses", params=params
        )
        resp.raise_for_status()
        statuses = resp.json()
        out = self.ok(
            posts=[self._to_post(s) for s in statuses],
            data=statuses,
            source=instance,
        )
        out.query_type = req.query_type.value
        next_cursor = str(statuses[-1]["id"]) if statuses and statuses[-1].get("id") else None
        return out, next_cursor

    # --- helpers ---

    @staticmethod
    def _strip_html(html: str) -> str:
        return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)

    def _to_profile(self, account: dict, instance: str) -> SocialProfile:
        return SocialProfile(
            username=f"{account.get('username', '')}@{instance}",
            display_name=account.get("display_name"),
            bio=self._strip_html(account.get("note", "")),
            followers=account.get("followers_count"),
            following=account.get("following_count"),
            posts_count=account.get("statuses_count"),
            avatar_url=account.get("avatar"),
            url=account.get("url"),
            extra={"created_at": account.get("created_at")},
        )

    def _to_post(self, status: dict) -> SocialPost:
        account = status.get("account", {})
        media = [
            {"type": m.get("type"), "url": m.get("url"), "alt": m.get("description")}
            for m in status.get("media_attachments", [])
        ]
        return SocialPost(
            id=str(status.get("id", "")) or None,
            url=status.get("url"),
            author=account.get("acct"),
            text=self._strip_html(status.get("content", "")),
            created_at=status.get("created_at"),
            stats={
                "likes": status.get("favourites_count", 0),
                "reposts": status.get("reblogs_count", 0),
                "replies": status.get("replies_count", 0),
            },
            media=media,
            extra={"author_display_name": account.get("display_name")},
        )
