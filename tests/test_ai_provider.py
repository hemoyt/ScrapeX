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
    ("openai", "api.openai.com", "gpt-5.4-mini"),
    ("deepseek", "api.deepseek.com", "deepseek-v4-flash"),
    ("xai", "api.x.ai", "grok-4.3"),
    ("grok", "api.x.ai", "grok-4.3"),
    ("groq", "api.groq.com", "openai/gpt-oss-20b"),
    ("mistral", "api.mistral.ai", "mistral-large-latest"),
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


def test_untouched_openrouter_model_uses_current_default_not_stale_legacy():
    # Regression: an untouched model field used to resolve to the historical
    # config default (google/gemini-flash-1.5) for openrouter specifically,
    # even as that model aged out of usefulness. It must now resolve like
    # every other provider — to PROVIDERS' current default.
    settings.ai_provider = "openrouter"
    settings.ai_api_key = "sk-or-test"
    settings.ai_model = "google/gemini-flash-1.5"
    assert ai_provider.resolve_model() == ai_provider.PROVIDERS["openrouter"][2]
    assert ai_provider.resolve_model() != "google/gemini-flash-1.5"


def test_models_catalog_covers_every_cloud_provider():
    for name, (base_url, key_required, default_model) in ai_provider.PROVIDERS.items():
        if name in ("custom", "lmstudio"):  # no fixed catalog — user names their own local model
            continue
        models = ai_provider.MODELS.get(name)
        assert models, f"no curated model list for provider '{name}'"
        ids = [m["id"] for m in models]
        assert default_model in ids, f"{name}'s default_model isn't offered in its own MODELS list"


class _FakeChatClient:
    """Minimal stand-in for AsyncOpenAI's chat.completions.create()."""

    def __init__(self, replies, fail_on_response_format=False):
        self._replies = list(replies)
        self.calls = []
        self.fail_on_response_format = fail_on_response_format
        self.chat = type("_C", (), {"completions": self})()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on_response_format and "response_format" in kwargs:
            raise ValueError("response_format is not supported by this endpoint")
        content = self._replies.pop(0)
        message = type("_M", (), {"content": content})()
        return type("_R", (), {"choices": [type("_Ch", (), {"message": message})()]})()


@pytest.mark.asyncio
async def test_chat_json_parses_fenced_json():
    client = _FakeChatClient(['Sure, here you go:\n```json\n{"a": 1}\n```'])
    result = await ai_provider.chat_json(client, model="m", messages=[])
    assert result == {"a": 1}


@pytest.mark.asyncio
async def test_chat_json_retries_without_response_format():
    # The first attempt (with response_format) raises before consuming a
    # reply; only the fallback attempt (without it) pops one.
    client = _FakeChatClient(['{"ok": true}'], fail_on_response_format=True)
    result = await ai_provider.chat_json(client, model="m", messages=[])
    assert result == {"ok": True}
    assert "response_format" not in client.calls[-1]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_chat_json_raises_ai_json_error_on_garbage():
    client = _FakeChatClient(["I cannot produce JSON for that, sorry."])
    with pytest.raises(ai_provider.AIJSONError) as exc:
        await ai_provider.chat_json(client, model="m", messages=[])
    assert "sorry" in exc.value.raw
