"""Tests for the Data Viewer's prompt-based clean endpoint (/api/v1/clean)."""
import json
from types import SimpleNamespace

import pytest

from app.services import ai_cleaner


@pytest.fixture
def fake_ai(monkeypatch):
    """Stand-in AI client that echoes back reshaped rows as JSON."""
    captured = {}

    async def _create(**kwargs):
        captured["messages"] = kwargs["messages"]
        content = json.dumps({
            "items": [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}],
            "notes": "kept only the name field",
        })
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(ai_cleaner, "get_ai_client", lambda: fake)
    return captured


def test_clean_without_ai_tidies_only(client):
    resp = client.post("/api/v1/clean", json={
        "items": [{"text": "  hi <b>there</b>  ", "empty": ""}],
        "prompt": "clean it up",
    })
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "no_llm"
    assert body["items"] == [{"text": "hi there"}]
    assert "AI not configured" in body["notes"]


def test_clean_with_ai_reshapes_items(client, fake_ai):
    resp = client.post("/api/v1/clean", json={
        "items": [{"name": "Ada Lovelace", "junk": "x"}, {"name": "Alan Turing", "junk": "y"}],
        "prompt": "keep only the name field",
        "context": "people",
    })
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["items"] == [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}]
    assert body["notes"] == "kept only the name field"
    # the user's instructions and context reach the model
    sent = " ".join(m["content"] for m in fake_ai["messages"])
    assert "keep only the name field" in sent
    assert "people" in sent


def test_clean_malformed_ai_reply_falls_back(client, monkeypatch):
    async def _create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json at all"))])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(ai_cleaner, "get_ai_client", lambda: fake)

    resp = client.post("/api/v1/clean", json={
        "items": [{"a": 1}],
        "prompt": "do something",
    })
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "error"
    assert body["items"] == [{"a": 1}]  # falls back to the tidied original


def test_clean_requires_items(client):
    resp = client.post("/api/v1/clean", json={"items": [], "prompt": "clean"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_prep_items_truncates_over_the_cap(monkeypatch):
    monkeypatch.setattr(ai_cleaner, "MAX_CLEAN_ITEMS", 2)
    items = [{"i": i} for i in range(5)]
    prepped, truncated = ai_cleaner._prep_items_for_ai(items)
    assert len(prepped) == 2
    assert truncated is True
