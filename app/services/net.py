"""Shared HTTP client helpers — browser-like headers, retries, proxy support."""
import asyncio
from typing import Optional

import httpx

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
