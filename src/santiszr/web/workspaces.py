from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from pydantic import BaseModel, Field

from santiszr.config.settings import AppSettings
from santiszr.core.workspace_manifest import (
    WorkspaceManifest,
    load_workspace_manifest,
    workspace_manifest_path,
)
from santiszr.core.web_workspace import (
    ensure_workspace_layout,
    get_current_workspace as core_get_current_workspace,
    get_recent_workspaces as core_get_recent_workspaces,
    select_workspace as core_select_workspace,
)
from santiszr.web.files import build_file_ref


_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
_SUBTITLE_SUFFIXES = {".srt", ".ass"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_TEXT_SUFFIXES = {".txt", ".md"}


class WorkspaceSummary(BaseModel):
    path: str
    name: str
    exists: bool
    selected: bool = False
    manifest_path: str | None = None
    mtime: float | None = None


class WorkspaceAsset(BaseModel):
    id: str
    kind: str
    display_name: str
    path: str
    relative_path: str
    size: int
    mtime: float
    linked_text_path: str | None = None
    linked_text_ref: str | None = None
    text_preview: str | None = None
    file_ref: str
    preview_ref: str | None = None
    source_dir: str | None = None


class WorkspaceAssets(BaseModel):
    workspace: str
    workspace_name: str
    manifest_path: str
    manifest: dict[str, object] | None = None
    reference_audio: list[WorkspaceAsset] = Field(default_factory=list)
    reference_videos: list[WorkspaceAsset] = Field(default_factory=list)
    generated_audio: list[WorkspaceAsset] = Field(default_factory=list)
    subtitles: list[WorkspaceAsset] = Field(default_factory=list)
    avatar_videos: list[WorkspaceAsset] = Field(default_factory=list)
    styled_videos: list[WorkspaceAsset] = Field(default_factory=list)
    pip_assets: list[WorkspaceAsset] = Field(default_factory=list)
    covers: list[WorkspaceAsset] = Field(default_factory=list)
    drafts: list[WorkspaceAsset] = Field(default_factory=list)


def select_workspace(settings: AppSettings, path: str | Path) -> WorkspaceSummary:
    workspace = core_select_workspace(settings, path)
    return describe_workspace(workspace, selected=True)


def get_recent_workspaces(settings: AppSettings) -> list[WorkspaceSummary]:
    current = core_get_current_workspace(settings)
    current_text = str(current) if current is not None else ""
    return [
        describe_workspace(workspace, selected=str(workspace) == current_text)
        for workspace in core_get_recent_workspaces(settings)
    ]


def get_current_workspace(settings: AppSettings) -> WorkspaceSummary | None:
    workspace = core_get_current_workspace(settings)
    if workspace is None:
        return None
    return describe_workspace(workspace, selected=True)


def describe_workspace(workspace: str | Path, *, selected: bool = False) -> WorkspaceSummary:
    path = Path(workspace).expanduser().resolve()
    exists = path.exists() and path.is_dir()
    manifest_path = workspace_manifest_path(path)
    mtime = path.stat().st_mtime if exists else None
    return WorkspaceSummary(
        path=str(path),
        name=path.name,
        exists=exists,
        selected=selected,
        manifest_path=str(manifest_path) if manifest_path.exists() else None,
        mtime=mtime,
    )


def scan_workspace_assets(workspace: str | Path, settings: AppSettings) -> WorkspaceAssets:
    workspace_path = ensure_workspace_layout(workspace)
    manifest = load_workspace_manifest(workspace_path)

    reference_audio = _scan_generic_assets(
        workspace_path,
        settings,
        kind="reference_audio",
        source_dirs=("reference/audio", "uploads/audio"),
        suffixes=_AUDIO_SUFFIXES,
    )
    reference_videos = _scan_generic_assets(
        workspace_path,
        settings,
        kind="reference_video",
        source_dirs=("reference/video", "uploads/video"),
        suffixes=_VIDEO_SUFFIXES,
    )
    generated_audio = _scan_audio_assets(workspace_path / "tts", workspace_path, settings)
    subtitles = _scan_generic_assets(
        workspace_path,
        settings,
        kind="subtitle",
        source_dirs=("subtitle",),
        suffixes=_SUBTITLE_SUFFIXES,
    )
    avatar_videos = _scan_generic_assets(
        workspace_path,
        settings,
        kind="avatar_video",
        source_dirs=("avatar",),
        suffixes=_VIDEO_SUFFIXES,
    )
    styled_videos = _scan_generic_assets(
        workspace_path,
        settings,
        kind="styled_video",
        source_dirs=("subtitle", "bgm", "postprocess"),
        suffixes=_VIDEO_SUFFIXES,
    )
    pip_assets = _scan_generic_assets(
        workspace_path,
        settings,
        kind="pip_asset",
        source_dirs=("pip/image", "pip/video"),
        suffixes=_IMAGE_SUFFIXES | _VIDEO_SUFFIXES,
    )
    covers = _scan_generic_assets(
        workspace_path,
        settings,
        kind="cover",
        source_dirs=("cover", "uploads/image", "publish"),
        suffixes=_IMAGE_SUFFIXES,
    )
    drafts = _scan_draft_assets(workspace_path, settings)

    return WorkspaceAssets(
        workspace=str(workspace_path),
        workspace_name=workspace_path.name,
        manifest_path=str(workspace_manifest_path(workspace_path)),
        manifest=manifest.model_dump(mode="json") if isinstance(manifest, WorkspaceManifest) else None,
        reference_audio=reference_audio,
        reference_videos=reference_videos,
        generated_audio=generated_audio,
        subtitles=subtitles,
        avatar_videos=avatar_videos,
        styled_videos=styled_videos,
        pip_assets=pip_assets,
        covers=covers,
        drafts=drafts,
    )


def _scan_audio_assets(directory: Path, workspace: Path, settings: AppSettings) -> list[WorkspaceAsset]:
    assets: list[WorkspaceAsset] = []
    if not directory.exists():
        return assets

    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
            continue
        resolved = path.resolve()
        linked_text_path = resolved.with_suffix(".txt")
        linked_text = linked_text_path if linked_text_path.exists() and linked_text_path.is_file() else None
        assets.append(
            _build_asset(
                resolved,
                workspace=workspace,
                settings=settings,
                kind="generated_audio",
                linked_text_path=linked_text,
                source_dir="tts",
            )
        )
    return _sorted_assets(assets)


def _scan_generic_assets(
    workspace: Path,
    settings: AppSettings,
    *,
    kind: str,
    source_dirs: tuple[str, ...],
    suffixes: set[str],
) -> list[WorkspaceAsset]:
    assets: list[WorkspaceAsset] = []
    seen_paths: set[str] = set()
    for source_dir in source_dirs:
        directory = workspace / source_dir
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            resolved = path.resolve()
            resolved_text = str(resolved)
            if resolved_text in seen_paths:
                continue
            seen_paths.add(resolved_text)
            assets.append(
                _build_asset(
                    resolved,
                    workspace=workspace,
                    settings=settings,
                    kind=kind,
                    source_dir=source_dir,
                )
            )
    return _sorted_assets(assets)


def _scan_draft_assets(workspace: Path, settings: AppSettings) -> list[WorkspaceAsset]:
    assets: list[WorkspaceAsset] = []
    for source_dir in ("drafts", "rewrite", "content"):
        directory = workspace / source_dir
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            resolved = path.resolve()
            display_name = f"{source_dir}/{resolved.stem}"
            assets.append(
                _build_asset(
                    resolved,
                    workspace=workspace,
                    settings=settings,
                    kind="draft",
                    source_dir=source_dir,
                    display_name=display_name,
                    text_preview=_read_text_preview(resolved),
                )
            )
    return _sorted_assets(assets)


def _build_asset(
    path: Path,
    *,
    workspace: Path,
    settings: AppSettings,
    kind: str,
    source_dir: str | None = None,
    linked_text_path: Path | None = None,
    display_name: str | None = None,
    text_preview: str | None = None,
) -> WorkspaceAsset:
    resolved = path.resolve()
    stat = resolved.stat()
    relative_path = resolved.relative_to(workspace).as_posix()
    linked_text_ref = None
    linked_text_value = None
    preview_text = text_preview
    if linked_text_path is not None:
        linked_text_value = str(linked_text_path.resolve())
        linked_text_ref = build_file_ref(linked_text_path, workspace=workspace, settings=settings)
        preview_text = _read_text_preview(linked_text_path)

    file_ref = build_file_ref(resolved, workspace=workspace, settings=settings)
    asset_id = sha1(f"{kind}|{relative_path}".encode("utf-8")).hexdigest()[:16]
    return WorkspaceAsset(
        id=asset_id,
        kind=kind,
        display_name=display_name or resolved.stem,
        path=str(resolved),
        relative_path=relative_path,
        size=stat.st_size,
        mtime=stat.st_mtime,
        linked_text_path=linked_text_value,
        linked_text_ref=linked_text_ref,
        text_preview=preview_text,
        file_ref=file_ref,
        preview_ref=file_ref,
        source_dir=source_dir,
    )


def _sorted_assets(assets: list[WorkspaceAsset]) -> list[WorkspaceAsset]:
    return sorted(
        assets,
        key=lambda item: (-item.mtime, item.display_name.lower(), item.relative_path.lower()),
    )


def _read_text_preview(path: Path, *, limit: int = 220) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            return None
    except Exception:
        return None
    normalized = " ".join(text.replace("\r", "\n").split())
    return normalized[:limit] if normalized else None
