from __future__ import annotations

import santiszr.domain.services.workflow_service as workflow_module
from santiszr.core.gpu_memory import VideoMemoryDecision
from santiszr.domain.schemas.audio import AudioMeta, RewriteMode, RewriteResult, TTSResult
from santiszr.domain.schemas.avatar import AvatarResult
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.content import ContentResult, ExtractedCopy, VideoSource
from santiszr.domain.schemas.postprocess import BGMSelection, CoverRequest, PostProcessResult
from santiszr.domain.schemas.publish import (
    GenerateVideoWorkflowRequest,
    PublishBatchResult,
    PublishPlatform,
    PublishResult,
)
from santiszr.domain.schemas.subtitle import SubtitleResult, SubtitleStyle
from santiszr.domain.services.workflow_service import WorkflowService


class FakeContentService:
    def extract(self, request):  # noqa: ANN001
        return ContentResult(
            success=True,
            platform="local",
            workspace=request.workspace,
            video_path="D:/tmp/source.mp4",
            audio_path="D:/tmp/source.wav",
            extracted_copy=ExtractedCopy(raw_text="raw", cleaned_text="cleaned"),
        )


class FakeRewriteService:
    def rewrite(self, request):  # noqa: ANN001
        return RewriteResult(
            success=True,
            rewritten_text="rewritten copy",
            title="rewritten title",
            tags=["#demo"],
            provider="test",
        )


class FakeTTSService:
    def __init__(self) -> None:
        self.release_calls = 0
        self.requests = []

    def synthesize(self, request):  # noqa: ANN001
        self.requests.append(request)
        return TTSResult(
            success=True,
            audio_path="D:/tmp/narration.wav",
            meta=AudioMeta(duration_sec=1.0, sample_rate=22050, channels=1),
            provider="test",
        )

    def release_resources(self) -> None:
        self.release_calls += 1


class FakeSubtitleService:
    def generate(self, request):  # noqa: ANN001
        return SubtitleResult(
            success=True,
            srt_path="D:/tmp/narration.srt",
            burned_video_path=None,
            subtitle_text="1\n00:00:00,000 --> 00:00:01,000\nhello",
        )


class FakeAvatarService:
    def __init__(self) -> None:
        self.requests = []

    def render(self, request):  # noqa: ANN001
        self.requests.append(request)
        return AvatarResult(
            success=True,
            video_path="D:/tmp/final.mp4",
            duration_sec=2.0,
            elapsed_sec=0.5,
            engine_used="test",
        )


class OOMThenSuccessAvatarService:
    def __init__(self) -> None:
        self.requests = []
        self.calls = 0

    def render(self, request):  # noqa: ANN001
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            return AvatarResult(
                success=False,
                error=ErrorInfo(code="avatar_failed", message="CUDA out of memory while rendering avatar"),
            )
        return AvatarResult(
            success=True,
            video_path="D:/tmp/final.mp4",
            duration_sec=2.0,
            elapsed_sec=0.5,
            engine_used="test",
        )


class FakePostProcessService:
    def process(self, request):  # noqa: ANN001
        return PostProcessResult(
            success=True,
            final_video_path="D:/tmp/final_post.mp4",
            subtitle_video_path="D:/tmp/final_subtitle.mp4",
            cover_image_path="D:/tmp/final_cover.jpg",
            steps_applied=["subtitle", "bgm", "cover"],
        )


class FakePublishService:
    def publish_batch(self, request):  # noqa: ANN001
        return PublishBatchResult(
            success=True,
            results=[
                PublishResult(success=True, platform=PublishPlatform.douyin, status="published"),
                PublishResult(success=True, platform=PublishPlatform.xiaohongshu, status="published"),
            ],
            summary="douyin: success; xiaohongshu: success",
        )


def test_workflow_service_reports_stage_progress(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        workflow_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(False, "enough memory"),
    )
    events: list[tuple[str, float, str]] = []
    tts_service = FakeTTSService()
    avatar_service = FakeAvatarService()
    service = WorkflowService(
        content_service=FakeContentService(),
        rewrite_service=FakeRewriteService(),
        tts_service=tts_service,
        subtitle_service=FakeSubtitleService(),
        avatar_service=avatar_service,
    )

    result = service.generate_video(
        GenerateVideoWorkflowRequest(
            source=VideoSource(source_type="raw_text", raw_input="hello"),
            rewrite_mode=RewriteMode.custom,
            rewrite_prompt="demo",
            voice="neutral",
            avatar_model_id="uploaded-avatar",
            reference_video_path="D:/tmp/reference.mp4",
            workspace="D:/tmp/workflow-test",
            subtitle_burn_in=False,
            subtitle_style=SubtitleStyle(font_size=30, bottom_margin=88),
        ),
        progress_callback=lambda stage, progress, message: events.append((stage, progress, message)),
        task_id="workflow-123",
    )

    assert result.success is True
    assert result.context.task_id == "workflow-123"
    assert [stage for stage, _, _ in events if stage in {"content", "rewrite", "tts", "subtitle", "avatar"}] == [
        "content",
        "content",
        "rewrite",
        "rewrite",
        "tts",
        "tts",
        "avatar",
        "avatar",
        "subtitle",
        "subtitle",
    ]
    assert result.final_video_path == "D:/tmp/final.mp4"
    assert result.artifacts.postprocess is None
    assert result.artifacts.publish is None
    assert avatar_service.requests[0].reference_video_path == "D:/tmp/reference.mp4"
    assert avatar_service.requests[0].subtitle_path is None
    assert avatar_service.requests[0].subtitle_style.font_size == 30
    assert avatar_service.requests[0].subtitle_style.bottom_margin == 88
    assert tts_service.requests[0].ultimate_clone is False
    assert tts_service.requests[0].prompt_text is None
    assert tts_service.release_calls == 0


def test_workflow_service_passes_ultimate_clone_to_tts(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        workflow_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(False, "enough memory"),
    )
    tts_service = FakeTTSService()
    service = WorkflowService(
        content_service=FakeContentService(),
        rewrite_service=FakeRewriteService(),
        tts_service=tts_service,
        subtitle_service=FakeSubtitleService(),
        avatar_service=FakeAvatarService(),
    )

    result = service.generate_video(
        GenerateVideoWorkflowRequest(
            source=VideoSource(source_type="raw_text", raw_input="hello"),
            rewrite_mode=RewriteMode.custom,
            voice="reference-clone",
            reference_audio_path="D:/tmp/reference.wav",
            ultimate_clone=True,
            prompt_text="recognized reference transcript",
            avatar_model_id="uploaded-avatar",
            reference_video_path="D:/tmp/reference.mp4",
            workspace="D:/tmp/workflow-test",
            subtitle_burn_in=False,
        )
    )

    assert result.success is True
    assert result.artifacts.tts is not None
    assert tts_service.requests[0].reference_audio_path == "D:/tmp/reference.wav"
    assert tts_service.requests[0].ultimate_clone is True
    assert tts_service.requests[0].prompt_text == "recognized reference transcript"


def test_workflow_service_releases_tts_before_avatar_when_memory_is_tight(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        workflow_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(True, "free memory is low"),
    )
    tts_service = FakeTTSService()
    service = WorkflowService(
        content_service=FakeContentService(),
        rewrite_service=FakeRewriteService(),
        tts_service=tts_service,
        subtitle_service=FakeSubtitleService(),
        avatar_service=FakeAvatarService(),
    )

    result = service.generate_video(
        GenerateVideoWorkflowRequest(
            source=VideoSource(source_type="raw_text", raw_input="hello"),
            rewrite_mode=RewriteMode.custom,
            voice="reference-clone",
            avatar_model_id="uploaded-avatar",
            reference_video_path="D:/tmp/reference.mp4",
            workspace="D:/tmp/workflow-test",
            subtitle_burn_in=False,
        )
    )

    assert result.success is True
    assert tts_service.release_calls == 1


def test_workflow_service_retries_avatar_once_after_cuda_oom(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        workflow_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(False, "enough memory"),
    )
    tts_service = FakeTTSService()
    avatar_service = OOMThenSuccessAvatarService()
    service = WorkflowService(
        content_service=FakeContentService(),
        rewrite_service=FakeRewriteService(),
        tts_service=tts_service,
        subtitle_service=FakeSubtitleService(),
        avatar_service=avatar_service,
    )

    result = service.generate_video(
        GenerateVideoWorkflowRequest(
            source=VideoSource(source_type="raw_text", raw_input="hello"),
            rewrite_mode=RewriteMode.custom,
            voice="reference-clone",
            avatar_model_id="uploaded-avatar",
            reference_video_path="D:/tmp/reference.mp4",
            workspace="D:/tmp/workflow-test",
            subtitle_burn_in=False,
        )
    )

    assert result.success is True
    assert avatar_service.calls == 2
    assert tts_service.release_calls == 1


def test_workflow_service_can_run_postprocess_and_publish_stages(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        workflow_module,
        "evaluate_tts_release_for_video",
        lambda: VideoMemoryDecision(False, "enough memory"),
    )
    events: list[tuple[str, float, str]] = []
    tts_service = FakeTTSService()
    avatar_service = FakeAvatarService()
    service = WorkflowService(
        content_service=FakeContentService(),
        rewrite_service=FakeRewriteService(),
        tts_service=tts_service,
        subtitle_service=FakeSubtitleService(),
        avatar_service=avatar_service,
        postprocess_service=FakePostProcessService(),
        publish_service=FakePublishService(),
    )

    result = service.generate_video(
        GenerateVideoWorkflowRequest(
            source=VideoSource(source_type="raw_text", raw_input="hello"),
            rewrite_mode=RewriteMode.custom,
            rewrite_prompt="demo",
            voice="neutral",
            avatar_model_id="uploaded-avatar",
            reference_video_path="D:/tmp/reference.mp4",
            workspace="D:/tmp/workflow-test",
            enable_postprocess=True,
            subtitle_burn_in=True,
            subtitle_style=SubtitleStyle(font_size=26, bottom_margin=92),
            bgm=BGMSelection(bgm_directory="D:/tmp/bgm", random_choice=True),
            cover=CoverRequest(enabled=True),
            publish_platforms=[PublishPlatform.douyin, PublishPlatform.xiaohongshu],
        ),
        progress_callback=lambda stage, progress, message: events.append((stage, progress, message)),
        task_id="workflow-456",
    )

    assert result.success is True
    assert result.context.task_id == "workflow-456"
    assert result.final_video_path == "D:/tmp/final_post.mp4"
    assert result.final_cover_path == "D:/tmp/final_cover.jpg"
    assert result.artifacts.postprocess is not None
    assert result.artifacts.publish is not None
    assert [stage for stage, _, _ in events if stage in {"postprocess", "publish"}] == [
        "postprocess",
        "postprocess",
        "publish",
        "publish",
    ]
    assert avatar_service.requests[0].reference_video_path == "D:/tmp/reference.mp4"
    assert avatar_service.requests[0].subtitle_style.font_size == 26
    assert avatar_service.requests[0].subtitle_style.bottom_margin == 92
    assert tts_service.release_calls == 0
