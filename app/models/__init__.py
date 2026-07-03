"""Pydantic schemas for ScrapeX API."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class ScrapeRequest(BaseModel):
    url: str = Field(..., description="URL to scrape")
    render_js: bool = Field(default=False, description="Use browser for JS rendering")
    extract_ai: bool = Field(default=False, description="Use AI to extract structured data")
    ai_prompt: Optional[str] = Field(default=None, description="What data to extract (natural language)")
    include_screenshot: bool = Field(default=False)
    timeout: int = Field(default=30, ge=5, le=120)


class ScrapeResponse(BaseModel):
    success: bool
    url: str
    title: Optional[str] = None
    content: Optional[str] = None        # Clean markdown/text
    html: Optional[str] = None           # Raw HTML
    extracted: Optional[Dict[str, Any]] = None  # AI-extracted structured data
    screenshot: Optional[str] = None      # Base64 screenshot
    metadata: Dict[str, Any] = {}
    links: List[Dict[str, Any]] = []
    error: Optional[str] = None


class CrawlRequest(BaseModel):
    url: str
    max_depth: int = Field(default=2, ge=1, le=5)
    max_pages: int = Field(default=20, ge=1, le=100)
    same_domain: bool = True
    render_js: bool = False


class CrawlStatus(BaseModel):
    id: str
    status: str  # queued, running, completed, failed
    pages_scraped: int = 0
    total_pages: int = 0
    results: Optional[List[ScrapeResponse]] = None
    error: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    num_results: int = Field(default=5, ge=1, le=20)
    scrape_results: bool = False


class TwitterRequest(BaseModel):
    # Legacy shape
    username: Optional[str] = None
    tweet_url: Optional[str] = None
    max_tweets: int = Field(default=10, ge=1, le=50)
    # Unified shape (also accepted on /social/twitter)
    query_type: Optional[str] = None
    identifier: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=50)
    options: Dict[str, Any] = {}


class RedditRequest(BaseModel):
    # Legacy shape
    subreddit: Optional[str] = None
    post_url: Optional[str] = None
    listing: str = Field(default="hot", pattern="^(hot|new|top|rising)$")
    limit: int = Field(default=10, ge=1, le=50)
    # Unified shape (also accepted on /social/reddit)
    query_type: Optional[str] = None
    identifier: Optional[str] = None
    options: Dict[str, Any] = {}


class SocialQueryType(str, Enum):
    profile = "profile"
    posts = "posts"
    post = "post"
    search = "search"


class SocialRequest(BaseModel):
    query_type: SocialQueryType = Field(
        default=SocialQueryType.posts,
        description="profile | posts | post | search",
    )
    identifier: str = Field(
        ...,
        description="Username/handle (profile, posts), URL or id (post), or search query (search)",
    )
    limit: int = Field(default=10, ge=1, le=50)
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description="Platform extras, e.g. {'listing': 'top'} for reddit, {'instance': 'fosstodon.org'} for mastodon",
    )


class SocialPost(BaseModel):
    id: Optional[str] = None
    url: Optional[str] = None
    author: Optional[str] = None
    text: Optional[str] = None
    created_at: Optional[str] = None
    stats: Dict[str, int] = {}      # likes / replies / reposts / views — only what the platform gives
    media: List[Dict[str, Any]] = []
    extra: Dict[str, Any] = {}


class SocialProfile(BaseModel):
    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    followers: Optional[int] = None
    following: Optional[int] = None
    posts_count: Optional[int] = None
    avatar_url: Optional[str] = None
    url: Optional[str] = None
    verified: Optional[bool] = None
    extra: Dict[str, Any] = {}


class SocialResponse(BaseModel):
    success: bool
    platform: str
    status: str = "ok"                  # ok | partial | blocked | error
    query_type: Optional[str] = None
    profile: Optional[SocialProfile] = None
    posts: List[SocialPost] = []
    data: List[Dict[str, Any]] = []     # legacy/raw payloads (kept for back-compat)
    source: Optional[str] = None        # strategy that served it, e.g. "fxtwitter", "innertube"
    cached: bool = False
    error: Optional[str] = None


class MultiSearchRequest(BaseModel):
    query: str
    platforms: List[str] = ["reddit", "bluesky", "hackernews", "youtube", "mastodon"]
    limit: int = Field(default=5, ge=1, le=20)


class MultiSearchResponse(BaseModel):
    success: bool
    query: str
    results: Dict[str, SocialResponse]  # keyed by platform, includes per-platform failures


class ExtractRequest(BaseModel):
    url: str
    prompt: str = Field(..., description="What to extract, e.g. 'product name and price'")
    render_js: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
