"""LinkedIn + Facebook — honest best-effort public-page scrapers.

Both platforms aggressively login-wall anonymous traffic. These services exist
to be honest rather than to pretend: they try plain HTTP first, then a
Playwright render when available, and salvage whatever og:/meta tags survive
the auth wall. When nothing usable comes back they return status=blocked with
an explanation — never fabricated data.
"""
import re
from typing import Any, Dict, Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import SocialProfile, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import BEST_EFFORT, PlatformBlocked, SocialPlatform


def _og_tags(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    tags = {}
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name") or ""
        if key.startswith(("og:", "twitter:")) or key == "description":
            content = meta.get("content")
            if content:
                tags[key] = content
    if soup.title and soup.title.get_text(strip=True):
        tags.setdefault("title", soup.title.get_text(strip=True))
    return tags


class _WalledPlatform(SocialPlatform):
    """Shared logic: fetch page -> detect wall -> salvage og: metadata."""

    WALL_MARKERS: tuple = ()
    HOME: str = ""

    capabilities = {SocialQueryType.profile: BEST_EFFORT}

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

    def _profile_url(self, identifier: str) -> str:
        raise NotImplementedError

    def _is_walled(self, final_url: str, html: str) -> bool:
        low = (final_url + " " + html[:5000]).lower()
        return any(marker in low for marker in self.WALL_MARKERS)

    async def _get_html(self, url: str) -> tuple[str, str]:
        resp = await self.client.get(url)
        if resp.status_code in (401, 403, 429, 999):  # LinkedIn uses 999 for bot blocks
            raise PlatformBlocked(f"{self.name} blocked the request (HTTP {resp.status_code}).")
        return str(resp.url), resp.text

    async def _render(self, url: str) -> Optional[str]:
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

    async def get_profile(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        url = self._profile_url(identifier)
        walled = False
        html = ""
        try:
            final_url, html = await self._get_html(url)
            walled = self._is_walled(final_url, html)
        except PlatformBlocked:
            walled = True

        if walled:
            rendered = await self._render(url)
            if rendered and not self._is_walled(url, rendered):
                html = rendered
                walled = False

        tags = _og_tags(html) if html else {}
        title = tags.get("og:title") or tags.get("title")
        description = tags.get("og:description") or tags.get("description")

        # A wall/error page title is not profile data — treat it as nothing.
        junk_titles = {"error", "facebook", "linkedin", "log in", "login", "sign in", "sign up"}
        if title and (title.strip().lower() in junk_titles or "log in" in title.lower()):
            title = None
            description = None

        profile = SocialProfile(
            username=identifier,
            display_name=title,
            bio=description,
            avatar_url=tags.get("og:image"),
            url=url,
            extra={"og_tags": tags} if tags else {},
        )

        if walled or not title:
            return SocialResponse(
                success=bool(title),
                platform=self.name,
                status="blocked" if not title else "partial",
                profile=profile if title else None,
                data=[tags] if tags else [],
                source="og_meta",
                error=(
                    f"{self.name} login-walls anonymous access. "
                    + ("Only public og:/meta tags could be salvaged. " if title else "Nothing usable was returned. ")
                    + "For full data use an authenticated integration."
                ),
            )
        return self.partial(
            error=f"{self.name} exposes only limited public metadata without login.",
            profile=profile,
            data=[tags],
            source="og_meta",
        )

    async def probe(self) -> str:
        try:
            resp = await self.client.get(self.HOME, timeout=5)
            return "degraded" if resp.status_code < 500 else "down"
        except Exception:
            return "down"
        finally:
            await self.aclose()


class LinkedInService(_WalledPlatform):
    """profile -> public profile slug ("satyanadella"), company ("company/anthropic"),
    or a full linkedin.com URL. Everything else is honestly unsupported."""

    name = "linkedin"
    HOME = "https://www.linkedin.com"
    WALL_MARKERS = ("authwall", "/login", "signup", "join now", "sign in")

    def _profile_url(self, identifier: str) -> str:
        ident = identifier.strip()
        if ident.startswith("http"):
            return ident
        ident = ident.lstrip("@").strip("/")
        if "/" in ident:  # e.g. company/anthropic
            return f"https://www.linkedin.com/{ident}/"
        return f"https://www.linkedin.com/in/{ident}/"


class FacebookService(_WalledPlatform):
    """profile -> page name ("nasa") or full facebook.com URL."""

    name = "facebook"
    HOME = "https://www.facebook.com"
    WALL_MARKERS = ("/login", "log in to facebook", "you must log in", "login_form")

    def _profile_url(self, identifier: str) -> str:
        ident = identifier.strip()
        if ident.startswith("http"):
            return ident
        return f"https://www.facebook.com/{ident.lstrip('@').strip('/')}/"
