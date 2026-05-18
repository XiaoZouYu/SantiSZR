from __future__ import annotations

import json
import os
from pathlib import Path

from santiszr.vendor.voxcpm.model import VoxCPM2Model


_REQUIRED_MODEL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def resolve_local_voxcpm2_model_dir(model_dir: str | Path) -> Path:
    candidate = Path(model_dir).expanduser().resolve()
    candidates = [candidate]
    if candidate.name.lower() != "voxcpm2":
        candidates.append(candidate / "VoxCPM2")

    for path in candidates:
        if not path.exists() or not path.is_dir():
            continue
        if (path / "config.json").is_file():
            return path

    raise RuntimeError(f"VoxCPM2 model directory is missing: {candidate}")


def load_local_voxcpm2_model(
    model_dir: str | Path,
    *,
    device: str = "cuda",
    optimize: bool = True,
    load_denoiser: bool = False,
):
    if load_denoiser:
        raise RuntimeError("VoxCPM2 helper does not support loading an external denoiser runtime.")

    resolved_model_dir = resolve_local_voxcpm2_model_dir(model_dir)
    _validate_local_model_dir(resolved_model_dir)
    _enable_offline_transformers_mode()

    model = VoxCPM2Model.from_local(
        str(resolved_model_dir),
        optimize=optimize,
        device=device,
    )
    runtime_device = str(getattr(model, "device", "") or "").lower()
    if not runtime_device.startswith("cuda"):
        raise RuntimeError(f"VoxCPM2 loaded on unexpected device '{runtime_device or 'unknown'}'.")
    return model


def _validate_local_model_dir(model_dir: Path) -> None:
    missing = [name for name in _REQUIRED_MODEL_FILES if not (model_dir / name).is_file()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"VoxCPM2 model directory is incomplete: missing {joined}")

    has_main_checkpoint = any((model_dir / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))
    if not has_main_checkpoint:
        raise RuntimeError("VoxCPM2 main checkpoint is missing: expected model.safetensors or pytorch_model.bin")

    has_audio_vae = any((model_dir / name).is_file() for name in ("audiovae.safetensors", "audiovae.pth"))
    if not has_audio_vae:
        raise RuntimeError("VoxCPM2 AudioVAE checkpoint is missing: expected audiovae.safetensors or audiovae.pth")

    try:
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read VoxCPM2 config.json: {exc}") from exc

    architecture = str(config.get("architecture") or "").strip().lower()
    if architecture != "voxcpm2":
        raise RuntimeError(
            f"Configured model is not VoxCPM2. Expected architecture='voxcpm2', got '{architecture or 'missing'}'."
        )


def _enable_offline_transformers_mode() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

