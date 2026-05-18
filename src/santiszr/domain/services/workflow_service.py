from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from santiszr.core.gpu_memory import evaluate_tts_release_for_video, is_cuda_oom_error
from santiszr.domain.schemas.audio import RewriteRequest, TTSRequest
from santiszr.domain.schemas.avatar import AvatarRequest
from santiszr.domain.schemas.common import ErrorInfo, TaskContext, TaskStatus
from santiszr.domain.schemas.content import ContentRequest
from santiszr.domain.schemas.postprocess import PostProcessRequest
from santiszr.domain.schemas.publish import (
    GenerateVideoWorkflowRequest,
    GenerateVideoWorkflowResult,
    PublishBatchRequest,
    WorkflowArtifacts,
)
from santiszr.domain.schemas.subtitle import SubtitleRequest
from santiszr.domain.services.avatar_service import AvatarService
from santiszr.domain.services.content_service import ContentService
from santiszr.domain.services.postprocess_service import PostProcessService
from santiszr.domain.services.publish_service import PublishService
from santiszr.domain.services.rewrite_service import RewriteService
from santiszr.domain.services.subtitle_service import SubtitleService
from santiszr.domain.services.tts_service import TTSService


ProgressCallback = Callable[[str, float, str], None]


class WorkflowService:
    def __init__(
        self,
        content_service: ContentService | None = None,
        rewrite_service: RewriteService | None = None,
        tts_service: TTSService | None = None,
        subtitle_service: SubtitleService | None = None,
        avatar_service: AvatarService | None = None,
        postprocess_service: PostProcessService | None = None,
        publish_service: PublishService | None = None,
    ) -> None:
        self.content_service = content_service or ContentService()
        self.rewrite_service = rewrite_service or RewriteService()
        self.tts_service = tts_service or TTSService()
        self.subtitle_service = subtitle_service or SubtitleService()
        self.avatar_service = avatar_service or AvatarService()
        self.postprocess_service = postprocess_service or PostProcessService()
        self.publish_service = publish_service or PublishService()

    def generate_video(
        self,
        request: GenerateVideoWorkflowRequest,
        *,
        progress_callback: ProgressCallback | None = None,
        task_id: str | None = None,
    ) -> GenerateVideoWorkflowResult:
        context = TaskContext(
            task_id=task_id or f"workflow-{datetime.now(UTC).timestamp()}",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            status=TaskStatus.running,
            progress=0.0,
        )
        artifacts = WorkflowArtifacts()
        workspace = Path(request.workspace).expanduser().resolve()
        final_video_path: str | None = None
        final_cover_path: str | None = None

        try:
            self._report(progress_callback, context, "content", 0.05, "Starting content extraction.")
            content_result = self.content_service.extract(
                ContentRequest(
                    source=request.source,
                    workspace=str(workspace),
                    download_video=True,
                    extract_audio=True,
                )
            )
            artifacts.content = content_result
            self._ensure_success(content_result.success, content_result.error, "Content extraction failed.")
            self._report(progress_callback, context, "content", 0.18, "Content extraction completed.")

            rewrite_input = (
                content_result.extracted_copy.cleaned_text
                if content_result.extracted_copy
                else request.source.raw_input
            )
            self._report(progress_callback, context, "rewrite", 0.28, "Starting copy rewrite.")
            rewrite_result = self.rewrite_service.rewrite(
                RewriteRequest(
                    text=rewrite_input,
                    mode=request.rewrite_mode,
                    prompt=request.rewrite_prompt,
                    model=request.rewrite_model,
                    workspace=str(workspace),
                )
            )
            artifacts.rewrite = rewrite_result
            self._ensure_success(
                rewrite_result.success and bool(rewrite_result.rewritten_text),
                rewrite_result.error,
                "Rewrite stage failed.",
            )
            self._report(progress_callback, context, "rewrite", 0.4, "Rewrite completed.")

            self._report(progress_callback, context, "tts", 0.48, "Starting TTS.")
            tts_result = self.tts_service.synthesize(
                TTSRequest(
                    text=rewrite_result.rewritten_text or "",
                    voice=request.voice,
                    reference_audio_path=request.reference_audio_path,
                    ultimate_clone=request.ultimate_clone,
                    prompt_text=request.prompt_text,
                    speed=request.voice_speed,
                    workspace=str(workspace),
                    output_name="narration",
                )
            )
            artifacts.tts = tts_result
            self._ensure_success(
                tts_result.success and bool(tts_result.audio_path),
                tts_result.error,
                "TTS stage failed.",
            )
            self._report(progress_callback, context, "tts", 0.58, "TTS completed.")

            reference_video_path = (request.reference_video_path or "").strip()
            if not reference_video_path:
                raise RuntimeError("Avatar stage requires an uploaded reference video.")

            self._release_tts_resources_for_video_if_needed(progress_callback, context)
            self._report(progress_callback, context, "avatar", 0.82, "Starting avatar rendering.")
            avatar_request = AvatarRequest(
                audio_path=tts_result.audio_path or "",
                model_id=request.avatar_model_id,
                engine=request.avatar_engine,
                workspace=str(workspace),
                subtitle_path=None,
                subtitle_style=request.subtitle_style,
                reference_video_path=reference_video_path,
                overlay_text=rewrite_result.title,
            )
            avatar_result = self._render_avatar_with_retry(
                avatar_request,
                progress_callback=progress_callback,
                context=context,
            )
            artifacts.avatar = avatar_result
            self._ensure_success(
                avatar_result.success and bool(avatar_result.video_path),
                avatar_result.error,
                "Avatar stage failed.",
            )
            final_video_path = avatar_result.video_path
            self._report(progress_callback, context, "avatar", 0.88, "Avatar rendering completed.")

            self._report(progress_callback, context, "subtitle", 0.91, "Starting subtitle generation.")
            subtitle_result = self.subtitle_service.generate(
                SubtitleRequest(
                    audio_path=tts_result.audio_path or "",
                    reference_text=rewrite_result.rewritten_text,
                    burn_in=False,
                    workspace=str(workspace),
                    output_name="narration",
                )
            )
            artifacts.subtitle = subtitle_result
            self._ensure_success(
                subtitle_result.success and bool(subtitle_result.srt_path),
                subtitle_result.error,
                "Subtitle stage failed.",
            )
            self._report(progress_callback, context, "subtitle", 0.94, "Subtitle generation completed.")

            if self._needs_postprocess(request):
                self._report(progress_callback, context, "postprocess", 0.96, "Starting postprocess.")
                postprocess_result = self.postprocess_service.process(
                    self._build_postprocess_request(
                        request=request,
                        workspace=workspace,
                        rewrite_title=rewrite_result.title,
                        rewrite_tags=rewrite_result.tags,
                        avatar_video_path=avatar_result.video_path or "",
                        subtitle_path=subtitle_result.srt_path,
                    )
                )
                artifacts.postprocess = postprocess_result
                self._ensure_success(
                    postprocess_result.success and bool(postprocess_result.final_video_path),
                    postprocess_result.error,
                    "Postprocess stage failed.",
                )
                final_video_path = postprocess_result.final_video_path
                final_cover_path = postprocess_result.cover_image_path
                self._report(progress_callback, context, "postprocess", 0.975, "Postprocess completed.")

            if request.publish_platforms:
                self._report(progress_callback, context, "publish", 0.985, "Starting publishing.")
                publish_result = self.publish_service.publish_batch(
                    self._build_publish_request(
                        request=request,
                        rewrite_title=rewrite_result.title,
                        rewrite_tags=rewrite_result.tags,
                        final_video_path=final_video_path or avatar_result.video_path or "",
                        final_cover_path=final_cover_path,
                    )
                )
                artifacts.publish = publish_result
                self._ensure_success(
                    publish_result.success,
                    publish_result.error,
                    "Publish stage failed.",
                )
                self._report(progress_callback, context, "publish", 0.995, publish_result.summary or "Publishing completed.")

            summary = f"Workflow completed. Final video: {final_video_path or avatar_result.video_path}"
            self._report(progress_callback, context, "workflow", 1.0, summary)
            context.status = TaskStatus.succeeded
            context.progress = 1.0
            context.updated_at = datetime.now(UTC)
            return GenerateVideoWorkflowResult(
                success=True,
                context=context,
                artifacts=artifacts,
                final_video_path=final_video_path or avatar_result.video_path,
                final_cover_path=final_cover_path,
                summary=summary,
            )
        except Exception as exc:
            context.status = TaskStatus.failed
            context.updated_at = datetime.now(UTC)
            failure_message = str(exc)
            self._report(progress_callback, context, "workflow", context.progress, failure_message)
            return GenerateVideoWorkflowResult(
                success=False,
                context=context,
                artifacts=artifacts,
                final_video_path=(
                    artifacts.postprocess.final_video_path
                    if artifacts.postprocess and artifacts.postprocess.final_video_path
                    else (artifacts.avatar.video_path if artifacts.avatar else None)
                ),
                final_cover_path=artifacts.postprocess.cover_image_path if artifacts.postprocess else None,
                summary=None,
                error=ErrorInfo(code="workflow_failed", message=failure_message),
            )

    def _report(
        self,
        progress_callback: ProgressCallback | None,
        context: TaskContext,
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        context.progress = progress
        context.updated_at = datetime.now(UTC)
        if progress_callback:
            progress_callback(stage, progress, message)

    def _ensure_success(self, success: bool, error: ErrorInfo | None, fallback_message: str) -> None:
        if success:
            return
        raise RuntimeError(error.message if error else fallback_message)

    def _release_tts_resources(self) -> None:
        release = getattr(self.tts_service, "release_resources", None)
        if callable(release):
            release()

    def _release_tts_resources_for_video_if_needed(
        self,
        progress_callback: ProgressCallback | None,
        context: TaskContext,
    ) -> bool:
        decision = evaluate_tts_release_for_video()
        if not decision.should_release:
            return False
        self._report(progress_callback, context, "gpu", max(context.progress, 0.8), decision.reason)
        return self._try_release_tts_resources(progress_callback, context)

    def _try_release_tts_resources(
        self,
        progress_callback: ProgressCallback | None,
        context: TaskContext,
        *,
        failure_prefix: str = "释放音频模型失败。",
    ) -> bool:
        try:
            self._release_tts_resources()
        except Exception as exc:
            self._report(
                progress_callback,
                context,
                "gpu",
                context.progress,
                f"{failure_prefix} {exc}",
            )
            return False
        return True

    def _render_avatar_with_retry(
        self,
        request: AvatarRequest,
        *,
        progress_callback: ProgressCallback | None,
        context: TaskContext,
    ) -> AvatarResult:
        result = self.avatar_service.render(request)
        if result.success or not self._avatar_result_is_cuda_oom(result):
            return result

        released = self._try_release_tts_resources(
            progress_callback,
            context,
            failure_prefix="视频渲染显存不足，但释放音频模型失败。",
        )
        self._report(
            progress_callback,
            context,
            "gpu",
            max(context.progress, 0.84),
            (
                "视频渲染显存不足，已释放音频模型并重试一次。"
                if released
                else "视频渲染显存不足，释放音频模型失败，仍重试一次。"
            ),
        )
        return self.avatar_service.render(request)

    def _avatar_result_is_cuda_oom(self, result: AvatarResult) -> bool:
        return is_cuda_oom_error(result.error.message if result.error else None)

    def _needs_postprocess(self, request: GenerateVideoWorkflowRequest) -> bool:
        return request.enable_postprocess or request.subtitle_burn_in or request.bgm is not None or request.cover.enabled

    def _build_postprocess_request(
        self,
        *,
        request: GenerateVideoWorkflowRequest,
        workspace: Path,
        rewrite_title: str | None,
        rewrite_tags: list[str],
        avatar_video_path: str,
        subtitle_path: str | None,
    ) -> PostProcessRequest:
        cover = request.cover.model_copy(deep=True)
        if cover.enabled:
            if not cover.title:
                cover.title = rewrite_title or ""
            if not cover.highlight_text and rewrite_tags:
                cover.highlight_text = rewrite_tags[0].lstrip("#")
        return PostProcessRequest(
            video_path=avatar_video_path,
            subtitle_path=subtitle_path if request.subtitle_burn_in else None,
            subtitle_style=request.subtitle_style,
            burn_subtitles=request.subtitle_burn_in,
            bgm=request.bgm.model_copy(deep=True) if request.bgm else None,
            cover=cover,
            workspace=str(workspace),
            output_name="workflow-final",
        )

    def _build_publish_request(
        self,
        *,
        request: GenerateVideoWorkflowRequest,
        rewrite_title: str | None,
        rewrite_tags: list[str],
        final_video_path: str,
        final_cover_path: str | None,
    ) -> PublishBatchRequest:
        return PublishBatchRequest(
            platforms=request.publish_platforms,
            video_path=final_video_path,
            title=request.publish_title or rewrite_title or Path(final_video_path).stem,
            tags=request.publish_tags or rewrite_tags,
            cover_path=final_cover_path,
            scheduled_at=request.publish_scheduled_at,
            workspace=request.workspace,
            account_file=request.publish_account_file,
            category=request.publish_category,
            description=None,
            continue_on_error=request.publish_continue_on_error,
        )
