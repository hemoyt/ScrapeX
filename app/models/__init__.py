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
    username: Optional[str] = None
    tweet_url: Optional[str] = None
    max_tweets: int = Field(default=10, ge=1, le=50)


class RedditRequest(BaseModel):
    subreddit: Optional[str] = None
    post_url: Optional[str] = None
    listing: str = Field(default="hot", pattern="^(hot|new|top|rising)$")
    limit: int = Field(default=10, ge=1, le=50)


class SocialResponse(BaseModel):
    success: bool
    platform: str
    data: List[Dict[str, Any]] = []
    error: Optional[str] = None


class ExtractRequest(BaseModel):
    url: str
    prompt: str = Field(..., description="What to extract, e.g. 'product name and price'")
    render_js: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
