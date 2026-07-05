"""Tests for the AI Studio console — a direct, no-tools prompt/response call."""
from types import SimpleNamespace

import pytest

from app.config import settings as app_settings
from app.services import runtime_settings


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "settings_file", str(tmp_path / "s.json"))
    runtime_settings._overrides.clear()
    monkeypatch.setattr(app_settings, "ai_provider", "openrouter")
    monkeypatch.setattr(app_settings, "ai_api_key", None)
    monkeypatch.setattr(app_settings, "openrouter_api_key", None)
    monkeypatch.setattr(app_settings, "ai_base_url", None)
    monkeypatch.setattr(app_settings, "ai_model", "google/gemini-flash-1.5")
    yield
    runtime_settings._overrides.clear()


def test_studio_without_provider_returns_no_llm(client):
    body = client.post("/api/v1/ai/studio", json={"prompt": "hello"}).json()
    assert body["success"] is False
    assert body["status"] == "no_llm"
    assert body["error"]


def test_studio_sends_prompt_and_returns_reply(client, monkeypatch):
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hi there!"))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr("app.services.ai_studio.get_ai_client", lambda: fake)
    client.post("/api/v1/settings/ai", json={"ai_provider": "anthropic", "ai_api_key": "sk-ant-123456"})

    body = client.post("/api/v1/ai/studio", json={
        "prompt": "Say hi", "system": "Be brief", "temperature": 0.5, "max_tokens": 50,
    }).json()

    assert body["success"] is True
    assert body["reply"] == "Hi there!"
    assert body["provider"] == "anthropic"
    assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 4}
    assert captured["messages"] == [
        {"role": "system", "content": "Be brief"},
        {"role": "user", "content": "Say hi"},
    ]
    assert captured["temperature"] == 0.5
    assert captured["max_tokens"] == 50


def test_studio_model_override(client, monkeypatch):
    async def _create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr("app.services.ai_studio.get_ai_client", lambda: fake)
    client.post("/api/v1/settings/ai", json={"ai_provider": "anthropic", "ai_api_key": "sk-ant-123456"})

    body = client.post("/api/v1/ai/studio", json={"prompt": "hi", "model": "claude-opus-4-8"}).json()
    assert body["model"] == "claude-opus-4-8"


def test_studio_reports_provider_errors(client, monkeypatch):
    async def _create(**kwargs):
        raise RuntimeError("boom")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr("app.services.ai_studio.get_ai_client", lambda: fake)
    client.post("/api/v1/settings/ai", json={"ai_provider": "anthropic", "ai_api_key": "sk-ant-123456"})

    body = client.post("/api/v1/ai/studio", json={"prompt": "hi"}).json()
    assert body["success"] is False
    assert body["status"] == "error"
    assert "boom" in body["error"]
