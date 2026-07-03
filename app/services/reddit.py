"""Reddit scraper — HTML-based scraping via old.reddit.com (bypasses API blocks)."""
import httpx
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any


class RedditService:
    """Scrape Reddit posts and comments using old.reddit.com HTML parsing.

    Why HTML instead of JSON API: Reddit's JSON API returns 403 for
    datacenter/VPS IPs. The HTML version of old.reddit.com is more
    lenient and works without authentication.
    """

    BASE = "https://old.reddit.com"

    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            },
        )

    def get_subreddit(self, subreddit: str, listing: str = "hot", limit: int = 10) -> List[Dict[str, Any]]:
        """Get posts from a subreddit by scraping old.reddit.com HTML."""
        url = f"{self.BASE}/r/{subreddit}/{listing}/"
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            posts = []
            for thing in soup.find_all("div", class_="thing")[:limit]:
                try:
                    # Score
                    score_el = thing.find("div", class_="score unvoted")
                    score = score_el.get("title", "0") if score_el else "0"
                    try:
                        score = int(score)
                    except ValueError:
                        score = 0

                    # Title & link
                    title_el = thing.find("a", class_="title")
                    title = title_el.get_text(strip=True) if title_el else ""
                    permalink = title_el.get("href") if title_el else ""

                    # Author
                    author_el = thing.find("a", class_="author")
                    author = author_el.get_text(strip=True) if author_el else ""

                    # Comments count
                    comments_el = thing.find("a", class_="comments")
                    comments_text = comments_el.get_text(strip=True) if comments_el else "0 comments"
                    num_comments = 0
                    try:
                        num_comments = int(comments_text.split()[0])
                    except (ValueError, IndexError):
                        pass

                    # Flair
                    flair_el = thing.find("span", class_="linkflairlabel")
                    flair = flair_el.get_text(strip=True) if flair_el else None

                    # Domain
                    domain_el = thing.find("span", class_="domain")
                    domain = domain_el.get_text(strip=True).lstrip("(").rstrip(")") if domain_el else ""

                    # Selftext (expand if available)
                    entry = thing.find("div", class_="entry")
                    selftext = ""
                    if entry:
                        md = entry.find("div", class_="md")
                        if md:
                            selftext = md.get_text(strip=True)[:2000]

                    # Full URL
                    post_url = permalink
                    if post_url and not post_url.startswith("http"):
                        post_url = f"https://old.reddit.com{post_url}"

                    if title:
                        posts.append({
                            "id": thing.get("data-fullname", "").replace("t3_", ""),
                            "title": title,
                            "author": author,
                            "subreddit": subreddit,
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

        except Exception as e:
            raise RuntimeError(f"Reddit scrape failed: {e}")

    def get_post(self, post_url: str) -> Dict[str, Any]:
        """Get a single Reddit post with its comments."""
        # Ensure we use old.reddit.com
        url = post_url.replace("www.reddit.com", "old.reddit.com")
        if "old.reddit.com" not in url:
            url = f"https://old.reddit.com{url}" if url.startswith("/") else url.replace("https://reddit.com", "https://old.reddit.com")

        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Post
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

            # Post selftext
            post_md = soup.find("div", class_="expando")
            selftext = post_md.get_text(strip=True)[:5000] if post_md else ""

            post = {
                "title": title,
                "author": author,
                "score": score,
                "selftext": selftext,
                "url": url,
            }

            # Comments
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

        except Exception as e:
            raise RuntimeError(f"Reddit post scrape failed: {e}")

    def close(self):
        self.client.close()
