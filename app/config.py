"""ScrapeX configuration."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "ScrapeX"
    app_version: str = "0.1.0"
    debug: bool = False

    # OpenRouter (for AI extraction)
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_model: str = "google/gemini-flash-1.5"

    # Rate limiting
    rate_limit_requests: int = 60  # per minute
    rate_limit_window: int = 60    # seconds

    # Browser
    browser_headless: bool = True
    browser_timeout: int = 30

    # Proxy (optional)
    proxy_url: Optional[str] = None

    # Crawl settings
    max_crawl_depth: int = 3
    max_crawl_pages: int = 50
    crawl_delay: float = 1.0

    model_config = {"env_file": ".env", "env_prefix": "SCRAPEX_"}


settings = Settings()
