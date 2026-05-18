from __future__ import annotations

import math
import subprocess
import struct
import wave
from pathlib import Path
import uuid

import pytest

from santiszr.infra.media.ffmpeg import FFmpegAdapter


def write_test_tone(path: Path, duration_sec: float = 0.6, sample_rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = int(duration_sec * sample_rate)
    frames = bytearray()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for frame_index in range(total_frames):
            sample = 0.2 * math.sin(2 * math.pi * 220 * frame_index / sample_rate)
            frames.extend(struct.pack("<h", int(sample * 32767)))
        handle.writeframes(frames)
    return path


@pytest.fixture()
def ffmpeg_adapter() -> FFmpegAdapter:
    adapter = FFmpegAdapter()
    if not adapter.available():
        pytest.skip("FFmpeg is required for media pipeline tests.")
    return adapter


@pytest.fixture(autouse=True)
def isolated_runtime_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = Path("D:/SantiSZR/.cache/pytest-runtime") / uuid.uuid4().hex
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SANTISZR_DATA_DIR", str(runtime_root / "data"))
    monkeypatch.setenv("SANTISZR_CACHE_DIR", str(runtime_root / "cache"))
    monkeypatch.setenv("SANTISZR_LOG_DIR", str(runtime_root / "logs"))


@pytest.fixture()
def temp_workspace() -> Path:
    workspace = Path("D:/SantiSZR/.cache/test-workspaces") / uuid.uuid4().hex
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture()
def sample_audio(temp_workspace: Path) -> Path:
    return write_test_tone(temp_workspace / "fixtures" / "tone.wav")


@pytest.fixture()
def sample_video(sample_audio: Path, temp_workspace: Path, ffmpeg_adapter: FFmpegAdapter) -> Path:
    output_path = temp_workspace / "fixtures" / "sample.mp4"
    command = [
        str(ffmpeg_adapter.ffmpeg_path),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x1b2230:s=160x90:r=25:d=1.0",
        "-i",
        str(sample_audio),
        "-shortest",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return output_path
