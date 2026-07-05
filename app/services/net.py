"""Shared HTTP client helpers — browser-like headers, retries, proxy support."""
import asyncio
from typing import Dict, Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def make_async_client(
    timeout: Optional[float] = None,
    headers: Optional[dict] = None,
) -> httpx.AsyncClient:
    """Create an AsyncClient with browser headers, redirects, and optional proxy."""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    kwargs: dict = {
        "timeout": timeout or settings.social_timeout,
        "headers": merged,
        "follow_redirects": True,
    }
    if settings.proxy_url:
        kwargs["proxy"] = settings.proxy_url
    return httpx.AsyncClient(**kwargs)


async def get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 0.5,
    **kwargs,
) -> httpx.Response:
    """GET with retries on connection errors and retryable status codes."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS and attempt < retries:
                await asyncio.sleep(backoff * (2 ** attempt))
                continue
            return resp
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc  # type: ignore[misc]


def cookie_header(pairs: Dict[str, Optional[str]]) -> Optional[str]:
    """Build a `Cookie:` header value from {name: value}, skipping unset ones."""
    parts = [f"{name}={value}" for name, value in pairs.items() if value]
    return "; ".join(parts) if parts else None


def og_tags(html: str) -> Dict[str, str]:
    """Salvage og:/twitter: meta tags (and <title>) from an HTML page —
    usually the only thing left standing behind a login wall."""
    soup = BeautifulSoup(html, "html.parser")
    tags: Dict[str, str] = {}
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name") or ""
        if key.startswith(("og:", "twitter:")) or key == "description":
            content = meta.get("content")
            if content:
                tags[key] = content
    if soup.title and soup.title.get_text(strip=True):
        tags.setdefault("title", soup.title.get_text(strip=True))
    return tags
