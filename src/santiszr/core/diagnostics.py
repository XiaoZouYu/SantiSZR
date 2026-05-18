from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from santiszr.config.settings import AppSettings


DiagnosticStatus = Literal["ok", "warning", "error", "not_configured"]


@dataclass(slots=True, frozen=True)
class DiagnosticCheck:
    name: str
    status: DiagnosticStatus
    message: str
    detail: str | None = None


def run_startup_diagnostics(
    settings: AppSettings,
    *,
    project_root: Path | None = None,
) -> list[DiagnosticCheck]:
    root = (project_root or _project_root()).expanduser().resolve()
    checks = [
        _check_binary(
            "FFmpeg",
            configured_path=settings.media.ffmpeg_path,
            bundled_path=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            command_name="ffmpeg",
        ),
        _check_binary(
            "FFprobe",
            configured_path=settings.media.ffprobe_path,
            bundled_path=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
            command_name="ffprobe",
        ),
        _check_voxcpm_model(settings),
        _check_voxcpm_python(root),
        _check_whisper_model(settings),
        _check_tuilionnx_model(settings),
        _check_tuilionnx_python(settings, root),
        DiagnosticCheck(
            name="Publisher",
            status="not_configured",
            message="发布功能未内置：当前只能生成素材，不能自动发布",
            detail=str(root / "src" / "santiszr" / "infra" / "publisher"),
        ),
    ]
    return checks


def format_diagnostic_report(checks: list[DiagnosticCheck]) -> str:
    return "\n".join(_format_check(check) for check in checks)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _format_check(check: DiagnosticCheck) -> str:
    prefix = {
        "ok": "✅",
        "warning": "⚠",
        "error": "❌",
        "not_configured": "⚠",
    }[check.status]
    line = f"{prefix} {check.message}"
    if check.detail:
        line = f"{line}：{check.detail}"
    return line


def _check_binary(
    name: str,
    *,
    configured_path: Path | None,
    bundled_path: Path,
    command_name: str,
) -> DiagnosticCheck:
    if configured_path:
        configured = configured_path.expanduser()
        if configured.exists():
            return DiagnosticCheck(name=name, status="ok", message=f"{name} 可用", detail=str(configured))
        return DiagnosticCheck(
            name=name,
            status="error",
            message=f"{name} 不存在",
            detail=str(configured),
        )

    if bundled_path.exists():
        return DiagnosticCheck(name=name, status="ok", message=f"{name} 可用", detail=str(bundled_path))

    resolved = shutil.which(command_name)
    if resolved:
        return DiagnosticCheck(name=name, status="ok", message=f"{name} 可用", detail=resolved)

    return DiagnosticCheck(
        name=name,
        status="error",
        message=f"{name} 不可用",
        detail="未配置路径，且 PATH 中未找到可执行文件",
    )


def _check_voxcpm_model(settings: AppSettings) -> DiagnosticCheck:
    model_dir = _resolve_dir(settings.models.voxcpm_model_dir)
    if model_dir is None:
        return DiagnosticCheck(
            name="VoxCPM2 Model",
            status="error",
            message="VoxCPM2 模型目录未配置",
        )
    if not model_dir.exists():
        return DiagnosticCheck(
            name="VoxCPM2 Model",
            status="error",
            message="VoxCPM2 模型目录不存在",
            detail=str(model_dir),
        )

    required_files = [
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ]
    missing = [name for name in required_files if not (model_dir / name).exists()]
    if not ((model_dir / "model.safetensors").exists() or (model_dir / "pytorch_model.bin").exists()):
        missing.append("model.safetensors | pytorch_model.bin")
    if not ((model_dir / "audiovae.pth").exists() or (model_dir / "audiovae.safetensors").exists()):
        missing.append("audiovae.pth | audiovae.safetensors")

    if missing:
        return DiagnosticCheck(
            name="VoxCPM2 Model",
            status="error",
            message="VoxCPM2 模型不完整",
            detail=f"{model_dir} | 缺少: {', '.join(missing)}",
        )

    return DiagnosticCheck(
        name="VoxCPM2 Model",
        status="ok",
        message="VoxCPM2 模型完整",
        detail=str(model_dir),
    )


def _check_voxcpm_python(project_root: Path) -> DiagnosticCheck:
    raw_env = str(os.getenv("SANTISZR_VOXCPM_PYTHON") or "").strip()
    env_path = Path(raw_env).expanduser() if raw_env else None
    bundled_path = project_root / "tools" / "voxcpm_python" / "python.exe"

    if env_path is not None:
        if env_path.exists():
            return DiagnosticCheck(
                name="VoxCPM Python",
                status="ok",
                message="VoxCPM Python 可用",
                detail=str(env_path),
            )
        return DiagnosticCheck(
            name="VoxCPM Python",
            status="error",
            message="VoxCPM Python 不存在",
            detail=str(env_path),
        )

    if bundled_path.exists():
        return DiagnosticCheck(
            name="VoxCPM Python",
            status="ok",
            message="VoxCPM Python 可用",
            detail=str(bundled_path),
        )

    return DiagnosticCheck(
        name="VoxCPM Python",
        status="error",
        message="VoxCPM Python 不存在",
        detail=str(bundled_path),
    )


def _check_whisper_model(settings: AppSettings) -> DiagnosticCheck:
    model_dir = _resolve_dir(settings.models.whisper_model_dir)
    if model_dir is None:
        return DiagnosticCheck(
            name="Whisper Model",
            status="warning",
            message="Whisper 模型目录未配置",
        )
    if not model_dir.exists():
        return DiagnosticCheck(
            name="Whisper Model",
            status="warning",
            message="Whisper 模型目录不存在",
            detail=str(model_dir),
        )
    return DiagnosticCheck(
        name="Whisper Model",
        status="ok",
        message="Whisper 模型目录可用",
        detail=str(model_dir),
    )


def _check_tuilionnx_model(settings: AppSettings) -> DiagnosticCheck:
    model_dir = _resolve_dir(settings.avatar.tuilionnx_root or settings.models.tuilionnx_model_dir)
    if model_dir is None:
        return DiagnosticCheck(
            name="TuiliONNX Model",
            status="warning",
            message="TuiliONNX 模型目录未配置",
        )
    if not model_dir.exists():
        return DiagnosticCheck(
            name="TuiliONNX Model",
            status="warning",
            message="TuiliONNX 模型目录不存在",
            detail=str(model_dir),
        )
    return DiagnosticCheck(
        name="TuiliONNX Model",
        status="ok",
        message="TuiliONNX 模型目录可用",
        detail=str(model_dir),
    )


def _check_tuilionnx_python(settings: AppSettings, project_root: Path) -> DiagnosticCheck:
    raw_env = str(os.getenv("SANTISZR_TUILIONNX_PYTHON") or "").strip()
    configured = settings.avatar.tuilionnx_python
    candidate = None
    if raw_env:
        candidate = Path(raw_env).expanduser()
    elif configured is not None:
        candidate = configured.expanduser()
    else:
        candidate = _first_existing_path(
            project_root / "tools" / "tuilionnx_python" / "python.exe",
            project_root / "tools" / "cosyvoice_python" / "python.exe",
        ) or project_root / "tools" / "tuilionnx_python" / "python.exe"

    if candidate.exists():
        return DiagnosticCheck(
            name="TuiliONNX Python",
            status="ok",
            message="TuiliONNX Python 可用",
            detail=str(candidate),
        )

    return DiagnosticCheck(
        name="TuiliONNX Python",
        status="warning",
        message="TuiliONNX Python 不存在",
        detail=str(candidate),
    )


def _resolve_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve()


def _first_existing_path(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None
