"""Tests for runtime AI settings (set provider/key from the app UI)."""
from types import SimpleNamespace

import pytest

from app.config import settings as app_settings
from app.services import ai_provider, runtime_settings


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Point the settings file at a temp path and reset overrides around
    each test so nothing leaks between tests or onto the real repo."""
    monkeypatch.setattr(app_settings, "settings_file", str(tmp_path / "s.json"))
    runtime_settings._overrides.clear()
    # start from a known env baseline
    monkeypatch.setattr(app_settings, "ai_provider", "openrouter")
    monkeypatch.setattr(app_settings, "ai_api_key", None)
    monkeypatch.setattr(app_settings, "openrouter_api_key", None)
    monkeypatch.setattr(app_settings, "ai_base_url", None)
    monkeypatch.setattr(app_settings, "ai_model", "google/gemini-flash-1.5")
    yield
    runtime_settings._overrides.clear()


def test_get_shows_disabled_by_default(client):
    body = client.get("/api/v1/settings/ai").json()
    assert body["provider"] == "openrouter"
    assert body["enabled"] is False
    assert body["api_key_set"] is False
    assert "anthropic" in body["available_providers"]
    assert body["providers"]["ollama"]["needs_key"] is False


def test_set_provider_and_key_enables_ai(client):
    body = client.post("/api/v1/settings/ai", json={
        "ai_provider": "anthropic", "ai_api_key": "sk-ant-secret123456",
    }).json()
    assert body["provider"] == "anthropic"
    assert body["enabled"] is True
    assert body["api_key_set"] is True
    # key is masked, never returned in full
    assert "secret" not in str(body)
    assert body["api_key_hint"].startswith("sk-a")
    # and the runtime override actually drives the client
    assert "api.anthropic.com" in ai_provider.resolve_base_url()
    assert ai_provider.resolve_api_key() == "sk-ant-secret123456"


def test_key_persists_and_blank_does_not_wipe_it(client):
    client.post("/api/v1/settings/ai", json={"ai_provider": "openai", "ai_api_key": "sk-keep-me-123456"})
    # a later save that changes only the model must NOT clear the key
    body = client.post("/api/v1/settings/ai", json={"ai_model": "gpt-4o"}).json()
    assert body["api_key_set"] is True
    assert body["model"] == "gpt-4o"
    assert ai_provider.resolve_api_key() == "sk-keep-me-123456"


def test_empty_string_clears_a_field(client):
    client.post("/api/v1/settings/ai", json={"ai_provider": "openai", "ai_api_key": "sk-x-123456789"})
    body = client.post("/api/v1/settings/ai", json={"ai_api_key": ""}).json()
    assert body["api_key_set"] is False


def test_local_provider_needs_no_key(client):
    body = client.post("/api/v1/settings/ai", json={"ai_provider": "ollama"}).json()
    assert body["provider"] == "ollama"
    assert body["enabled"] is True
    assert body["api_key_set"] is False


def test_settings_persist_to_disk(client):
    client.post("/api/v1/settings/ai", json={"ai_provider": "deepseek", "ai_api_key": "sk-disk-123456"})
    # simulate a restart: reload overrides from the file
    runtime_settings._overrides.clear()
    runtime_settings._load()
    assert runtime_settings.get("ai_provider") == "deepseek"
    assert runtime_settings.get("ai_api_key") == "sk-disk-123456"


def test_clear_falls_back_to_env(client, monkeypatch):
    client.post("/api/v1/settings/ai", json={"ai_provider": "xai", "ai_api_key": "sk-xai-123456"})
    monkeypatch.setattr(app_settings, "ai_provider", "openrouter")
    body = client.post("/api/v1/settings/ai/clear").json()
    assert body["provider"] == "openrouter"     # back to env
    assert body["api_key_set"] is False


def test_test_endpoint_reports_missing_provider(client):
    body = client.post("/api/v1/settings/ai/test").json()
    assert body["ok"] is False
    assert body["error"]


def test_test_endpoint_success(client, monkeypatch):
    async def _create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr("app.routes.settings.get_ai_client", lambda: fake)
    client.post("/api/v1/settings/ai", json={"ai_provider": "ollama"})
    body = client.post("/api/v1/settings/ai/test").json()
    assert body["ok"] is True
    assert body["reply"] == "ok"


def test_health_reflects_runtime_provider(client):
    client.post("/api/v1/settings/ai", json={"ai_provider": "groq", "ai_api_key": "sk-groq-123456"})
    ai = client.get("/health").json()["ai"]
    assert ai["provider"] == "groq"
    assert ai["enabled"] is True
