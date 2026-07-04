"""Runtime-adjustable settings — set the AI provider/key/model from the app
UI instead of only through environment variables.

Precedence: a value set here (via POST /api/v1/settings/ai, persisted to a
JSON file) overrides the matching SCRAPEX_* env var. Clearing a field falls
back to the env value. The file is written 0600 and holds the API key in
plaintext — same trust level as an .env file on the same host.
"""
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings

# Fields the UI is allowed to override at runtime.
FIELDS = ("ai_provider", "ai_api_key", "ai_base_url", "ai_model", "agent_model")

_LOCK = threading.Lock()
_overrides: Dict[str, Any] = {}


def _path() -> Path:
    return Path(settings.settings_file)


def _load() -> None:
    global _overrides
    try:
        p = _path()
        if p.exists():
            data = json.loads(p.read_text())
            _overrides = {k: v for k, v in data.items() if k in FIELDS and v not in (None, "")}
    except Exception:
        _overrides = {}


def _persist() -> None:
    try:
        p = _path()
        p.write_text(json.dumps(_overrides, indent=2))
        os.chmod(p, 0o600)
    except Exception:
        pass  # non-fatal — overrides still apply from memory this run


def get(field: str) -> Optional[Any]:
    """Effective value: runtime override if set, else the env/config value."""
    val = _overrides.get(field)
    if val not in (None, ""):
        return val
    return getattr(settings, field, None)


def overrides() -> Dict[str, Any]:
    return dict(_overrides)


def is_overridden(field: str) -> bool:
    return _overrides.get(field) not in (None, "")


def update(values: Dict[str, Any]) -> None:
    """Apply changed fields. A value of "" or None clears that override
    (falls back to env); any other value sets it."""
    with _LOCK:
        for k, v in values.items():
            if k not in FIELDS:
                continue
            if v in (None, ""):
                _overrides.pop(k, None)
            else:
                _overrides[k] = v.strip() if isinstance(v, str) else v
        _persist()


def clear() -> None:
    with _LOCK:
        _overrides.clear()
        _persist()


_load()
