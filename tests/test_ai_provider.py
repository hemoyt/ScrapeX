"""Tests for the bring-your-own-AI provider layer."""
import pytest

from app.config import settings
from app.services import ai_provider


@pytest.fixture(autouse=True)
def _reset_ai_settings():
    saved = (
        settings.ai_provider, settings.ai_api_key, settings.ai_base_url,
        settings.ai_model, settings.openrouter_api_key,
    )
    yield
    (settings.ai_provider, settings.ai_api_key, settings.ai_base_url,
     settings.ai_model, settings.openrouter_api_key) = saved


def test_default_openrouter_disabled_without_key():
    settings.ai_provider = "openrouter"
    settings.ai_api_key = None
    settings.openrouter_api_key = None
    assert ai_provider.ai_enabled() is False
    assert ai_provider.get_ai_client() is None
    assert "API key" in ai_provider.disabled_reason()


def test_legacy_openrouter_key_still_works():
    settings.ai_provider = "openrouter"
    settings.ai_api_key = None
    settings.openrouter_api_key = "sk-or-legacy"
    assert ai_provider.ai_enabled() is True
    client = ai_provider.get_ai_client()
    assert client is not None
    assert "openrouter.ai" in str(client.base_url)
    assert client.api_key == "sk-or-legacy"


@pytest.mark.parametrize("provider,base_fragment,default_model", [
    ("anthropic", "api.anthropic.com", "claude-sonnet-5"),
    ("openai", "api.openai.com", "gpt-4o-mini"),
    ("deepseek", "api.deepseek.com", "deepseek-chat"),
    ("xai", "api.x.ai", "grok-3-mini"),
    ("grok", "api.x.ai", "grok-3-mini"),
    ("groq", "api.groq.com", "llama-3.3-70b-versatile"),
    ("mistral", "api.mistral.ai", "mistral-small-latest"),
])
def test_cloud_provider_presets(provider, base_fragment, default_model):
    settings.ai_provider = provider
    settings.ai_api_key = "sk-test"
    settings.ai_base_url = None
    settings.ai_model = "google/gemini-flash-1.5"  # untouched legacy default
    assert ai_provider.ai_enabled() is True
    assert base_fragment in ai_provider.resolve_base_url()
    # untouched default model -> provider's own sensible default
    assert ai_provider.resolve_model() == default_model


def test_explicit_model_always_wins():
    settings.ai_provider = "anthropic"
    settings.ai_api_key = "sk-test"
    settings.ai_model = "claude-opus-4-8"
    assert ai_provider.resolve_model() == "claude-opus-4-8"
    assert ai_provider.resolve_model("override-model") == "override-model"


def test_ollama_needs_no_key():
    settings.ai_provider = "ollama"
    settings.ai_api_key = None
    settings.openrouter_api_key = None
    settings.ai_base_url = None
    settings.ai_model = "google/gemini-flash-1.5"
    assert ai_provider.ai_enabled() is True
    client = ai_provider.get_ai_client()
    assert "localhost:11434" in str(client.base_url)
    assert ai_provider.resolve_model() == "llama3.1:8b"


def test_custom_requires_base_url():
    settings.ai_provider = "custom"
    settings.ai_api_key = None
    settings.openrouter_api_key = None
    settings.ai_base_url = None
    assert ai_provider.ai_enabled() is False
    assert "SCRAPEX_AI_BASE_URL" in ai_provider.disabled_reason()

    settings.ai_base_url = "http://my-vllm:8080/v1"
    assert ai_provider.ai_enabled() is True
    assert "my-vllm:8080" in str(ai_provider.get_ai_client().base_url)


def test_unknown_provider_treated_as_custom():
    settings.ai_provider = "some-future-thing"
    settings.ai_base_url = None
    assert ai_provider.provider_name() == "custom"


def test_health_exposes_provider_not_key(client):
    settings.ai_provider = "ollama"
    settings.ai_api_key = "super-secret"
    body = client.get("/health").json()
    assert body["ai"]["provider"] == "ollama"
    assert body["ai"]["enabled"] is True
    assert "super-secret" not in str(body)
