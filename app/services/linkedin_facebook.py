"""LinkedIn + Facebook — honest best-effort public-page scrapers.

Both platforms aggressively login-wall anonymous traffic — confirmed live:
LinkedIn returns HTTP 999 (its bot-block code) and Facebook redirects
anonymous requests straight to a login form. Without credentials that wall is
not something any scraper can parse around, so these services salvage
whatever og:/meta tags survive and are honest about the rest.

Optionally, pasting your own logged-in session cookie (Settings -> Session
cookies, or SCRAPEX_LINKEDIN_COOKIE) lets requests go out authenticated as
you, which is what actually gets past the wall — same mechanism a real
browser tab uses when you're signed in. No cookie configured -> unchanged
best-effort og:/meta-tag behavior.
"""
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.models import SocialProfile, SocialQueryType, SocialResponse
from app.services import runtime_settings as rt
from app.services.net import cookie_header, make_async_client, og_tags as _og_tags
from app.services.social_base import BEST_EFFORT, PlatformBlocked, SocialPlatform


class _WalledPlatform(SocialPlatform):
    """Shared logic: fetch page -> detect wall -> salvage og: metadata."""

    WALL_MARKERS: tuple = ()
    HOME: str = ""
    COOKIE_FIELD: Optional[str] = None   # runtime_settings field holding the session cookie
    COOKIE_NAME: Optional[str] = None    # cookie name it's sent under, e.g. "li_at"

    capabilities = {SocialQueryType.profile: BEST_EFFORT}

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _session_cookie(self) -> Optional[str]:
        if not self.COOKIE_FIELD:
            return None
        return rt.get(self.COOKIE_FIELD)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "text/html"}
            cookie = self._session_cookie()
            if cookie and self.COOKIE_NAME:
                headers["Cookie"] = cookie_header({self.COOKIE_NAME: cookie})
            self._client = make_async_client(headers=headers)
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
            cookies = None
            cookie = self._session_cookie()
            if cookie and self.COOKIE_NAME:
                domain = "." + self.HOME.split("://", 1)[-1]
                cookies = [{"name": self.COOKIE_NAME, "value": cookie, "domain": domain, "path": "/"}]
            try:
                data = await browser.render(url, wait_until="domcontentloaded", cookies=cookies)
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
            hint = (
                "Add your own session cookie in Settings -> Session cookies for authenticated access."
                if self.COOKIE_FIELD and not self._session_cookie()
                else "For full data use an authenticated integration."
            )
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
                    + hint
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
    or a full linkedin.com URL. Everything else is honestly unsupported.

    Set a `li_at` session cookie (Settings -> Session cookies) to fetch as a
    logged-in user instead of hitting LinkedIn's anonymous authwall."""

    name = "linkedin"
    HOME = "https://www.linkedin.com"
    WALL_MARKERS = ("authwall", "/login", "signup", "join now", "sign in")
    COOKIE_FIELD = "linkedin_cookie"
    COOKIE_NAME = "li_at"

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
