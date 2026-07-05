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
    include_answer: bool = Field(
        default=False,
        description="Synthesize an LLM answer over the results (requires OpenRouter key)",
    )


class SearchResponse(BaseModel):
    success: bool
    query: str
    answer: Optional[str] = None        # only when include_answer and a key is set
    results: List[Dict[str, Any]] = []  # {title, url, snippet, score, content?}
    error: Optional[str] = None


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
    clean: bool = False


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
    clean: bool = False


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
    clean: bool = Field(
        default=False,
        description="Tidy the output (strip HTML noise, drop raw payloads) and, when an AI provider is configured, add a plain-language summary",
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
    summary: Optional[str] = None       # plain-language AI summary (only when clean=true and AI is configured)
    cached: bool = False
    error: Optional[str] = None


class MultiSearchRequest(BaseModel):
    query: str
    platforms: List[str] = ["reddit", "bluesky", "hackernews", "youtube", "mastodon"]
    limit: int = Field(default=5, ge=1, le=20)
    clean: bool = Field(default=False, description="Tidy results and add one AI summary across all platforms")


class MultiSearchResponse(BaseModel):
    success: bool
    query: str
    summary: Optional[str] = None       # cross-platform AI summary (clean=true + AI configured)
    results: Dict[str, SocialResponse]  # keyed by platform, includes per-platform failures


class ProfileFindRequest(BaseModel):
    """Find someone across platforms from just a username."""
    username: str = Field(..., min_length=1, description="Handle to look up, with or without @")
    platforms: Optional[List[str]] = Field(
        default=None,
        description="Platforms to check; omit for every profile-capable platform",
    )


class ProfileFindResponse(BaseModel):
    success: bool
    username: str
    found: List[str] = []               # platforms that returned a profile
    checked: List[str] = []             # platforms that were queried
    results: Dict[str, SocialResponse] = {}  # per-platform outcome, failures included


class RunRequest(BaseModel):
    """Start an Apify-style dataset run: paginate a platform until max_items
    are collected or the time budget runs out."""
    platform: str = Field(..., description="Platform name, e.g. reddit, hackernews, bluesky")
    query_type: SocialQueryType = Field(default=SocialQueryType.posts)
    identifier: str = Field(..., description="Username/handle, URL, or search query")
    max_items: int = Field(default=100, ge=1, le=5000, description="Stop after this many items")
    options: Dict[str, Any] = Field(default_factory=dict)
    clean: bool = Field(
        default=False,
        description="Tidy every item as it's collected and add an AI summary of the dataset at the end",
    )


class RunInfo(BaseModel):
    id: str
    dataset_id: str
    platform: str
    query_type: str
    identifier: str
    status: str = "READY"       # READY | RUNNING | SUCCEEDED | TIMED_OUT | ABORTED | FAILED
    max_items: int
    item_count: int = 0
    pages_fetched: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    source: Optional[str] = None        # strategy that served the pages
    status_detail: Optional[str] = None # last platform status (ok/partial/...) or note
    summary: Optional[str] = None       # AI summary of the dataset (clean=true + AI configured)
    error: Optional[str] = None


class DatasetInfo(BaseModel):
    id: str
    run_id: str
    platform: str
    item_count: int
    created_at: str


class DatasetItemsPage(BaseModel):
    dataset_id: str
    total: int
    offset: int
    limit: int
    count: int
    items: List[Dict[str, Any]]


class ExtractRequest(BaseModel):
    url: str
    prompt: str = Field(..., description="What to extract, e.g. 'product name and price'")
    render_js: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class CompetitorRequest(BaseModel):
    product: str = Field(..., description="Your product/company name (optionally with a short descriptor)")
    max_competitors: int = Field(default=5, ge=1, le=10)
    enrich: bool = Field(default=True, description="Pull social profiles and mentions for each competitor")


class Competitor(BaseModel):
    name: str
    website: Optional[str] = None
    description: Optional[str] = None
    handles: Dict[str, str] = {}                 # e.g. {"twitter": "vercel", "youtube": "@vercel"}
    profiles: Dict[str, SocialProfile] = {}      # platform -> profile (enrichment)
    mentions: Dict[str, List[SocialPost]] = {}   # platform -> recent posts mentioning them


class CompetitorResponse(BaseModel):
    success: bool
    product: str
    competitors: List[Competitor] = []
    sources: List[Dict[str, Any]] = []           # web results the discovery was grounded on
    status: str = "ok"                           # ok | partial | no_llm | error
    error: Optional[str] = None


class AgentRequest(BaseModel):
    query: str = Field(..., description="Natural-language research question")
    depth: str = Field(default="basic", pattern="^(basic|advanced)$")
    max_sources: int = Field(default=5, ge=1, le=20)
    include_social: bool = Field(default=True, description="Let the agent use social platform tools")
    model: Optional[str] = Field(default=None, description="Override the OpenRouter model")


class AgentSource(BaseModel):
    id: int
    url: str
    title: str = ""
    snippet: str = ""
    platform: str = "web"


class AgentStep(BaseModel):
    step: int
    tool: str
    args: Dict[str, Any] = {}
    result_summary: str = ""


class AgentResponse(BaseModel):
    success: bool
    query: str
    answer: Optional[str] = None       # markdown with [n] citations
    sources: List[AgentSource] = []
    steps: List[AgentStep] = []
    usage: Dict[str, int] = {}         # prompt_tokens, completion_tokens, llm_calls, tool_calls
    status: str = "ok"                 # ok | no_llm | max_steps_reached | error
    error: Optional[str] = None


class AIStudioRequest(BaseModel):
    prompt: str = Field(..., description="Message to send to the model")
    system: Optional[str] = Field(default=None, description="Optional system prompt")
    model: Optional[str] = Field(default=None, description="Override the configured model for this call")
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=8000)


class AIStudioResponse(BaseModel):
    success: bool
    provider: str
    model: str
    reply: Optional[str] = None
    usage: Dict[str, int] = {}   # prompt_tokens, completion_tokens
    status: str = "ok"           # ok | no_llm | error
    error: Optional[str] = None


class CleanRequest(BaseModel):
    """Reshape/clean any list of scraped items with a free-text instruction,
    e.g. 'keep only name and email, drop duplicates, one line per person'."""
    items: List[Dict[str, Any]] = Field(..., min_length=1, description="Rows to clean (from a run, a scrape, or pasted JSON)")
    prompt: str = Field(..., min_length=1, description="Plain-language instructions for how to clean/reshape the data")
    context: Optional[str] = Field(default=None, description="Short label for what this data is, e.g. platform or query")


class CleanResponse(BaseModel):
    success: bool
    items: List[Dict[str, Any]] = []   # cleaned/reshaped rows
    notes: Optional[str] = None        # short explanation of what changed
    truncated: bool = False            # true if only a prefix of the input was sent to the AI
    status: str = "ok"                 # ok | no_llm | error
    error: Optional[str] = None
