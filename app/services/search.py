"""Web search — DuckDuckGo HTML (keyless) with a lite fallback and relevance scores."""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.net import make_async_client


def _clean_ddg_url(href: str) -> str:
    """DDG wraps results as //duckduckgo.com/l/?uddg=<real url>."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


def _score(query: str, title: str, snippet: str, rank: int, total: int) -> float:
    """Blend rank position with keyword overlap. 0..1, higher is better."""
    rank_score = 1.0 - (rank / max(total, 1))
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
    if not words:
        return round(rank_score, 3)
    haystack = f"{title} {snippet}".lower()
    overlap = sum(1 for w in words if w in haystack) / len(words)
    return round(0.6 * rank_score + 0.4 * overlap, 3)


class SearchService:
    """Keyless web search via DuckDuckGo's HTML endpoints."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Full browser UA is required — bare UAs get a challenge page.
            self._client = make_async_client(headers={"Accept": "text/html"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Engine chain: DDG html -> DDG lite -> Startpage. Datacenter IPs get
        challenged unpredictably, so each engine is a best-effort fallback."""
        results = await self._search_html(query, num_results)
        if not results:
            results = await self._search_lite(query, num_results)
        if not results:
            results = await self._search_startpage(query, num_results)
        total = len(results)
        for i, r in enumerate(results):
            r["score"] = _score(query, r["title"], r["snippet"], i, total)
        return results

    async def _search_html(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        try:
            resp = await self.client.get(
                "https://html.duckduckgo.com/html/", params={"q": query}
            )
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result")[:num_results]:
                title_el = r.select_one(".result__title a")
                snippet_el = r.select_one(".result__snippet")
                url = _clean_ddg_url(title_el.get("href", "")) if title_el else ""
                title = title_el.get_text(strip=True) if title_el else ""
                if title and url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    })
            return results
        except Exception:
            return []

    async def _search_lite(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        try:
            resp = await self.client.get(
                "https://lite.duckduckgo.com/lite/", params={"q": query}
            )
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            # lite serves a table: link rows followed by snippet rows
            for a in soup.select("a.result-link")[:num_results]:
                url = _clean_ddg_url(a.get("href", ""))
                title = a.get_text(strip=True)
                snippet = ""
                row = a.find_parent("tr")
                if row:
                    next_row = row.find_next_sibling("tr")
                    if next_row:
                        snippet_el = next_row.select_one(".result-snippet")
                        if snippet_el:
                            snippet = snippet_el.get_text(strip=True)
                if title and url:
                    results.append({"title": title, "url": url, "snippet": snippet})
            return results
        except Exception:
            return []

    async def _search_startpage(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        try:
            resp = await self.client.get(
                "https://www.startpage.com/sp/search", params={"query": query}
            )
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select("div.result")[: num_results * 2]:
                a = r.select_one("a.result-title")
                if not a or not a.get("href", "").startswith("http"):
                    continue
                desc = r.select_one(".description")
                results.append({
                    "title": a.get_text(strip=True),
                    "url": a["href"],
                    "snippet": desc.get_text(strip=True) if desc else "",
                })
                if len(results) >= num_results:
                    break
            return results
        except Exception:
            return []
