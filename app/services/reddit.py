"""Reddit scraper — HTML-based scraping via old.reddit.com (bypasses API blocks)."""
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.models import SocialPost, SocialQueryType, SocialResponse
from app.services.net import make_async_client
from app.services.social_base import RELIABLE, SocialPlatform


class RedditService(SocialPlatform):
    """Scrape Reddit posts and comments using old.reddit.com HTML parsing.

    Why HTML instead of JSON API: Reddit's JSON API returns 403 for
    datacenter/VPS IPs. The HTML version of old.reddit.com is more
    lenient and works without authentication.

    Identifiers:
        posts  -> subreddit name (options: {"listing": "hot|new|top|rising"})
        post   -> post URL
        search -> search query (options: {"subreddit": "python"} to scope)
    """

    name = "reddit"
    capabilities = {
        SocialQueryType.posts: RELIABLE,
        SocialQueryType.post: RELIABLE,
        SocialQueryType.search: RELIABLE,
    }

    BASE = "https://old.reddit.com"

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = make_async_client(headers={"Cache-Control": "no-cache"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> str:
        try:
            resp = await self.client.get(f"{self.BASE}/r/python/hot/", timeout=5)
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "down"
        finally:
            await self.aclose()

    # --- unified interface ---

    async def get_posts(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        listing = options.get("listing", "hot")
        raw = await self._scrape_listing(f"{self.BASE}/r/{identifier}/{listing}/", limit, subreddit=identifier)
        return self.ok(posts=[self._to_post(p) for p in raw], data=raw, source="old.reddit.com")

    async def search(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        base = f"{self.BASE}/r/{options['subreddit']}" if options.get("subreddit") else self.BASE
        url = f"{base}/search?q={quote(identifier)}&sort=relevance&restrict_sr={'on' if options.get('subreddit') else 'off'}"
        raw = await self._scrape_search(url, limit)
        return self.ok(posts=[self._to_post(p) for p in raw], data=raw, source="old.reddit.com")

    async def _scrape_search(self, url: str, limit: int) -> List[Dict[str, Any]]:
        """Search results use different markup (search-result-link) than listings."""
        resp = await self.client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        posts = []
        for result in soup.find_all("div", class_="search-result-link")[:limit]:
            try:
                title_el = result.find("a", class_="search-title")
                title = title_el.get_text(strip=True) if title_el else ""
                permalink = title_el.get("href", "") if title_el else ""

                score = 0
                score_el = result.find("span", class_="search-score")
                if score_el:
                    try:
                        score = int(score_el.get_text(strip=True).split()[0].replace(",", ""))
                    except (ValueError, IndexError):
                        pass

                num_comments = 0
                comments_el = result.find("a", class_="search-comments")
                if comments_el:
                    try:
                        num_comments = int(comments_el.get_text(strip=True).split()[0].replace(",", ""))
                    except (ValueError, IndexError):
                        pass

                author_el = result.find("a", class_="author")
                author = author_el.get_text(strip=True) if author_el else ""

                sub_el = result.find("a", class_="search-subreddit-link")
                sub = sub_el.get_text(strip=True).replace("r/", "") if sub_el else ""

                time_el = result.find("time")
                created_at = time_el.get("datetime") if time_el else None

                body_el = result.find("div", class_="search-result-body")
                selftext = body_el.get_text(strip=True)[:2000] if body_el else ""

                if title:
                    posts.append({
                        "id": result.get("data-fullname", "").replace("t3_", ""),
                        "title": title,
                        "author": author,
                        "subreddit": sub,
                        "score": score,
                        "num_comments": num_comments,
                        "url": permalink,
                        "permalink": permalink,
                        "selftext": selftext,
                        "created_at": created_at,
                        "domain": "",
                        "flair": None,
                    })
            except Exception:
                continue

        return posts

    async def get_post(self, identifier: str, limit: int, options: Dict[str, Any]) -> SocialResponse:
        data = await self._get_post(identifier)
        post = data.get("post", {})
        return self.ok(
            posts=[SocialPost(
                url=post.get("url"),
                author=post.get("author"),
                text=f"{post.get('title', '')}\n\n{post.get('selftext', '')}".strip(),
                stats={"score": post.get("score", 0)},
                extra={"comments": data.get("comments", [])},
            )],
            data=[data],
            source="old.reddit.com",
        )

    def _to_post(self, p: Dict[str, Any]) -> SocialPost:
        return SocialPost(
            id=p.get("id"),
            url=p.get("permalink"),
            author=p.get("author"),
            text=f"{p.get('title', '')}\n\n{p.get('selftext', '')}".strip(),
            created_at=p.get("created_at"),
            stats={"score": p.get("score", 0), "comments": p.get("num_comments", 0)},
            extra={k: v for k, v in p.items() if k in ("subreddit", "domain", "flair")},
        )

    # --- parsing (proven old.reddit HTML logic) ---

    async def _scrape_listing(self, url: str, limit: int, subreddit: str = "") -> List[Dict[str, Any]]:
        resp = await self.client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        posts = []
        for thing in soup.find_all("div", class_="thing")[:limit]:
            try:
                score_el = thing.find("div", class_="score unvoted")
                score = score_el.get("title", "0") if score_el else "0"
                try:
                    score = int(score)
                except ValueError:
                    score = 0

                title_el = thing.find("a", class_="title") or thing.find("a", class_="search-title")
                title = title_el.get_text(strip=True) if title_el else ""
                permalink = title_el.get("href") if title_el else ""

                author_el = thing.find("a", class_="author")
                author = author_el.get_text(strip=True) if author_el else ""

                comments_el = thing.find("a", class_="comments") or thing.find("a", class_="search-comments")
                comments_text = comments_el.get_text(strip=True) if comments_el else "0 comments"
                num_comments = 0
                try:
                    num_comments = int(comments_text.split()[0])
                except (ValueError, IndexError):
                    pass

                flair_el = thing.find("span", class_="linkflairlabel")
                flair = flair_el.get_text(strip=True) if flair_el else None

                domain_el = thing.find("span", class_="domain")
                domain = domain_el.get_text(strip=True).lstrip("(").rstrip(")") if domain_el else ""

                entry = thing.find("div", class_="entry")
                selftext = ""
                if entry:
                    md = entry.find("div", class_="md")
                    if md:
                        selftext = md.get_text(strip=True)[:2000]

                post_url = permalink
                if post_url and not post_url.startswith("http"):
                    post_url = f"https://old.reddit.com{post_url}"

                sub = subreddit
                if not sub:
                    sub_el = thing.find("a", class_="subreddit")
                    sub = sub_el.get_text(strip=True).replace("r/", "") if sub_el else ""

                if title:
                    posts.append({
                        "id": thing.get("data-fullname", "").replace("t3_", ""),
                        "title": title,
                        "author": author,
                        "subreddit": sub,
                        "score": score,
                        "num_comments": num_comments,
                        "url": post_url,
                        "permalink": post_url,
                        "selftext": selftext,
                        "domain": domain,
                        "flair": flair,
                    })
            except Exception:
                continue

        return posts

    async def _get_post(self, post_url: str) -> Dict[str, Any]:
        url = post_url.replace("www.reddit.com", "old.reddit.com")
        if "old.reddit.com" not in url:
            url = f"https://old.reddit.com{url}" if url.startswith("/") else url.replace("https://reddit.com", "https://old.reddit.com")

        resp = await self.client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_div = soup.find("div", class_="sitetable")
        if not post_div:
            return {"error": "Could not parse post"}

        title_el = soup.find("a", class_="title")
        title = title_el.get_text(strip=True) if title_el else ""

        author_el = soup.find("a", class_="author")
        author = author_el.get_text(strip=True) if author_el else ""

        score_el = soup.find("div", class_="score")
        score = 0
        if score_el:
            try:
                score = int(score_el.get_text(strip=True))
            except ValueError:
                pass

        post_md = soup.find("div", class_="expando")
        selftext = post_md.get_text(strip=True)[:5000] if post_md else ""

        post = {
            "title": title,
            "author": author,
            "score": score,
            "selftext": selftext,
            "url": url,
        }

        comments = []
        for c in soup.find_all("div", class_="comment")[:20]:
            try:
                c_author = c.find("a", class_="author")
                c_body = c.find("div", class_="md")
                c_score = c.find("span", class_="score")

                author_name = c_author.get_text(strip=True) if c_author else "[deleted]"
                body = c_body.get_text(strip=True)[:1000] if c_body else ""
                c_score_val = c_score.get_text(strip=True).replace("points", "").strip() if c_score else "0"
                try:
                    c_score_val = int(c_score_val)
                except ValueError:
                    c_score_val = 0

                if body:
                    comments.append({
                        "author": author_name,
                        "body": body,
                        "score": c_score_val,
                    })
            except Exception:
                continue

        return {"post": post, "comments": comments}
