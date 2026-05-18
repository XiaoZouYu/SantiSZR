from __future__ import annotations

import sys
from typing import Any

from santiszr.config.settings import load_settings
from santiszr.core.llm_config import load_persisted_llm_settings
from santiszr.core.gpu_memory import evaluate_tts_release_for_video, is_cuda_oom_error
from santiszr.domain.schemas.audio import RewriteRequest, TTSRequest
from santiszr.domain.schemas.avatar import AvatarRequest
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.content import ContentRequest, VideoSource
from santiszr.domain.schemas.publish import GenerateVideoWorkflowRequest
from santiszr.domain.schemas.subtitle import SubtitleRequest
from santiszr.domain.services.avatar_service import AvatarService
from santiszr.domain.services.content_service import ContentService
from santiszr.domain.services.rewrite_service import RewriteService
from santiszr.domain.services.subtitle_service import SubtitleService
from santiszr.domain.services.tts_service import TTSService
from santiszr.domain.services.workflow_service import WorkflowService
from santiszr.infra.llm.client import LLMClient
from santiszr.infra.subtitle.corrector import SubtitleCorrector
from santiszr.workers.protocol import (
    WorkerEvent,
    WorkerEventType,
    WorkerTaskKind,
    WorkerTaskRequest,
    encode_json_line,
    parse_task_request,
)


class WorkerReporter:
    def __init__(self, task_request: WorkerTaskRequest) -> None:
        self.task_request = task_request

    def emit(
        self,
        event: WorkerEventType,
        *,
        stage: str = "",
        progress: float = 0.0,
        message: str = "",
        payload: dict[str, Any] | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        worker_event = WorkerEvent(
            event=event,
            task_id=self.task_request.task_id,
            task_kind=self.task_request.task_kind,
            stage=stage,
            progress=progress,
            message=message,
            payload=payload or {},
            error=error,
        )
        sys.stdout.write(encode_json_line(worker_event))
        sys.stdout.flush()

    def log(self, stage: str, message: str, progress: float = 0.0) -> None:
        self.emit(WorkerEventType.log, stage=stage, progress=progress, message=message)


def _configure_stdio() -> None:
    stream_configs = {
        "stdin": {"encoding": "utf-8", "errors": "strict"},
        "stdout": {"encoding": "utf-8", "errors": "strict"},
        "stderr": {"encoding": "utf-8", "errors": "backslashreplace"},
    }
    for stream_name, config in stream_configs.items():
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(**config)


def main() -> int:
    _configure_stdio()
    services = build_services()

    for raw_request in sys.stdin:
        request_line = raw_request.strip()
        if not request_line:
            continue

        try:
            task_request = parse_task_request(request_line)
        except Exception as exc:
            sys.stderr.write(f"Worker task request is invalid: {exc}\n")
            sys.stderr.flush()
            return 1

        reporter = WorkerReporter(task_request)

        try:
            _dispatch_task(task_request, reporter, services)
        except Exception as exc:
            reporter.emit(
                WorkerEventType.failed,
                stage="worker",
                message=str(exc),
                error=ErrorInfo(code="worker_unhandled_exception", message=str(exc)),
            )

    return 0


def build_services() -> dict[str, object]:
    settings = load_settings()
    load_persisted_llm_settings(settings)
    llm_client = LLMClient(
        api_key=settings.llm.api_key,
        api_base=settings.llm.api_base,
        model=settings.llm.model,
        timeout_sec=settings.llm.timeout_sec,
    )
    content = ContentService()
    rewrite = RewriteService(llm_client=llm_client)
    tts = TTSService()
    subtitle = SubtitleService(corrector=SubtitleCorrector(llm_client=llm_client))
    avatar = AvatarService()
    workflow = WorkflowService(
        content_service=content,
        rewrite_service=rewrite,
        tts_service=tts,
        subtitle_service=subtitle,
        avatar_service=avatar,
    )
    return {
        "content": content,
        "rewrite": rewrite,
        "tts": tts,
        "subtitle": subtitle,
        "avatar": avatar,
        "workflow": workflow,
    }


def _dispatch_task(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    if task_request.task_kind is WorkerTaskKind.full_workflow:
        return _run_full_workflow(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.content:
        return _run_content(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.rewrite:
        return _run_rewrite(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.rewrite_text:
        return _run_rewrite_text(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.tts:
        return _run_tts(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.subtitle:
        return _run_subtitle(task_request, reporter, services)
    if task_request.task_kind is WorkerTaskKind.avatar:
        return _run_avatar(task_request, reporter, services)

    message = f"不支持的任务类型：{task_request.task_kind.value}"
    reporter.emit(
        WorkerEventType.failed,
        stage="worker",
        message=message,
        error=ErrorInfo(code="unsupported_task_kind", message=message),
    )
    return 1


def _run_full_workflow(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = GenerateVideoWorkflowRequest.model_validate(task_request.payload)
    reporter.emit(
        WorkerEventType.started,
        stage="workflow",
        progress=0.01,
        message="已提交完整流程任务。",
    )

    def progress_callback(stage: str, progress: float, message: str) -> None:
        reporter.emit(
            WorkerEventType.progress,
            stage=stage,
            progress=progress,
            message=message,
        )

    workflow_service = services["workflow"]
    result = workflow_service.generate_video(
        request,
        progress_callback=progress_callback,
        task_id=task_request.task_id,
    )
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="avatar",
            progress=1.0,
            message=result.summary or "完整流程已完成。",
            payload={"workflow": result.model_dump(mode="json")},
        )
        return 0

    reporter.emit(
        WorkerEventType.failed,
        stage="workflow",
        progress=result.context.progress,
        message=result.error.message if result.error else "完整流程执行失败。",
        payload={"workflow": result.model_dump(mode="json")},
        error=result.error,
    )
    return 1


def _run_content(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = ContentRequest.model_validate(task_request.payload)
    reporter.emit(WorkerEventType.started, stage="content", progress=0.05, message="开始执行文案提取任务。")
    content_service = services["content"]
    result = content_service.extract(request)
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="content",
            progress=1.0,
            message="文案提取任务已完成。",
            payload={"content": result.model_dump(mode="json")},
        )
        return 0
    reporter.emit(
        WorkerEventType.failed,
        stage="content",
        progress=1.0,
        message=result.error.message if result.error else "文案提取任务失败。",
        error=result.error,
    )
    return 1


def _run_rewrite(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    payload = task_request.payload
    source = VideoSource.model_validate(payload["source"])
    workspace = str(payload["workspace"])
    reporter.emit(WorkerEventType.started, stage="content", progress=0.05, message="开始执行提取并改写任务。")
    content_service = services["content"]
    content_result = content_service.extract(
        ContentRequest(
            source=source,
            workspace=workspace,
            download_video=bool(payload.get("download_video", False)),
            extract_audio=bool(payload.get("extract_audio", False)),
            stream_transcription=bool(payload.get("stream_transcription", True)),
        )
    )
    if not content_result.success or not content_result.extracted_copy:
        reporter.emit(
            WorkerEventType.failed,
            stage="content",
            progress=0.2,
            message=content_result.error.message if content_result.error else "文案提取失败。",
            error=content_result.error,
        )
        return 1
    reporter.emit(WorkerEventType.progress, stage="content", progress=0.35, message="文案提取完成。")

    rewrite_service = services["rewrite"]
    rewrite_result = rewrite_service.rewrite(
        RewriteRequest(
            text=content_result.extracted_copy.cleaned_text,
            mode=payload["rewrite_mode"],
            prompt=payload.get("rewrite_prompt"),
            model=payload.get("rewrite_model", "deepseek"),
            workspace=workspace,
        )
    )
    if rewrite_result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="rewrite",
            progress=1.0,
            message="文案改写任务已完成。",
            payload={
                "content": content_result.model_dump(mode="json"),
                "rewrite": rewrite_result.model_dump(mode="json"),
            },
        )
        return 0

    reporter.emit(
        WorkerEventType.failed,
        stage="rewrite",
        progress=1.0,
        message=rewrite_result.error.message if rewrite_result.error else "文案改写失败。",
        error=rewrite_result.error,
    )
    return 1


def _run_rewrite_text(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = RewriteRequest.model_validate(task_request.payload)
    reporter.emit(WorkerEventType.started, stage="rewrite", progress=0.05, message="开始执行文案改写任务。")
    rewrite_service = services["rewrite"]
    result = rewrite_service.rewrite(request)
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="rewrite",
            progress=1.0,
            message="文案改写任务已完成。",
            payload={"rewrite": result.model_dump(mode="json")},
        )
        return 0
    reporter.emit(
        WorkerEventType.failed,
        stage="rewrite",
        progress=1.0,
        message=result.error.message if result.error else "文案改写失败。",
        error=result.error,
    )
    return 1


def _run_tts(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = TTSRequest.model_validate(task_request.payload)
    reporter.emit(WorkerEventType.started, stage="tts", progress=0.05, message="开始执行语音合成任务。")
    tts_service = services["tts"]
    result = tts_service.synthesize(request)
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="tts",
            progress=1.0,
            message="语音合成任务已完成。",
            payload={"tts": result.model_dump(mode="json")},
        )
        return 0
    reporter.emit(
        WorkerEventType.failed,
        stage="tts",
        progress=1.0,
        message=result.error.message if result.error else "语音合成任务失败。",
        error=result.error,
    )
    return 1


def _run_subtitle(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = SubtitleRequest.model_validate(task_request.payload)
    reporter.emit(WorkerEventType.started, stage="subtitle", progress=0.05, message="开始执行字幕任务。")
    subtitle_service = services["subtitle"]
    result = subtitle_service.generate(request)
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="subtitle",
            progress=1.0,
            message="字幕任务已完成。",
            payload={"subtitle": result.model_dump(mode="json")},
        )
        return 0
    reporter.emit(
        WorkerEventType.failed,
        stage="subtitle",
        progress=1.0,
        message=result.error.message if result.error else "字幕任务失败。",
        error=result.error,
    )
    return 1


def _run_avatar(task_request: WorkerTaskRequest, reporter: WorkerReporter, services: dict[str, object]) -> int:
    request = AvatarRequest.model_validate(task_request.payload)
    reporter.emit(
        WorkerEventType.started,
        stage="avatar",
        progress=0.05,
        message="开始执行数字人渲染任务（严格 GPU 模式）。",
    )
    tts_service = services.get("tts")
    avatar_service = services["avatar"]
    _prepare_tts_for_video_render(tts_service, reporter)
    result = avatar_service.render(request)
    if not result.success and _avatar_result_is_cuda_oom(result):
        _release_tts_resources(
            tts_service,
            reporter=reporter,
            success_message="视频渲染显存不足，已释放音频模型并重试一次。",
            failure_message="视频渲染显存不足，但释放音频模型失败：{error}",
        )
        result = avatar_service.render(request)
    if result.success:
        reporter.emit(
            WorkerEventType.succeeded,
            stage="avatar",
            progress=1.0,
            message="数字人渲染任务已完成。",
            payload={"avatar": result.model_dump(mode="json")},
        )
        return 0
    reporter.emit(
        WorkerEventType.failed,
        stage="avatar",
        progress=1.0,
        message=result.error.message if result.error else "数字人渲染任务失败。",
        error=result.error,
    )
    return 1


def _prepare_tts_for_video_render(tts_service: object | None, reporter: WorkerReporter) -> bool:
    decision = evaluate_tts_release_for_video()
    if not decision.should_release:
        return False
    return _release_tts_resources(
        tts_service,
        reporter=reporter,
        success_message=decision.reason,
        failure_message=f"{decision.reason} 释放音频模型失败：{{error}}",
    )


def _avatar_result_is_cuda_oom(result: object) -> bool:
    error = getattr(result, "error", None)
    message = getattr(error, "message", None)
    return is_cuda_oom_error(message)


def _release_tts_resources(
    tts_service: object | None,
    *,
    reporter: WorkerReporter | None = None,
    success_message: str | None = None,
    failure_message: str | None = None,
) -> bool:
    release = getattr(tts_service, "release_resources", None)
    if not callable(release):
        return False

    try:
        release()
    except Exception as exc:
        if reporter and failure_message:
            reporter.emit(
                WorkerEventType.log,
                stage="gpu",
                message=failure_message.format(error=str(exc)),
            )
        return False

    if reporter and success_message:
        reporter.emit(WorkerEventType.log, stage="gpu", message=success_message)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
