"""Twitter/X scraper — uses browser-based approach via Nitter or direct scraping."""
import asyncio
import re
from typing import Optional, List, Dict, Any
from urllib.parse import quote


class TwitterService:
    """Scrape Twitter/X public profiles and tweets.

    Uses Nitter mirrors (privacy-friendly Twitter frontends) to avoid
    authentication requirements. Falls back to direct browser scraping.
    """

    NITTER_INSTANCES = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
    ]

    def __init__(self):
        import httpx
        self.client = httpx.Client(
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )

    def _try_nitter(self, username: str, max_tweets: int = 10) -> List[Dict[str, Any]]:
        """Try scraping via Nitter instances."""
        from bs4 import BeautifulSoup

        for instance in self.NITTER_INSTANCES:
            try:
                url = f"{instance}/{username}"
                resp = self.client.get(url, follow_redirects=True)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                tweets = []

                for item in soup.find_all("div", class_="timeline-item")[:max_tweets]:
                    try:
                        # Extract tweet content
                        content_el = item.find("div", class_="tweet-content")
                        date_el = item.find("span", class_="tweet-date")
                        stats_el = item.find_all("span", class_="tweet-stat")

                        content = content_el.get_text(strip=True) if content_el else ""
                        date = date_el.find("a").get("title", "") if date_el else ""

                        # Extract stats
                        replies = retweets = likes = 0
                        if len(stats_el) >= 3:
                            replies = self._parse_stat(stats_el[0].get_text())
                            retweets = self._parse_stat(stats_el[1].get_text())
                            likes = self._parse_stat(stats_el[2].get_text())

                        if content:
                            tweets.append({
                                "content": content[:500],
                                "date": date,
                                "replies": replies,
                                "retweets": retweets,
                                "likes": likes,
                            })
                    except Exception:
                        continue

                if tweets:
                    return tweets

            except Exception:
                continue

        return []

    def get_tweets(self, username: str, max_tweets: int = 10) -> List[Dict[str, Any]]:
        """Get recent tweets from a user.

        Returns list of tweets with: content, date, likes, retweets.
        """
        # Clean username
        username = username.lstrip("@").strip()

        # Try Nitter first
        tweets = self._try_nitter(username, max_tweets)
        if tweets:
            return tweets

        # Return helpful error if Nitter fails
        return [{
            "content": f"[Nitter instances unavailable. Visit https://x.com/{username} directly.]",
            "date": "",
            "replies": 0,
            "retweets": 0,
            "likes": 0,
            "_error": "All Nitter instances are down. Twitter/X direct scraping requires browser-based mode.",
        }]

    def get_tweet_by_url(self, tweet_url: str) -> Optional[Dict[str, Any]]:
        """Extract a single tweet by URL using Nitter."""
        from bs4 import BeautifulSoup

        # Convert twitter.com URL to nitter
        for instance in self.NITTER_INSTANCES:
            try:
                nitter_url = tweet_url.replace("twitter.com", instance.split("//")[1])
                nitter_url = nitter_url.replace("x.com", instance.split("//")[1])

                resp = self.client.get(nitter_url, follow_redirects=True)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                content_el = soup.find("div", class_="tweet-content")
                date_el = soup.find("span", class_="tweet-date")

                if content_el:
                    return {
                        "content": content_el.get_text(strip=True)[:1000],
                        "date": date_el.find("a").get("title", "") if date_el else "",
                        "url": tweet_url,
                    }
            except Exception:
                continue

        return {"error": "Could not fetch tweet. Nitter instances may be down."}

    @staticmethod
    def _parse_stat(text: str) -> int:
        """Parse '1.2K' -> 1200"""
        text = text.strip().upper().replace(",", ".")
        try:
            if "K" in text:
                return int(float(text.replace("K", "")) * 1000)
            if "M" in text:
                return int(float(text.replace("M", "")) * 1000000)
            return int(text)
        except (ValueError, TypeError):
            return 0

    def close(self):
        self.client.close()
