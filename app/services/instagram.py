"""Instagram scraper — best-effort via the public web profile API.

i.instagram.com/api/v1/users/web_profile_info/ (with the web app id header)
returns a profile plus its first 12 posts in one call, keyless. It works from
many IPs today but Instagram aggressively rate-limits and login-walls
anonymous traffic — confirmed live: HTTP 429 on plain requests. Every failure
degrades to an honest status=blocked. Single posts use the public embed page,
which is more lenient. Cache aggressively.

Optionally, pasting your own logged-in `sessionid` cookie (Settings ->
Session cookies, or SCRAPEX_INSTAGRAM_SESSIONID) makes these requests go out
authenticated as you instead of anonymous — the same technique tools like
instaloader use — which is what actually gets past the rate-limit/wall.
"""
import json
import re
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from app.models import SocialPost, SocialProfile, SocialQueryType, SocialResponse
from app.services import runtime_settings as rt
from app.services.net import cookie_header, make_async_client
from app.services.social_base import BEST_EFFORT, PlatformBlocked, SocialPlatform

IG_APP_ID = "936619743392459"  # instagram.com web client id (public, embedded in their JS)


class InstagramService(SocialPlatform):
    """Identifiers:
        profile / posts -> username (posts = the ~12 most recent from the profile payload)
        post            -> post/reel URL or shortcode
    Search is not supported keyless — honestly unsupported.
    """

    name = "instagram"
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
            headers = {
                "x-ig-app-id": IG_APP_ID,
                "Accept": "*/*",
                "Referer": "https://www.instagram.com/",
            }
            sessionid = rt.get("instagram_sessionid")
            csrftoken = rt.get("instagram_csrftoken")
            cookie = cookie_header({"sessionid": sessionid, "csrftoken": csrftoken})
            if cookie:
                headers["Cookie"] = cookie
            if csrftoken:
                headers["x-csrftoken"] = csrftoken
            self._client = make_async_client(headers=headers)
        return self._client

    def _authenticated(self) -> bool:
        return bool(rt.get("instagram_sessionid"))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> str:
        try:
            user = await self._web_profile("instagram")
            return "ok" if user else "degraded"
        except PlatformBlocked:
            return "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    async def _web_profile(self, username: str) -> dict:
        resp = await self.client.get(
            "https://i.instagram.com/api/v1/users/web_profile_info/",
            params={"username": username},
        )
        if resp.status_code in (401, 403, 429):
            hint = (
                "Retry later — responses are cached to minimize hits."
                if self._authenticated()
                else "Add your own `sessionid` cookie in Settings -> Session cookies for authenticated (far more reliable) access."
            )
            raise PlatformBlocked(
                f"Instagram is rate-limiting/login-walling this IP (HTTP {resp.status_code}). {hint}"
            )
        resp.raise_for_status()
        if "login" in str(resp.url):
            raise PlatformBlocked("Instagram redirected to login — this IP is walled.")
        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise PlatformBlocked("Instagram returned non-JSON (challenge page).")
        user = (data.get("data") or {}).get("user")
        if not user:
            raise PlatformBlocked("Instagram returned an empty user (blocked or nonexistent account).")
        return user

    @staticmethod
    def _clean_username(identifier: str) -> str:
        ident = identifier.strip().lstrip("@")
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", ident)
        if m:
            ident = m.group(1)
        return ident

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        user = await self._web_profile(self._clean_username(identifier))
        profile = SocialProfile(
            username=user.get("username", identifier),
            display_name=user.get("full_name"),
            bio=user.get("biography"),
            followers=(user.get("edge_followed_by") or {}).get("count"),
            following=(user.get("edge_follow") or {}).get("count"),
            posts_count=(user.get("edge_owner_to_timeline_media") or {}).get("count"),
            avatar_url=user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
            url=f"https://www.instagram.com/{user.get('username', '')}/",
            verified=user.get("is_verified"),
            extra={"is_private": user.get("is_private"), "category": user.get("category_name")},
        )
        return self.ok(profile=profile, data=[{k: user.get(k) for k in (
            "username", "full_name", "biography", "is_private", "is_verified", "category_name",
        )}], source="web_profile_info")

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        username = self._clean_username(identifier)
        user = await self._web_profile(username)
        if user.get("is_private"):
            raise PlatformBlocked(f"@{username} is a private account.")
        edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges", [])
        posts = [self._to_post(e.get("node", {}), username) for e in edges[:limit]]
        return self.ok(
            posts=posts,
            data=[p.model_dump(exclude_none=True) for p in posts],
            source="web_profile_info",
        )

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        shortcode = identifier.strip()
        m = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", shortcode)
        if m:
            shortcode = m.group(1)
        # The captioned embed page is served with lighter blocking than the app APIs.
        resp = await self.client.get(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            headers={"Accept": "text/html"},
        )
        if resp.status_code in (401, 403, 429):
            raise PlatformBlocked(f"Instagram embed blocked (HTTP {resp.status_code}).")
        resp.raise_for_status()
        post = self._parse_embed(resp.text, shortcode)
        if post is None:
            raise PlatformBlocked("Could not parse Instagram embed page (blocked or removed post).")
        return self.ok(posts=[post], data=[post.model_dump(exclude_none=True)], source="embed")

    # --- parsers ---

    def _to_post(self, node: dict, username: str) -> SocialPost:
        caption_edges = (node.get("edge_media_to_caption") or {}).get("edges", [])
        caption = caption_edges[0]["node"].get("text", "") if caption_edges else ""
        shortcode = node.get("shortcode", "")
        media = []
        if node.get("display_url"):
            media.append({
                "type": "video" if node.get("is_video") else "image",
                "url": node.get("display_url"),
            })
        stats = {
            "likes": (node.get("edge_liked_by") or {}).get("count", 0),
            "comments": (node.get("edge_media_to_comment") or {}).get("count", 0),
        }
        if node.get("video_view_count"):
            stats["views"] = node["video_view_count"]
        return SocialPost(
            id=node.get("id"),
            url=f"https://www.instagram.com/p/{shortcode}/" if shortcode else None,
            author=username,
            text=caption[:3000],
            created_at=str(node.get("taken_at_timestamp", "")) or None,
            stats=stats,
            media=media,
            extra={"shortcode": shortcode},
        )

    @staticmethod
    def _parse_embed(html: str, shortcode: str) -> Optional[SocialPost]:
        soup = BeautifulSoup(html, "html.parser")
        caption_el = soup.select_one(".Caption")
        author_el = soup.select_one(".UsernameText") or soup.select_one(".CaptionUsername")
        img = soup.select_one("img.EmbeddedMediaImage")
        if not (caption_el or author_el or img):
            return None
        author = author_el.get_text(strip=True) if author_el else None
        caption = caption_el.get_text(" ", strip=True) if caption_el else None
        if caption and author and caption.startswith(author):
            caption = caption[len(author):].strip()
        return SocialPost(
            id=shortcode,
            url=f"https://www.instagram.com/p/{shortcode}/",
            author=author,
            text=caption,
            media=[{"type": "image", "url": img.get("src")}] if img else [],
            extra={"shortcode": shortcode},
        )
