from __future__ import annotations

from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.app_state import remember_workspace, resolve_saved_workspace
from santiszr.core.paths import ensure_workspace as ensure_workspace_dir
from santiszr.core.workspace_manifest import ensure_workspace_manifest


WORKSPACE_SUBDIRECTORIES = (
    "content",
    "rewrite",
    "tts",
    "subtitle",
    "avatar",
    "cover",
    "bgm",
    "publish",
    "uploads",
    "reference",
    "drafts",
    "postprocess",
)


def select_workspace(settings: AppSettings, path: str | Path) -> Path:
    raw_path = str(path).strip()
    if not raw_path:
        raise ValueError("Workspace path is required.")
    normalized = Path(raw_path).expanduser()
    if normalized.exists() and not normalized.is_dir():
        raise ValueError(f"Workspace path is not a directory: {normalized}")

    workspace = ensure_workspace_dir(normalized)
    ensure_workspace_layout(workspace)
    ensure_workspace_manifest(workspace)
    remember_workspace(settings, workspace)
    return workspace


def ensure_workspace_layout(workspace: str | Path) -> Path:
    workspace_path = ensure_workspace_dir(workspace)
    for name in WORKSPACE_SUBDIRECTORIES:
        (workspace_path / name).mkdir(parents=True, exist_ok=True)
    (workspace_path / "reference" / "audio").mkdir(parents=True, exist_ok=True)
    (workspace_path / "reference" / "video").mkdir(parents=True, exist_ok=True)
    return workspace_path


def get_recent_workspaces(settings: AppSettings) -> list[Path]:
    recent: list[Path] = []
    seen: set[str] = set()
    from santiszr.core.app_state import load_app_state

    for raw_path in load_app_state(settings).recent_workspaces:
        try:
            candidate = Path(raw_path).expanduser().resolve()
        except OSError:
            continue
        candidate_text = str(candidate)
        if candidate_text in seen or not candidate.exists() or not candidate.is_dir():
            continue
        seen.add(candidate_text)
        recent.append(candidate)
    return recent


def get_current_workspace(settings: AppSettings) -> Path | None:
    return resolve_saved_workspace(settings)
