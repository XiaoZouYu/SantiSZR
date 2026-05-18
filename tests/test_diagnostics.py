from __future__ import annotations

from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.diagnostics import format_diagnostic_report, run_startup_diagnostics


def _build_settings(root: Path) -> AppSettings:
    model_root = root / "models"
    return AppSettings(
        models={
            "root_dir": model_root,
            "voxcpm_model_dir": model_root / "voxcpm" / "VoxCPM2",
            "whisper_model_dir": model_root / "whisper",
            "tuilionnx_model_dir": model_root / "tuilionnx",
        },
        avatar={
            "tuilionnx_root": model_root / "tuilionnx",
            "tuilionnx_python": root / "tools" / "tuilionnx_python" / "python.exe",
        },
    )


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_diagnostics_reports_complete_voxcpm_runtime(tmp_path: Path, monkeypatch) -> None:
    settings = _build_settings(tmp_path)
    voxcpm_dir = settings.models.voxcpm_model_dir
    assert voxcpm_dir is not None
    for name in (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "model.safetensors",
        "audiovae.pth",
    ):
        _touch(voxcpm_dir / name)

    whisper_dir = settings.models.whisper_model_dir
    tuilionnx_dir = settings.models.tuilionnx_model_dir
    assert whisper_dir is not None
    assert tuilionnx_dir is not None
    whisper_dir.mkdir(parents=True, exist_ok=True)
    tuilionnx_dir.mkdir(parents=True, exist_ok=True)

    _touch(tmp_path / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    _touch(tmp_path / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    _touch(tmp_path / "tools" / "voxcpm_python" / "python.exe")
    _touch(tmp_path / "tools" / "tuilionnx_python" / "python.exe")

    monkeypatch.delenv("SANTISZR_VOXCPM_PYTHON", raising=False)
    monkeypatch.delenv("SANTISZR_TUILIONNX_PYTHON", raising=False)

    checks = run_startup_diagnostics(settings, project_root=tmp_path)
    by_name = {item.name: item for item in checks}

    assert by_name["FFmpeg"].status == "ok"
    assert by_name["FFprobe"].status == "ok"
    assert by_name["VoxCPM2 Model"].status == "ok"
    assert by_name["VoxCPM Python"].status == "ok"
    assert by_name["Whisper Model"].status == "ok"
    assert by_name["TuiliONNX Model"].status == "ok"
    assert by_name["TuiliONNX Python"].status == "ok"
    assert by_name["Publisher"].status == "not_configured"

    report = format_diagnostic_report(checks)
    assert "✅ VoxCPM2 模型完整" in report
    assert "⚠ 发布功能未内置" in report


def test_diagnostics_reports_incomplete_voxcpm_model(tmp_path: Path, monkeypatch) -> None:
    settings = _build_settings(tmp_path)
    voxcpm_dir = settings.models.voxcpm_model_dir
    assert voxcpm_dir is not None
    _touch(voxcpm_dir / "config.json")
    _touch(voxcpm_dir / "tokenizer.json")

    custom_voxcpm_python = tmp_path / "custom" / "voxcpm_python.exe"
    monkeypatch.setenv("SANTISZR_VOXCPM_PYTHON", str(custom_voxcpm_python))
    monkeypatch.setenv("SANTISZR_TUILIONNX_PYTHON", str(tmp_path / "custom" / "tuilionnx_python.exe"))

    checks = run_startup_diagnostics(settings, project_root=tmp_path)
    by_name = {item.name: item for item in checks}

    assert by_name["VoxCPM2 Model"].status == "error"
    assert "缺少" in (by_name["VoxCPM2 Model"].detail or "")
    assert by_name["VoxCPM Python"].status == "error"
    assert by_name["TuiliONNX Python"].status == "warning"
    assert by_name["Publisher"].status == "not_configured"
