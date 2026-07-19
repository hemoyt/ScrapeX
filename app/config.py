"""ScrapeX configuration."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "ScrapeX"
    app_version: str = "0.2.0"
    debug: bool = False

    # AI provider — bring your own AI (see app/services/ai_provider.py)
    # openrouter | openai | anthropic | deepseek | xai | grok | groq | mistral
    # | ollama | lmstudio | custom
    ai_provider: str = "openrouter"
    ai_api_key: Optional[str] = None     # generic key; falls back to openrouter_api_key
    ai_base_url: Optional[str] = None    # preset override; required for provider=custom
    ai_model: str = "google/gemini-flash-1.5"

    # Where UI-set runtime settings (AI provider/key/model) are persisted.
    settings_file: str = ".scrapex_settings.json"

    # OpenRouter (legacy names — still honored for back-compat)
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # API auth — comma-separated keys; auth is enforced only when set
    api_keys: Optional[str] = None

    # Rate limiting
    rate_limit_requests: int = 60  # per minute
    rate_limit_window: int = 60    # seconds

    # Social scraping
    cache_ttl: int = 300           # seconds, TTL for social responses
    social_timeout: float = 20     # per-platform request timeout
    nitter_instances: Optional[str] = None  # comma-separated override

    # Dataset runs (Apify-style background scraping jobs)
    run_time_budget: float = 240   # seconds a run may spend paginating
    run_max_items: int = 1000      # hard cap on max_items per run
    run_page_delay: float = 0.5    # politeness delay between pages
    run_history_limit: int = 200   # runs/datasets kept in memory

    # Persistence — runs, datasets, and schedules survive restarts.
    # Set SCRAPEX_DB_FILE="" to disable and go back to memory-only.
    db_file: str = ".scrapex_data.sqlite3"

    # Scheduler — how often the loop checks for due schedules (seconds)
    scheduler_poll_interval: float = 10.0

    # Webhooks — timeout for run-finished webhook deliveries (seconds)
    webhook_timeout: float = 10.0

    # Research agent
    agent_model: Optional[str] = None  # falls back to ai_model
    agent_max_steps: int = 8

    # Browser
    browser_headless: bool = True
    browser_timeout: int = 30

    # Proxy (optional)
    proxy_url: Optional[str] = None

    # Bring-your-own session cookies — LinkedIn and Instagram login-wall
    # anonymous traffic almost completely; pasting your own logged-in
    # session cookie (grabbed from your own browser's devtools) lets these
    # scrapers make authenticated requests instead of hitting the wall.
    # Same trust/storage model as the AI API key (see runtime_settings.py).
    linkedin_cookie: Optional[str] = None        # the `li_at` cookie value
    instagram_sessionid: Optional[str] = None    # the `sessionid` cookie value
    instagram_csrftoken: Optional[str] = None    # the `csrftoken` cookie value (optional)

    # Crawl settings
    max_crawl_depth: int = 3
    max_crawl_pages: int = 50
    crawl_delay: float = 1.0

    model_config = {"env_file": ".env", "env_prefix": "SCRAPEX_"}


settings = Settings()
