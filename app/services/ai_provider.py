"""Bring-your-own-AI: run ScrapeX's LLM features on any OpenAI-compatible API.

Pick a provider with SCRAPEX_AI_PROVIDER and drop in its key — that's it.
Cloud (anthropic, openai, deepseek, xai/grok, groq, mistral, openrouter),
local (ollama, lmstudio — no key needed), or any other endpoint via
provider=custom + SCRAPEX_AI_BASE_URL.

Everything speaks the OpenAI chat-completions dialect: Anthropic, DeepSeek,
xAI, Groq, Mistral, Ollama, LM Studio, llama.cpp and vLLM all expose
compatible endpoints, so one client covers them all.
"""
import json
import re
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.services import runtime_settings as rt

# provider -> (base_url, key_required, default_model)
PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", True, "anthropic/claude-sonnet-5"),
    "openai": ("https://api.openai.com/v1", True, "gpt-5.4-mini"),
    "anthropic": ("https://api.anthropic.com/v1/", True, "claude-sonnet-5"),
    "deepseek": ("https://api.deepseek.com/v1", True, "deepseek-v4-flash"),
    "xai": ("https://api.x.ai/v1", True, "grok-4.3"),
    "grok": ("https://api.x.ai/v1", True, "grok-4.3"),  # alias of xai
    "groq": ("https://api.groq.com/openai/v1", True, "openai/gpt-oss-20b"),
    "mistral": ("https://api.mistral.ai/v1", True, "mistral-large-latest"),
    "ollama": ("http://localhost:11434/v1", False, "llama3.1:8b"),
    "lmstudio": ("http://localhost:1234/v1", False, "local-model"),
    "custom": (None, False, "local-model"),  # base_url comes from SCRAPEX_AI_BASE_URL
}

# Curated "pick one" model lists per provider, so the Settings UI can offer a
# dropdown instead of a freeform text box the user has to get exactly right.
# Each entry is {id, label}; the frontend always adds a final "custom" option
# that reveals a text input, since providers add/retire models constantly.
MODELS: Dict[str, List[Dict[str, str]]] = {
    "openrouter": [
        {"id": "anthropic/claude-sonnet-5", "label": "Claude Sonnet 5 — balanced (default)"},
        {"id": "anthropic/claude-opus-4-8", "label": "Claude Opus 4.8 — most capable"},
        {"id": "openai/gpt-5.4-mini", "label": "GPT-5.4 mini"},
        {"id": "google/gemini-3-flash", "label": "Gemini 3 Flash"},
        {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B"},
    ],
    "openai": [
        {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini — fast & affordable (default)"},
        {"id": "gpt-5.5", "label": "GPT-5.5 — flagship reasoning"},
        {"id": "gpt-5.4", "label": "GPT-5.4 — strong all-rounder"},
        {"id": "gpt-5.4-nano", "label": "GPT-5.4 nano — cheapest, high volume"},
        {"id": "gpt-4o-mini", "label": "GPT-4o mini — legacy, still served"},
    ],
    "anthropic": [
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 — balanced (default)"},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 — most capable"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 — fastest & cheapest"},
        {"id": "claude-fable-5", "label": "Claude Fable 5 — frontier reasoning"},
    ],
    "deepseek": [
        {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash — fast & economical (default)"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro — strongest reasoning"},
        {"id": "deepseek-chat", "label": "deepseek-chat — legacy alias"},
    ],
    "xai": [
        {"id": "grok-4.3", "label": "Grok 4.3 — flagship, fastest (default)"},
        {"id": "grok-4.20-0309-reasoning", "label": "Grok 4.20 — reasoning"},
        {"id": "grok-4.20-0309-non-reasoning", "label": "Grok 4.20 — instant/non-reasoning"},
    ],
    "groq": [
        {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B — fast (default)"},
        {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B — stronger reasoning"},
        {"id": "qwen/qwen3-32b", "label": "Qwen3 32B"},
        {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (Groq is retiring this)"},
    ],
    "mistral": [
        {"id": "mistral-large-latest", "label": "Mistral Large — flagship reasoning (default)"},
        {"id": "mistral-small-latest", "label": "Mistral Small — fast, high-throughput"},
        {"id": "codestral-latest", "label": "Codestral — code generation"},
    ],
    "ollama": [
        {"id": "llama3.1:8b", "label": "llama3.1:8b — solid all-rounder (default)"},
        {"id": "qwen2.5:7b", "label": "qwen2.5:7b"},
        {"id": "mistral:7b", "label": "mistral:7b"},
    ],
}
MODELS["grok"] = MODELS["xai"]  # alias

# The historical default in Settings.ai_model — if the user never changed it,
# switching provider should switch to that provider's sensible default model.
_LEGACY_DEFAULT_MODEL = "google/gemini-flash-1.5"


# All AI config reads go through rt.get() so UI-set runtime overrides win over
# the SCRAPEX_* env vars (rt.get falls back to the env value when unset).


def provider_name() -> str:
    name = (rt.get("ai_provider") or "openrouter").lower().strip()
    return name if name in PROVIDERS else "custom"


def resolve_base_url() -> Optional[str]:
    if rt.get("ai_base_url"):
        return rt.get("ai_base_url")
    base, _, _ = PROVIDERS[provider_name()]
    # Back-compat: an explicitly overridden openrouter_base_url still wins there
    if provider_name() == "openrouter":
        return settings.openrouter_base_url or base
    return base


def resolve_api_key() -> Optional[str]:
    if rt.get("ai_api_key"):
        return rt.get("ai_api_key")
    if settings.openrouter_api_key:  # back-compat: old installs only set this
        return settings.openrouter_api_key
    return None


def resolve_model(override: Optional[str] = None) -> str:
    if override:
        return override
    model = rt.get("ai_model")
    if model and model != _LEGACY_DEFAULT_MODEL:
        return model
    _, _, default_model = PROVIDERS[provider_name()]
    return default_model


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
    if name == "custom" and not resolve_base_url():
        return "AI provider 'custom' needs a Base URL — set it in Settings or SCRAPEX_AI_BASE_URL."
    return (
        f"No API key set for AI provider '{name}'. "
        "Add one in the app's Settings tab (or set SCRAPEX_AI_API_KEY), or "
        "switch to a local provider (ollama, lmstudio) that needs no key."
    )


def provider_info() -> dict:
    """For /health and the UI: which brain is plugged in right now."""
    return {
        "provider": provider_name(),
        "base_url": resolve_base_url(),
        "model": resolve_model(),
        "agent_model": rt.get("agent_model") or resolve_model(),
        "enabled": ai_enabled(),
        "available_providers": sorted(PROVIDERS),
    }


class AIJSONError(Exception):
    """A chat_json() call couldn't find valid JSON in the model's reply."""

    def __init__(self, raw: str):
        super().__init__("AI response was not valid JSON")
        self.raw = raw


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def _first_json_value(text: str) -> str:
    """Best-effort: pull the first balanced {...} or [...] blob out of
    free-form text (models asked for "JSON only" often add a code fence or a
    sentence of preamble anyway)."""
    text = _strip_code_fence(text)
    for open_c, close_c in ("{}", "[]"):
        start = text.find(open_c)
        if start == -1:
            continue
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


async def chat_json(
    client: AsyncOpenAI, *, model: str, messages: list, temperature: float = 0.1,
    max_tokens: int = 1500,
) -> Any:
    """Chat completion that returns parsed JSON, tolerating providers that
    ignore or reject `response_format` — notably Anthropic's OpenAI-compat
    layer (which silently ignores it) and many local OpenAI-compatible
    runtimes (which can reject the unknown field outright)."""
    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"},
        )
    except Exception:
        response = await client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        )
    raw = response.choices[0].message.content or ""
    try:
        return json.loads(_first_json_value(raw))
    except json.JSONDecodeError:
        raise AIJSONError(raw)
