"""Core web scraper — HTTP + BeautifulSoup + Markdown conversion."""
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin, urlparse
import re


class ScraperService:
    """Scrape any webpage — returns clean markdown, text, metadata, and links."""

    def __init__(self, proxy_url: Optional[str] = None, timeout: int = 30):
        self.timeout = timeout
        self.proxy = proxy_url
        self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                proxy=self.proxy,
            )
        return self._client

    def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a single page. Returns structured data."""
        response = self.client.get(url)
        response.raise_for_status()

        html = response.text
        soup = BeautifulSoup(html, "lxml")

        # Remove noise
        for tag in soup.find_all(["script", "style", "nav", "footer", "iframe", "noscript"]):
            tag.decompose()

        # Title
        title = None
        if soup.title:
            title = soup.title.get_text(strip=True)

        # Clean markdown
        body = soup.find("body") or soup
        content_md = md(str(body), heading_style="ATX", strip=["img"])

        # Clean excessive whitespace
        content_md = re.sub(r"\n{4,}", "\n\n\n", content_md)
        content_md = re.sub(r" +", " ", content_md)

        # Metadata
        metadata = {}
        for meta in soup.find_all("meta"):
            name = meta.get("name") or meta.get("property", "")
            content = meta.get("content", "")
            if name and content:
                metadata[name] = content

        # Links
        links = []
        base_domain = urlparse(url).netloc
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            text = a.get_text(strip=True)
            if text and href.startswith("http"):
                links.append({
                    "text": text[:200],
                    "url": href,
                    "internal": urlparse(href).netloc == base_domain,
                })

        # Plain text
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)

        return {
            "title": title,
            "content": content_md[:50000],   # Truncate huge pages
            "text": text[:100000],
            "metadata": metadata,
            "links": links[:200],
            "content_length": len(content_md),
            "link_count": len(links),
        }

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
