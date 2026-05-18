from __future__ import annotations

import json
from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.paths import resolve_runtime_paths


def llm_config_path(settings: AppSettings) -> Path:
    return resolve_runtime_paths(settings).data / "config" / "llm.json"


def load_persisted_llm_settings(settings: AppSettings) -> bool:
    path = llm_config_path(settings)
    if not path.exists():
        return False

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False

    api_key = str(payload.get("api_key") or "").strip()
    api_base = str(payload.get("api_base") or "").strip()
    model = str(payload.get("model") or "").strip()

    if api_key:
        settings.llm.api_key = api_key
    if api_base:
        settings.llm.api_base = api_base
    if model:
        settings.llm.model = model
    return True


def save_persisted_llm_settings(settings: AppSettings) -> Path:
    path = llm_config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "api_key": settings.llm.api_key or "",
                "api_base": settings.llm.api_base,
                "model": settings.llm.model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
