from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.paths import resolve_runtime_paths


APP_STATE_VERSION = 1
MAX_RECENT_WORKSPACES = 5


@dataclass(slots=True)
class AppState:
    version: int = APP_STATE_VERSION
    last_workspace: str = ""
    recent_workspaces: list[str] = field(default_factory=list)


def app_state_path(settings: AppSettings) -> Path:
    return resolve_runtime_paths(settings).data / "app_state.json"


def load_app_state(settings: AppSettings) -> AppState:
    path = app_state_path(settings)
    if not path.exists():
        return AppState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppState()

    if not isinstance(payload, dict):
        return AppState()

    return AppState(
        version=_as_int(payload.get("version"), default=APP_STATE_VERSION),
        last_workspace=_as_text(payload.get("last_workspace")),
        recent_workspaces=_normalize_recent_workspaces(payload.get("recent_workspaces")),
    )


def save_app_state(settings: AppSettings, state: AppState) -> None:
    path = app_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": APP_STATE_VERSION,
        "last_workspace": state.last_workspace,
        "recent_workspaces": state.recent_workspaces[:MAX_RECENT_WORKSPACES],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_saved_workspace(settings: AppSettings) -> Path | None:
    last_workspace = load_app_state(settings).last_workspace
    if not last_workspace:
        return None
    try:
        path = Path(last_workspace).expanduser().resolve()
    except OSError:
        return None
    try:
        if not path.exists() or not path.is_dir():
            return None
    except OSError:
        return None
    return path


def remember_workspace(settings: AppSettings, workspace: str | Path) -> AppState:
    normalized = str(Path(workspace).expanduser().resolve())
    current = load_app_state(settings)
    recent_workspaces = [normalized]
    for candidate in current.recent_workspaces:
        candidate_text = candidate.strip()
        if candidate_text and candidate_text != normalized:
            recent_workspaces.append(candidate_text)
    state = AppState(
        version=APP_STATE_VERSION,
        last_workspace=normalized,
        recent_workspaces=recent_workspaces[:MAX_RECENT_WORKSPACES],
    )
    save_app_state(settings, state)
    return state


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_recent_workspaces(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in value:
        workspace = _as_text(candidate)
        if not workspace or workspace in seen:
            continue
        seen.add(workspace)
        normalized.append(workspace)
        if len(normalized) >= MAX_RECENT_WORKSPACES:
            break
    return normalized
