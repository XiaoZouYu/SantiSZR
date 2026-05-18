from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re

from santiszr.config.settings import AppSettings


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    data: Path
    cache: Path
    logs: Path
    workspaces: Path
    models: Path
    cosyvoice_models: Path
    voxcpm_models: Path
    whisper_models: Path
    tuilionnx_models: Path


def resolve_runtime_paths(settings: AppSettings) -> RuntimePaths:
    root = Path(__file__).resolve().parents[3]
    data = Path(settings.data_dir) if settings.data_dir else root / "data"
    cache = Path(settings.cache_dir) if settings.cache_dir else root / ".cache"
    logs = Path(settings.log_dir) if settings.log_dir else root / "logs"
    workspaces = data / "workspaces"
    models = Path(settings.models.root_dir) if settings.models.root_dir else root / "models"
    cosyvoice_models = (
        Path(settings.models.cosyvoice_model_dir)
        if settings.models.cosyvoice_model_dir
        else models / "cosyvoice"
    )
    voxcpm_models = (
        Path(settings.models.voxcpm_model_dir)
        if settings.models.voxcpm_model_dir
        else models / "voxcpm" / "VoxCPM2"
    )
    whisper_models = (
        Path(settings.models.whisper_model_dir)
        if settings.models.whisper_model_dir
        else models / "whisper"
    )
    tuilionnx_models = (
        Path(settings.models.tuilionnx_model_dir)
        if settings.models.tuilionnx_model_dir
        else models / "tuilionnx"
    )
    return RuntimePaths(
        root=root,
        data=data,
        cache=cache,
        logs=logs,
        workspaces=workspaces,
        models=models,
        cosyvoice_models=cosyvoice_models,
        voxcpm_models=voxcpm_models,
        whisper_models=whisper_models,
        tuilionnx_models=tuilionnx_models,
    )


def ensure_runtime_directories(settings: AppSettings) -> RuntimePaths:
    runtime_paths = resolve_runtime_paths(settings)
    for path in (
        runtime_paths.data,
        runtime_paths.cache,
        runtime_paths.logs,
        runtime_paths.workspaces,
        runtime_paths.models,
        runtime_paths.cosyvoice_models,
        runtime_paths.voxcpm_models,
        runtime_paths.whisper_models,
        runtime_paths.tuilionnx_models,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return runtime_paths


def ensure_workspace(path: str | Path) -> Path:
    workspace = Path(path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def ensure_module_dir(workspace: str | Path, module_name: str) -> Path:
    module_dir = ensure_workspace(workspace) / module_name
    module_dir.mkdir(parents=True, exist_ok=True)
    return module_dir


def default_workspace_name(prefix: str = "job") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"


def sanitize_filename(value: str, fallback: str = "artifact", max_length: int = 80) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", compact)
    sanitized = sanitized.strip(" ._")
    if not sanitized:
        sanitized = fallback
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(" ._")
    return sanitized or fallback
