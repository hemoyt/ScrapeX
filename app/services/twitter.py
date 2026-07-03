"""Twitter/X scraper — keyless fallback chain.

Single tweets and profiles are served reliably via public mirror APIs:
  1. api.fxtwitter.com   (tweets + profiles)
  2. api.vxtwitter.com   (tweets)
  3. cdn.syndication.twimg.com  (tweets; the embed CDN, needs a computed token)

Timelines and search have NO reliable keyless source since Nitter's demise —
we try any configured Nitter mirrors and otherwise return status="blocked"
with an honest explanation instead of fabricating data.
"""
import math
import re
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import SocialPost, SocialProfile, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import BEST_EFFORT, RELIABLE, PlatformBlocked, SocialPlatform

DEFAULT_NITTER_INSTANCES = [
    "https://nitter.net",
    "https://xcancel.com",
    "https://nitter.poast.org",
]


def syndication_token(tweet_id: int) -> str:
    """Token for cdn.syndication.twimg.com/tweet-result — JS reference:
    (id / 1e15 * Math.PI).toString(36).replace(/(0+|\\.)/g, '')."""
    value = tweet_id / 1e15 * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    int_part = int(value)
    frac = value - int_part
    out = ""
    if int_part == 0:
        out = "0"
    while int_part:
        out = digits[int_part % 36] + out
        int_part //= 36
    if frac > 0:
        out += "."
        for _ in range(12):
            frac *= 36
            d = int(frac)
            out += digits[d]
            frac -= d
    return out.replace("0", "").replace(".", "")


class TwitterService(SocialPlatform):
    """Identifiers:
        profile -> username or profile URL
        post    -> tweet URL (x.com / twitter.com) or bare numeric id
        posts   -> username (best-effort: Nitter mirrors, usually blocked)
        search  -> query (best-effort: Nitter mirrors, usually blocked)
    """

    name = "twitter"
    capabilities = {
        SocialQueryType.profile: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.posts: BEST_EFFORT,
        SocialQueryType.search: BEST_EFFORT,
    }

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
            resp = await self.client.get("https://api.fxtwitter.com/status/20", timeout=5)
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    @property
    def nitter_instances(self) -> List[str]:
        if settings.nitter_instances:
            return [u.strip().rstrip("/") for u in settings.nitter_instances.split(",") if u.strip()]
        return DEFAULT_NITTER_INSTANCES

    # --- post: fxtwitter -> vxtwitter -> syndication ---

    @staticmethod
    def _extract_tweet_id(identifier: str) -> str:
        ident = identifier.strip()
        m = re.search(r"/status(?:es)?/(\d+)", ident)
        if m:
            return m.group(1)
        if ident.isdigit():
            return ident
        raise ValueError(f"Cannot parse tweet id from: {identifier}")

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        tweet_id = self._extract_tweet_id(identifier)
        errors = []

        for fetcher, source in (
            (self._from_fxtwitter, "fxtwitter"),
            (self._from_vxtwitter, "vxtwitter"),
            (self._from_syndication, "syndication"),
        ):
            try:
                post, raw = await fetcher(tweet_id)
                return self.ok(posts=[post], data=[raw], source=source)
            except Exception as e:
                errors.append(f"{source}: {e}")

        raise RuntimeError("All tweet sources failed — " + "; ".join(errors))

    async def _from_fxtwitter(self, tweet_id: str) -> tuple:
        resp = await self.client.get(f"https://api.fxtwitter.com/status/{tweet_id}")
        resp.raise_for_status()
        data = resp.json()
        tweet = data.get("tweet")
        if not tweet:
            raise ValueError(data.get("message", "no tweet in response"))
        author = tweet.get("author") or {}
        media = [
            {"type": m.get("type"), "url": m.get("url")}
            for m in (tweet.get("media") or {}).get("all", [])
        ]
        post = SocialPost(
            id=tweet.get("id"),
            url=tweet.get("url"),
            author=author.get("screen_name"),
            text=tweet.get("text"),
            created_at=tweet.get("created_at"),
            stats={
                "likes": tweet.get("likes") or 0,
                "retweets": tweet.get("retweets") or 0,
                "replies": tweet.get("replies") or 0,
                "quotes": tweet.get("quotes") or 0,
                **({"views": tweet["views"]} if tweet.get("views") else {}),
            },
            media=media,
            extra={"author_name": author.get("name"), "lang": tweet.get("lang")},
        )
        return post, tweet

    async def _from_vxtwitter(self, tweet_id: str) -> tuple:
        resp = await self.client.get(f"https://api.vxtwitter.com/Twitter/status/{tweet_id}")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("tweetID"):
            raise ValueError("no tweet in response")
        media = [{"type": m.get("type"), "url": m.get("url")} for m in data.get("media_extended", [])]
        post = SocialPost(
            id=data.get("tweetID"),
            url=data.get("tweetURL"),
            author=data.get("user_screen_name"),
            text=data.get("text"),
            created_at=data.get("date"),
            stats={
                "likes": data.get("likes") or 0,
                "retweets": data.get("retweets") or 0,
                "replies": data.get("replies") or 0,
            },
            media=media,
            extra={"author_name": data.get("user_name")},
        )
        return post, data

    async def _from_syndication(self, tweet_id: str) -> tuple:
        token = syndication_token(int(tweet_id))
        resp = await self.client.get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={"id": tweet_id, "token": token, "lang": "en"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("id_str") or not data.get("text"):
            raise ValueError("no tweet in syndication response")
        user = data.get("user") or {}
        media = [
            {"type": m.get("type"), "url": m.get("media_url_https")}
            for m in (data.get("mediaDetails") or [])
        ]
        post = SocialPost(
            id=data.get("id_str"),
            url=f"https://x.com/{user.get('screen_name', 'i')}/status/{data.get('id_str')}",
            author=user.get("screen_name"),
            text=data.get("text"),
            created_at=data.get("created_at"),
            stats={
                "likes": data.get("favorite_count") or 0,
                "replies": data.get("conversation_count") or 0,
            },
            media=media,
            extra={"author_name": user.get("name")},
        )
        return post, data

    # --- profile: fxtwitter ---

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        username = identifier.strip().lstrip("@")
        m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", username)
        if m:
            username = m.group(1)
        resp = await self.client.get(f"https://api.fxtwitter.com/{username}")
        resp.raise_for_status()
        data = resp.json()
        user = data.get("user")
        if not user:
            raise ValueError(data.get("message", f"user {username} not found"))
        profile = SocialProfile(
            username=user.get("screen_name", username),
            display_name=user.get("name"),
            bio=user.get("description"),
            followers=user.get("followers"),
            following=user.get("following"),
            posts_count=user.get("tweets"),
            avatar_url=user.get("avatar_url"),
            url=user.get("url"),
            verified=(user.get("verification") or {}).get("verified"),
            extra={"joined": user.get("joined"), "location": user.get("location")},
        )
        return self.ok(profile=profile, data=[user], source="fxtwitter")

    # --- timeline & search: Nitter best-effort, honest blocked otherwise ---

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        username = identifier.strip().lstrip("@")
        posts, source = await self._try_nitter(f"/{username}", limit)
        if posts:
            return self.ok(posts=posts, data=[p.model_dump(exclude_none=True) for p in posts], source=source)
        raise PlatformBlocked(
            "Twitter timelines have no reliable keyless source (Nitter mirrors are "
            "down/blocking). Single tweets (query_type=post) and profiles "
            "(query_type=profile) still work. You can point SCRAPEX_NITTER_INSTANCES "
            "at a working mirror."
        )

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        posts, source = await self._try_nitter(f"/search?f=tweets&q={identifier}", limit)
        if posts:
            return self.ok(posts=posts, data=[p.model_dump(exclude_none=True) for p in posts], source=source)
        raise PlatformBlocked(
            "Twitter search has no reliable keyless source (Nitter mirrors are "
            "down/blocking). Set SCRAPEX_NITTER_INSTANCES to a working mirror, or "
            "search Bluesky/Mastodon instead."
        )

    async def _try_nitter(self, path: str, limit: int) -> tuple[List[SocialPost], Optional[str]]:
        headers = {"Accept": "text/html"}
        for base in self.nitter_instances:
            try:
                resp = await self.client.get(f"{base}{path}", headers=headers, timeout=8)
                if resp.status_code != 200 or not resp.text:
                    continue
                posts = self._parse_nitter_timeline(resp.text, base, limit)
                if posts:
                    return posts, base.replace("https://", "")
            except Exception:
                continue
        return [], None

    @staticmethod
    def _parse_nitter_timeline(html: str, base: str, limit: int) -> List[SocialPost]:
        soup = BeautifulSoup(html, "html.parser")
        posts = []
        for item in soup.select(".timeline-item")[:limit]:
            content = item.select_one(".tweet-content")
            if not content:
                continue
            link = item.select_one("a.tweet-link")
            author = item.select_one(".username")
            date = item.select_one(".tweet-date a")
            stats = {}
            for stat in item.select(".tweet-stat"):
                text = stat.get_text(strip=True).replace(",", "")
                icon = stat.select_one("span[class*='icon-']")
                if icon and text:
                    kind = next((c.replace("icon-", "") for c in icon.get("class", []) if c.startswith("icon-")), None)
                    key = {"comment": "replies", "retweet": "retweets", "heart": "likes", "quote": "quotes"}.get(kind)
                    if key:
                        try:
                            stats[key] = int(text)
                        except ValueError:
                            pass
            href = link.get("href", "") if link else ""
            tweet_id = None
            m = re.search(r"/status/(\d+)", href)
            if m:
                tweet_id = m.group(1)
            posts.append(SocialPost(
                id=tweet_id,
                url=f"https://x.com{href.split('#')[0]}" if href else None,
                author=author.get_text(strip=True).lstrip("@") if author else None,
                text=content.get_text(" ", strip=True),
                created_at=date.get("title") if date else None,
                stats=stats,
            ))
        return posts
