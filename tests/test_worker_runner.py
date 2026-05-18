from __future__ import annotations

import santiszr.workers.runner as runner_module
from santiszr.core.gpu_memory import VideoMemoryDecision
from santiszr.domain.schemas.audio import AudioMeta, TTSResult
from santiszr.domain.schemas.avatar import AvatarResult
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.workers.protocol import WorkerTaskKind, WorkerTaskRequest


class DummyReporter:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event, **kwargs):  # noqa: ANN001
        self.events.append({"event": event, **kwargs})


class FakeTTSService:
    def __init__(self) -> None:
        self.release_calls = 0
        self.requests = []

    def synthesize(self, request):  # noqa: ANN001
        self.requests.append(request)
        return TTSResult(
            success=True,
            audio_path="D:/tmp/audio.wav",
            meta=AudioMeta(duration_sec=1.0, sample_rate=22050, channels=1),
            provider="voxcpm2",
        )

    def release_resources(self) -> None:
        self.release_calls += 1


class FakeAvatarService:
    def __init__(self, results: list[AvatarResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def render(self, request):  # noqa: ANN001
        self.calls += 1
        return self.results.pop(0)


def test_run_tts_keeps_helper_loaded_after_success() -> None:
    reporter = DummyReporter()
    tts_service = FakeTTSService()
    task_request = WorkerTaskRequest(
        task_id="tts-1",
        task_kind=WorkerTaskKind.tts,
        payload={
            "text": "hello",
            "voice": "reference-clone",
            "reference_audio_path": "D:/tmp/ref.wav",
            "workspace": "D:/tmp/workspace",
        },
    )

    exit_code = runner_module._run_tts(task_request, reporter, {"tts": tts_service})

    assert exit_code == 0
    assert tts_service.release_calls == 0


def test_run_avatar_releases_tts_before_render_when_memory_is_tight(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        runner_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(True, "free memory is low"),
    )
    reporter = DummyReporter()
    tts_service = FakeTTSService()
    avatar_service = FakeAvatarService(
        [
            AvatarResult(
                success=True,
                video_path="D:/tmp/final.mp4",
                duration_sec=1.0,
                elapsed_sec=0.5,
            )
        ]
    )
    task_request = WorkerTaskRequest(
        task_id="avatar-1",
        task_kind=WorkerTaskKind.avatar,
        payload={
            "audio_path": "D:/tmp/audio.wav",
            "workspace": "D:/tmp/workspace",
            "reference_video_path": "D:/tmp/reference.mp4",
        },
    )

    exit_code = runner_module._run_avatar(
        task_request,
        reporter,
        {"tts": tts_service, "avatar": avatar_service},
    )

    assert exit_code == 0
    assert tts_service.release_calls == 1
    assert any(event.get("stage") == "gpu" for event in reporter.events)


def test_run_avatar_retries_once_after_cuda_oom(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        runner_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(False, "enough memory"),
    )
    reporter = DummyReporter()
    tts_service = FakeTTSService()
    avatar_service = FakeAvatarService(
        [
            AvatarResult(
                success=False,
                error=ErrorInfo(code="avatar_failed", message="CUDA out of memory during render"),
            ),
            AvatarResult(
                success=True,
                video_path="D:/tmp/final.mp4",
                duration_sec=1.0,
                elapsed_sec=0.5,
            ),
        ]
    )
    task_request = WorkerTaskRequest(
        task_id="avatar-2",
        task_kind=WorkerTaskKind.avatar,
        payload={
            "audio_path": "D:/tmp/audio.wav",
            "workspace": "D:/tmp/workspace",
            "reference_video_path": "D:/tmp/reference.mp4",
        },
    )

    exit_code = runner_module._run_avatar(
        task_request,
        reporter,
        {"tts": tts_service, "avatar": avatar_service},
    )

    assert exit_code == 0
    assert avatar_service.calls == 2
    assert tts_service.release_calls == 1
