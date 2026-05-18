from __future__ import annotations

import subprocess

from santiszr.core.gpu_memory import (
    CUDAMemorySnapshot,
    evaluate_tts_release_for_video,
    get_cuda_memory_snapshot,
    is_cuda_oom_error,
)


def test_get_cuda_memory_snapshot_parses_nvidia_smi_output() -> None:
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[0] == "nvidia-smi"
        return subprocess.CompletedProcess(command, 0, "8192, 24576\n", "")

    snapshot = get_cuda_memory_snapshot(command_runner=fake_run)

    assert snapshot == CUDAMemorySnapshot(free_mb=8192, total_mb=24576, source="nvidia-smi")


def test_evaluate_tts_release_for_video_is_conservative_when_detection_fails() -> None:
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    decision = evaluate_tts_release_for_video(command_runner=fake_run)

    assert decision.should_release is True
    assert "无法检测" in decision.reason


def test_evaluate_tts_release_for_video_keeps_tts_when_free_memory_is_sufficient() -> None:
    decision = evaluate_tts_release_for_video(
        snapshot=CUDAMemorySnapshot(free_mb=12000, total_mb=24576),
    )

    assert decision.should_release is False
    assert "12000/24576" in decision.reason


def test_is_cuda_oom_error_matches_common_messages() -> None:
    assert is_cuda_oom_error("CUDA out of memory while allocating tensor") is True
    assert is_cuda_oom_error("CUBLAS_STATUS_ALLOC_FAILED in matmul") is True
    assert is_cuda_oom_error("显存不足，无法继续渲染") is True
    assert is_cuda_oom_error("reference video is missing") is False
