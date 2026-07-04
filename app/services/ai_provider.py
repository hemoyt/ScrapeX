"""Bring-your-own-AI: run ScrapeX's LLM features on any OpenAI-compatible API.

Pick a provider with SCRAPEX_AI_PROVIDER and drop in its key — that's it.
Cloud (anthropic, openai, deepseek, xai/grok, groq, mistral, openrouter),
local (ollama, lmstudio — no key needed), or any other endpoint via
provider=custom + SCRAPEX_AI_BASE_URL.

Everything speaks the OpenAI chat-completions dialect: Anthropic, DeepSeek,
xAI, Groq, Mistral, Ollama, LM Studio, llama.cpp and vLLM all expose
compatible endpoints, so one client covers them all.
"""
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings

# provider -> (base_url, key_required, default_model)
PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", True, "google/gemini-flash-1.5"),
    "openai": ("https://api.openai.com/v1", True, "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com/v1/", True, "claude-sonnet-5"),
    "deepseek": ("https://api.deepseek.com/v1", True, "deepseek-chat"),
    "xai": ("https://api.x.ai/v1", True, "grok-3-mini"),
    "grok": ("https://api.x.ai/v1", True, "grok-3-mini"),  # alias of xai
    "groq": ("https://api.groq.com/openai/v1", True, "llama-3.3-70b-versatile"),
    "mistral": ("https://api.mistral.ai/v1", True, "mistral-small-latest"),
    "ollama": ("http://localhost:11434/v1", False, "llama3.1:8b"),
    "lmstudio": ("http://localhost:1234/v1", False, "local-model"),
    "custom": (None, False, "local-model"),  # base_url comes from SCRAPEX_AI_BASE_URL
}

# The historical default in Settings.ai_model — if the user never changed it,
# switching provider should switch to that provider's sensible default model.
_LEGACY_DEFAULT_MODEL = "google/gemini-flash-1.5"


def provider_name() -> str:
    name = (settings.ai_provider or "openrouter").lower().strip()
    return name if name in PROVIDERS else "custom"


def resolve_base_url() -> Optional[str]:
    if settings.ai_base_url:
        return settings.ai_base_url
    base, _, _ = PROVIDERS[provider_name()]
    # Back-compat: an explicitly overridden openrouter_base_url still wins there
    if provider_name() == "openrouter":
        return settings.openrouter_base_url or base
    return base


def resolve_api_key() -> Optional[str]:
    if settings.ai_api_key:
        return settings.ai_api_key
    if settings.openrouter_api_key:  # back-compat: old installs only set this
        return settings.openrouter_api_key
    return None


def resolve_model(override: Optional[str] = None) -> str:
    if override:
        return override
    if settings.ai_model and settings.ai_model != _LEGACY_DEFAULT_MODEL:
        return settings.ai_model
    _, _, default_model = PROVIDERS[provider_name()]
    return settings.ai_model if provider_name() == "openrouter" else default_model


def ai_enabled() -> bool:
    """True when LLM features can run: a base URL is known and, where the
    provider demands one, a key is present."""
    base = resolve_base_url()
    if not base:
        return False
    _, key_required, _ = PROVIDERS[provider_name()]
    return bool(resolve_api_key()) or not key_required


def get_ai_client() -> Optional[AsyncOpenAI]:
    """Configured AsyncOpenAI client for the chosen provider, or None when
    LLM features are disabled (callers already degrade gracefully on None)."""
    if not ai_enabled():
        return None
    # Local endpoints need no key, but the SDK insists on a non-empty string.
    return AsyncOpenAI(base_url=resolve_base_url(), api_key=resolve_api_key() or "not-needed")


def disabled_reason() -> str:
    name = provider_name()
    if name == "custom" and not settings.ai_base_url:
        return "AI provider 'custom' needs SCRAPEX_AI_BASE_URL."
    return (
        f"No API key configured for AI provider '{name}'. "
        "Set SCRAPEX_AI_API_KEY (or SCRAPEX_OPENROUTER_API_KEY), or switch "
        "SCRAPEX_AI_PROVIDER to a local one (ollama, lmstudio, custom)."
    )


def provider_info() -> dict:
    """For /health and the UI: which brain is plugged in right now."""
    return {
        "provider": provider_name(),
        "base_url": resolve_base_url(),
        "model": resolve_model(),
        "agent_model": settings.agent_model or resolve_model(),
        "enabled": ai_enabled(),
        "available_providers": sorted(PROVIDERS),
    }
