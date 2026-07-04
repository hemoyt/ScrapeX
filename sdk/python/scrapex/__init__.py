"""ScrapeX Python SDK — programmatic access to the ScrapeX API."""
import time

import httpx
from typing import Optional, Dict, Any, List


class ScrapeX:
    """Python client for the ScrapeX API.

    Usage:
        client = ScrapeX(api_key="sx-...")  # or base_url for self-hosted
        result = client.scrape("https://example.com")

        # Unified social API (10 platforms)
        posts = client.social("bluesky", "posts", "bsky.app")
        profile = client.social("youtube", "profile", "@mkbhd")
        hits = client.social_search("ai agents", platforms=["reddit", "hackernews"])

        # Apify-style dataset run: paginate until you have ALL the data
        run = client.run_social("hackernews", "search", "llm agents", max_items=500)
        run = client.wait_for_run(run["id"])
        items = client.dataset_all_items(run["dataset_id"])

        # Tavily-style research agent
        research = client.agent("What are people saying about AI agents?")
        print(research["answer"], research["sources"])

        # Legacy helpers still work
        tweets = client.twitter("jack")
        posts = client.reddit("python", listing="hot")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        headers = {}
        if api_key:
            headers = {"Authorization": f"Bearer {api_key}", "X-API-Key": api_key}
        self.client = httpx.Client(timeout=timeout, headers=headers)

    def scrape(
        self,
        url: str,
        render_js: bool = False,
        extract_ai: bool = False,
        ai_prompt: Optional[str] = None,
        include_screenshot: bool = False,
    ) -> Dict[str, Any]:
        """Scrape a single URL."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/scrape",
            json={
                "url": url,
                "render_js": render_js,
                "extract_ai": extract_ai,
                "ai_prompt": ai_prompt,
                "include_screenshot": include_screenshot,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def crawl(self, url: str, max_depth: int = 2, max_pages: int = 20) -> Dict[str, Any]:
        """Start a crawl job."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/crawl",
            json={"url": url, "max_depth": max_depth, "max_pages": max_pages},
        )
        resp.raise_for_status()
        return resp.json()

    def crawl_status(self, job_id: str) -> Dict[str, Any]:
        """Check crawl job status."""
        resp = self.client.get(f"{self.base_url}/api/v1/crawl/{job_id}")
        resp.raise_for_status()
        return resp.json()

    def search(
        self,
        query: str,
        num_results: int = 5,
        scrape_results: bool = False,
        include_answer: bool = False,
    ) -> Dict[str, Any]:
        """Search the web. include_answer=True adds an LLM-synthesized cited answer."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/search",
            json={
                "query": query,
                "num_results": num_results,
                "scrape_results": scrape_results,
                "include_answer": include_answer,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # --- unified social API ---

    def social(
        self,
        platform: str,
        query_type: str = "posts",
        identifier: str = "",
        limit: int = 10,
        **options,
    ) -> Dict[str, Any]:
        """Unified social scraping.

        platform:   twitter, reddit, bluesky, hackernews, mastodon, youtube,
                    instagram, tiktok, linkedin, facebook
        query_type: profile | posts | post | search
        identifier: username / URL / search query, depending on query_type
        options:    platform extras, e.g. listing="top" (reddit),
                    instance="fosstodon.org" (mastodon)
        """
        resp = self.client.post(
            f"{self.base_url}/api/v1/social/{platform}",
            json={
                "query_type": query_type,
                "identifier": identifier,
                "limit": limit,
                "options": options,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def social_search(
        self,
        query: str,
        platforms: Optional[List[str]] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Search one keyword across multiple social platforms at once."""
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if platforms:
            payload["platforms"] = platforms
        resp = self.client.post(f"{self.base_url}/api/v1/social/search", json=payload)
        resp.raise_for_status()
        return resp.json()

    # --- Apify-style dataset runs ---

    def run_social(
        self,
        platform: str,
        query_type: str = "posts",
        identifier: str = "",
        max_items: int = 100,
        **options,
    ) -> Dict[str, Any]:
        """Start a dataset run: paginates the platform in the background until
        max_items are collected or the server's time budget runs out."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/runs",
            json={
                "platform": platform,
                "query_type": query_type,
                "identifier": identifier,
                "max_items": max_items,
                "options": options,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def run_status(self, run_id: str) -> Dict[str, Any]:
        resp = self.client.get(f"{self.base_url}/api/v1/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()

    def wait_for_run(self, run_id: str, poll: float = 2.0, timeout: float = 600.0) -> Dict[str, Any]:
        """Block until the run leaves READY/RUNNING (or `timeout` seconds pass)."""
        deadline = time.monotonic() + timeout
        while True:
            run = self.run_status(run_id)
            if run["status"] not in ("READY", "RUNNING") or time.monotonic() > deadline:
                return run
            time.sleep(poll)

    def dataset_items(
        self,
        dataset_id: str,
        offset: int = 0,
        limit: int = 100,
        format: str = "json",
    ) -> Any:
        """Fetch dataset items. format='json' returns a dict envelope with paging
        info; 'ndjson' and 'csv' return the raw text body."""
        resp = self.client.get(
            f"{self.base_url}/api/v1/datasets/{dataset_id}/items",
            params={"offset": offset, "limit": limit, "format": format},
        )
        resp.raise_for_status()
        return resp.json() if format == "json" else resp.text

    def dataset_all_items(self, dataset_id: str, page_size: int = 500) -> List[Dict[str, Any]]:
        """Drain the whole dataset into a list, paging under the hood."""
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = self.dataset_items(dataset_id, offset=offset, limit=page_size)
            items.extend(page["items"])
            offset += page["count"]
            if offset >= page["total"] or page["count"] == 0:
                return items

    def competitors(
        self,
        product: str,
        max_competitors: int = 5,
        enrich: bool = True,
    ) -> Dict[str, Any]:
        """Discover a product's competitors and pull their social profiles + mentions."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/competitors",
            json={"product": product, "max_competitors": max_competitors, "enrich": enrich},
        )
        resp.raise_for_status()
        return resp.json()

    # --- research agent ---

    def agent(
        self,
        query: str,
        depth: str = "basic",
        max_sources: int = 5,
        include_social: bool = True,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the research agent: returns {answer, sources, steps, usage, status}."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/agent",
            json={
                "query": query,
                "depth": depth,
                "max_sources": max_sources,
                "include_social": include_social,
                "model": model,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def extract(self, url: str, prompt: str, render_js: bool = False) -> Dict[str, Any]:
        """Extract structured data from a URL using AI."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/extract",
            json={"url": url, "prompt": prompt, "render_js": render_js},
        )
        resp.raise_for_status()
        return resp.json()

    def twitter(self, username: str, max_tweets: int = 10) -> Dict[str, Any]:
        """Get tweets from a Twitter/X user."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/social/twitter",
            json={"username": username, "max_tweets": max_tweets},
        )
        resp.raise_for_status()
        return resp.json()

    def tweet(self, tweet_url: str) -> Dict[str, Any]:
        """Get a specific tweet by URL."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/social/twitter",
            json={"tweet_url": tweet_url},
        )
        resp.raise_for_status()
        return resp.json()

    def reddit(self, subreddit: str, listing: str = "hot", limit: int = 10) -> Dict[str, Any]:
        """Get posts from a subreddit."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/social/reddit",
            json={"subreddit": subreddit, "listing": listing, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    def reddit_post(self, post_url: str) -> Dict[str, Any]:
        """Get a specific Reddit post with comments."""
        resp = self.client.post(
            f"{self.base_url}/api/v1/social/reddit",
            json={"post_url": post_url},
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> Dict[str, Any]:
        """Check API health."""
        resp = self.client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()
