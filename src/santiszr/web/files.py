from __future__ import annotations

from pathlib import Path, PurePosixPath
import mimetypes

from pydantic import BaseModel

from santiszr.config.settings import AppSettings
from santiszr.core.paths import resolve_runtime_paths


class SafeFileInfo(BaseModel):
    ref: str
    scope: str
    path: str
    relative_path: str
    file_name: str
    media_type: str
    size: int
    mtime: float


def build_file_ref(
    path: str | Path,
    *,
    workspace: str | Path | None,
    settings: AppSettings,
) -> str:
    resolved = Path(path).expanduser().resolve()
    runtime_paths = resolve_runtime_paths(settings)
    workspace_root = _resolve_optional_workspace(workspace)

    if workspace_root is not None and _is_within(resolved, workspace_root):
        return f"workspace/{resolved.relative_to(workspace_root).as_posix()}"
    if _is_within(resolved, runtime_paths.data.resolve()):
        return f"data/{resolved.relative_to(runtime_paths.data.resolve()).as_posix()}"
    raise PermissionError(f"File path is outside allowed roots: {resolved}")


def resolve_safe_file(
    path_or_relative: str | Path,
    workspace: str | Path | None,
    settings: AppSettings,
) -> Path:
    raw_text = str(path_or_relative).strip()
    if not raw_text:
        raise ValueError("File path is required.")

    runtime_paths = resolve_runtime_paths(settings)
    workspace_root = _resolve_optional_workspace(workspace)
    data_root = runtime_paths.data.resolve()

    if Path(raw_text).is_absolute():
        candidate = Path(raw_text).expanduser().resolve()
        _ensure_allowed(candidate, workspace_root=workspace_root, data_root=data_root)
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    scope, relative_part = _split_ref(raw_text)
    if scope == "workspace":
        if workspace_root is None:
            raise PermissionError("Workspace file access requires a selected workspace.")
        root = workspace_root
    elif scope == "data":
        root = data_root
    else:
        if workspace_root is None:
            raise PermissionError("Relative file access requires a workspace or data-scoped file ref.")
        root = workspace_root
        relative_part = raw_text

    candidate = (root / _safe_relative_path(relative_part)).resolve()
    _ensure_allowed(candidate, workspace_root=workspace_root, data_root=data_root, expected_root=root)
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def describe_safe_file(
    path_or_relative: str | Path,
    workspace: str | Path | None,
    settings: AppSettings,
) -> SafeFileInfo:
    path = resolve_safe_file(path_or_relative, workspace, settings)
    ref = build_file_ref(path, workspace=workspace, settings=settings)
    scope, relative_path = _split_ref(ref)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    stat = path.stat()
    return SafeFileInfo(
        ref=ref,
        scope=scope or "workspace",
        path=str(path),
        relative_path=relative_path,
        file_name=path.name,
        media_type=media_type,
        size=stat.st_size,
        mtime=stat.st_mtime,
    )


def iter_file_chunks(
    path_or_relative: str | Path,
    workspace: str | Path | None,
    settings: AppSettings,
    *,
    chunk_size: int = 64 * 1024,
):
    path = resolve_safe_file(path_or_relative, workspace, settings)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _resolve_optional_workspace(workspace: str | Path | None) -> Path | None:
    if workspace is None:
        return None
    workspace_text = str(workspace).strip()
    if not workspace_text:
        return None
    return Path(workspace_text).expanduser().resolve()


def _split_ref(value: str) -> tuple[str, str]:
    normalized = value.replace("\\", "/").strip("/")
    if normalized.startswith("workspace/"):
        return "workspace", normalized[len("workspace/") :]
    if normalized.startswith("data/"):
        return "data", normalized[len("data/") :]
    return "", value


def _safe_relative_path(value: str) -> Path:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute():
        raise PermissionError(f"Absolute relative path is not allowed: {value}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise PermissionError(f"Unsafe relative path: {value}")
    return Path(*candidate.parts)


def _ensure_allowed(
    candidate: Path,
    *,
    workspace_root: Path | None,
    data_root: Path,
    expected_root: Path | None = None,
) -> None:
    allowed_roots = [root for root in (expected_root, workspace_root, data_root) if root is not None]
    if any(_is_within(candidate, root) for root in allowed_roots):
        return
    raise PermissionError(f"Access outside allowed roots is forbidden: {candidate}")


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
