from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from typing import Any, Callable


DEFAULT_VIDEO_REQUIRED_FREE_MB = 7000
DEFAULT_VIDEO_SAFETY_MARGIN_MB = 1536

_OOM_KEYWORDS = (
    "cuda out of memory",
    "out of memory",
    "cublas_status_alloc_failed",
    "cuda error: out of memory",
    "memory allocation",
)


@dataclass(slots=True, frozen=True)
class CUDAMemorySnapshot:
    free_mb: int
    total_mb: int
    source: str = "nvidia-smi"


@dataclass(slots=True, frozen=True)
class VideoMemoryDecision:
    should_release: bool
    reason: str
    snapshot: CUDAMemorySnapshot | None = None


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def get_cuda_memory_snapshot(
    *,
    command_runner: CommandRunner | None = None,
    timeout_sec: float = 3.0,
) -> CUDAMemorySnapshot | None:
    runner = command_runner or subprocess.run
    command = [
        "nvidia-smi",
        "--query-gpu=memory.free,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_sec,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        return None

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None

    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        free_mb = _extract_int(parts[0])
        total_mb = _extract_int(parts[1])
        if free_mb is None or total_mb is None:
            continue
        return CUDAMemorySnapshot(free_mb=free_mb, total_mb=total_mb)
    return None


def evaluate_tts_release_for_video(
    *,
    required_free_mb: int = DEFAULT_VIDEO_REQUIRED_FREE_MB,
    safety_margin_mb: int = DEFAULT_VIDEO_SAFETY_MARGIN_MB,
    snapshot: CUDAMemorySnapshot | None = None,
    command_runner: CommandRunner | None = None,
) -> VideoMemoryDecision:
    current = snapshot or get_cuda_memory_snapshot(command_runner=command_runner)
    threshold_mb = max(required_free_mb, 0) + max(safety_margin_mb, 0)

    if current is None:
        return VideoMemoryDecision(
            should_release=True,
            reason="无法检测 NVIDIA 显存，保守释放音频模型后再渲染视频。",
            snapshot=None,
        )

    if current.total_mb < threshold_mb:
        return VideoMemoryDecision(
            should_release=True,
            reason=(
                f"GPU 总显存仅 {current.total_mb} MB，低于视频渲染建议阈值 {threshold_mb} MB，"
                "保守释放音频模型后再渲染视频。"
            ),
            snapshot=current,
        )

    if current.free_mb < threshold_mb:
        return VideoMemoryDecision(
            should_release=True,
            reason=(
                f"当前可用显存 {current.free_mb} MB，低于视频渲染建议阈值 {threshold_mb} MB，"
                "先释放音频模型再渲染视频。"
            ),
            snapshot=current,
        )

    return VideoMemoryDecision(
        should_release=False,
        reason=f"当前可用显存 {current.free_mb}/{current.total_mb} MB，可直接渲染视频。",
        snapshot=current,
    )


def should_release_tts_for_video(
    *,
    required_free_mb: int = DEFAULT_VIDEO_REQUIRED_FREE_MB,
    safety_margin_mb: int = DEFAULT_VIDEO_SAFETY_MARGIN_MB,
    snapshot: CUDAMemorySnapshot | None = None,
    command_runner: CommandRunner | None = None,
) -> bool:
    return evaluate_tts_release_for_video(
        required_free_mb=required_free_mb,
        safety_margin_mb=safety_margin_mb,
        snapshot=snapshot,
        command_runner=command_runner,
    ).should_release


def is_cuda_oom_error(exc_or_message: Any) -> bool:
    if exc_or_message is None:
        return False

    raw_text = str(exc_or_message).strip()
    if not raw_text:
        return False

    lower_text = raw_text.lower()
    if any(keyword in lower_text for keyword in _OOM_KEYWORDS):
        return True
    return "显存" in raw_text


def _extract_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None
