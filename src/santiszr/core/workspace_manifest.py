from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from pydantic import BaseModel, Field


WORKSPACE_MANIFEST_VERSION = 1
WORKSPACE_MANIFEST_FILENAME = "workspace-manifest.json"


class WorkspaceManifest(BaseModel):
    version: int = WORKSPACE_MANIFEST_VERSION
    workspace: str = ""
    updated_at: str = ""
    selected_audio_path: str | None = None
    selected_subtitle_path: str | None = None
    selected_avatar_video_path: str | None = None
    selected_cover_path: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def workspace_manifest_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / WORKSPACE_MANIFEST_FILENAME


def load_workspace_manifest(workspace: str | Path) -> WorkspaceManifest | None:
    path = workspace_manifest_path(workspace)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        manifest = WorkspaceManifest.model_validate(payload)
    except Exception:
        return None
    if not manifest.workspace:
        manifest.workspace = str(Path(workspace).expanduser().resolve())
    if not manifest.updated_at:
        manifest.updated_at = _now_iso()
    return manifest


def save_workspace_manifest(
    workspace: str | Path,
    manifest: WorkspaceManifest | None = None,
) -> WorkspaceManifest:
    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    persisted = manifest.model_copy(deep=True) if manifest is not None else WorkspaceManifest()
    persisted.version = WORKSPACE_MANIFEST_VERSION
    persisted.workspace = str(workspace_path)
    persisted.updated_at = _now_iso()
    path = workspace_manifest_path(workspace_path)
    path.write_text(
        json.dumps(persisted.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return persisted


def ensure_workspace_manifest(workspace: str | Path) -> WorkspaceManifest:
    existing = load_workspace_manifest(workspace)
    if existing is not None:
        return existing
    return save_workspace_manifest(workspace)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
