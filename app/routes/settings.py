"""Runtime settings — configure the AI provider/key/model from the app UI.

GET  /settings/ai        current AI config (key masked, never leaked)
POST /settings/ai        set provider / api_key / model / base_url (persisted)
POST /settings/ai/test   send a tiny prompt to check the config actually works
POST /settings/ai/clear  drop UI overrides, fall back to env vars
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import runtime_settings
from app.services.ai_provider import (
    PROVIDERS,
    disabled_reason,
    get_ai_client,
    provider_info,
    resolve_model,
)

router = APIRouter()


class AISettings(BaseModel):
    ai_provider: Optional[str] = None
    ai_api_key: Optional[str] = None   # "" clears it; omit to leave unchanged
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None
    agent_model: Optional[str] = None


def _mask(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return (key[:4] + "…" + key[-4:]) if len(key) > 10 else "••••"


def _state() -> dict:
    info = provider_info()
    key = runtime_settings.get("ai_api_key")
    info["api_key_set"] = bool(key)
    info["api_key_hint"] = _mask(key)
    # which fields are set from the UI vs inherited from the environment
    info["from_ui"] = {f: runtime_settings.is_overridden(f) for f in runtime_settings.FIELDS}
    info["providers"] = {
        name: {"needs_key": key_required, "default_model": default_model}
        for name, (_, key_required, default_model) in PROVIDERS.items()
    }
    return info


@router.get("/settings/ai")
async def get_ai_settings():
    return _state()


@router.post("/settings/ai")
async def set_ai_settings(update: AISettings):
    # exclude_unset: only touch fields the client actually sent, so leaving the
    # API-key box blank doesn't wipe a previously saved key.
    runtime_settings.update(update.model_dump(exclude_unset=True))
    return _state()


@router.post("/settings/ai/clear")
async def clear_ai_settings():
    runtime_settings.clear()
    return _state()


@router.post("/settings/ai/test")
async def test_ai_settings():
    """Round-trip a one-token prompt so the user gets a real yes/no."""
    client = get_ai_client()
    if client is None:
        return {"ok": False, "error": disabled_reason()}
    try:
        resp = await client.chat.completions.create(
            model=resolve_model(),
            messages=[{"role": "user", "content": "Reply with just: ok"}],
            max_tokens=5,
            temperature=0,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return {"ok": True, "model": resolve_model(), "reply": reply}
    except Exception as e:
        return {"ok": False, "model": resolve_model(), "error": f"{type(e).__name__}: {e}"}
