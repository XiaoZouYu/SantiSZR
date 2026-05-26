from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
import queue
import threading
import uuid
from typing import Any

from pydantic import BaseModel

from santiszr.app import AppContext
from santiszr.core.gpu_memory import VideoMemoryDecision, evaluate_tts_release_for_video, is_cuda_oom_error
from santiszr.domain.schemas.audio import RewriteRequest, RewriteResult, TTSRequest
from santiszr.domain.schemas.avatar import AvatarRequest
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.content import ContentRequest, ContentResult, VideoSource
from santiszr.domain.schemas.postprocess import PostProcessRequest
from santiszr.domain.schemas.publish import (
    GenerateVideoWorkflowRequest,
    PublishBatchRequest,
)
from santiszr.domain.schemas.subtitle import SubtitleRequest
from santiszr.domain.services.postprocess_service import PostProcessService
from santiszr.web.schemas import TaskEventResponse, TaskRecordResponse, WebTaskKind, WebTaskStatus


_FINAL_STATUSES = {
    WebTaskStatus.succeeded,
    WebTaskStatus.failed,
    WebTaskStatus.cancelled,
}


class TaskConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class _TaskRecord:
    task_id: str
    kind: WebTaskKind
    status: WebTaskStatus
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cancel_requested: bool = False
    future: Future[None] | None = None

    def to_response(self) -> TaskRecordResponse:
        return TaskRecordResponse(
            task_id=self.task_id,
            kind=self.kind,
            status=self.status,
            stage=self.stage,
            progress=self.progress,
            message=self.message,
            logs=list(self.logs),
            result=self.result,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class WebTaskManager:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self._postprocess_service = PostProcessService()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="santiszr-web-task")
        self._lock = threading.RLock()
        self._tasks: dict[str, _TaskRecord] = {}
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()

    def submit(self, kind: WebTaskKind, payload: dict[str, Any]) -> TaskRecordResponse:
        with self._lock:
            active = [
                task for task in self._tasks.values() if task.status not in _FINAL_STATUSES
            ]
            if active:
                raise TaskConflictError(
                    f"Task {active[0].task_id} is already {active[0].status.value}."
                )

            task = _TaskRecord(
                task_id=uuid.uuid4().hex,
                kind=kind,
                status=WebTaskStatus.queued,
                message="Task queued.",
            )
            self._tasks[task.task_id] = task
            future = self._executor.submit(self._run_task, task.task_id, dict(payload))
            task.future = future
            task.updated_at = datetime.now(UTC)
            self._publish_event(task, message="Task queued.")
            return task.to_response()

    def list_tasks(self) -> list[TaskRecordResponse]:
        with self._lock:
            return [
                task.to_response()
                for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
            ]

    def recent_tasks(self, limit: int = 20) -> list[TaskRecordResponse]:
        return self.list_tasks()[:limit]

    def current_task(self) -> TaskRecordResponse | None:
        with self._lock:
            for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True):
                if task.status not in _FINAL_STATUSES:
                    return task.to_response()
        return None

    def get_task(self, task_id: str) -> TaskRecordResponse | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_response() if task else None

    def cancel(self, task_id: str) -> TaskRecordResponse | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status in _FINAL_STATUSES:
                return task.to_response()

            task.cancel_requested = True
            if task.future is not None and task.future.cancel():
                self._finish_task(
                    task,
                    WebTaskStatus.cancelled,
                    stage=task.stage or "task",
                    progress=task.progress,
                    message="Task cancelled before it started.",
                )
            else:
                task.message = "Cancellation requested. Running work will stop at the next safe point."
                task.logs.append(task.message)
                task.updated_at = datetime.now(UTC)
                self._publish_event(task, message=task.message)
            return task.to_response()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_task(self, task_id: str, payload: dict[str, Any]) -> None:
        task = self._task_or_none(task_id)
        if task is None:
            return

        self._update_task(task, status=WebTaskStatus.running, stage=task.kind.value, progress=0.01, message="Task started.")

        try:
            result = self._execute(task, payload)
            if task.cancel_requested:
                self._finish_task(
                    task,
                    WebTaskStatus.cancelled,
                    stage=task.stage,
                    progress=task.progress,
                    message="Task cancelled.",
                )
                return

            payload_key = task.kind.value
            result_payload = _model_to_json(result)
            success = bool(getattr(result, "success", True))
            error = getattr(result, "error", None)
            if success:
                self._apply_result_to_state(task.kind, result)
                self._finish_task(
                    task,
                    WebTaskStatus.succeeded,
                    stage=task.stage or task.kind.value,
                    progress=1.0,
                    message="Task completed.",
                    result={payload_key: result_payload},
                )
            else:
                self._finish_task(
                    task,
                    WebTaskStatus.failed,
                    stage=task.stage or task.kind.value,
                    progress=task.progress,
                    message=error.message if error else "Task failed.",
                    result={payload_key: result_payload},
                    error=error or ErrorInfo(code="task_failed", message="Task failed."),
                )
        except Exception as exc:
            self._finish_task(
                task,
                WebTaskStatus.failed,
                stage=task.stage or task.kind.value,
                progress=task.progress,
                message=str(exc),
                error=ErrorInfo(code="task_failed", message=str(exc)),
            )

    def _execute(self, task: _TaskRecord, payload: dict[str, Any]) -> BaseModel:
        services = self.context.services
        if task.kind is WebTaskKind.content:
            request = ContentRequest.model_validate(payload)
            return services.content.extract(request)

        if task.kind is WebTaskKind.rewrite:
            if "source" in payload:
                return self._extract_and_rewrite(task, payload)
            return services.rewrite.rewrite(RewriteRequest.model_validate(payload))

        if task.kind is WebTaskKind.tts:
            return services.tts.synthesize(TTSRequest.model_validate(payload))

        if task.kind is WebTaskKind.subtitle:
            return services.subtitle.generate(SubtitleRequest.model_validate(payload))

        if task.kind is WebTaskKind.avatar:
            return self._render_avatar(task, AvatarRequest.model_validate(payload))

        if task.kind is WebTaskKind.workflow:
            return services.workflow.generate_video(
                GenerateVideoWorkflowRequest.model_validate(payload),
                progress_callback=lambda stage, progress, message: self._progress(
                    task, stage, progress, message
                ),
                task_id=task.task_id,
            )

        if task.kind is WebTaskKind.postprocess:
            return self._postprocess_service.process(PostProcessRequest.model_validate(payload))

        if task.kind is WebTaskKind.publish_materials:
            return services.publish.publish_batch(PublishBatchRequest.model_validate(payload))

        raise ValueError(f"Unsupported task kind: {task.kind.value}")

    def _render_avatar(self, task: _TaskRecord, request: AvatarRequest) -> BaseModel:
        services = self.context.services
        self._prepare_tts_for_video_render(task)
        result = services.avatar.render(request)
        if not bool(getattr(result, "success", True)) and self._avatar_result_is_cuda_oom(result):
            self._release_tts_resources(
                task,
                "Avatar rendering hit CUDA memory pressure. Released the TTS GPU model and retrying once.",
            )
            result = services.avatar.render(request)
        return result

    def _prepare_tts_for_video_render(self, task: _TaskRecord) -> bool:
        decision = evaluate_tts_release_for_video()
        if not decision.should_release:
            return False
        return self._release_tts_resources(task, self._video_memory_release_message(decision))

    def _release_tts_resources(self, task: _TaskRecord, message: str) -> bool:
        release = getattr(self.context.services.tts, "release_resources", None)
        if not callable(release):
            self._progress(task, "avatar", max(task.progress, 0.04), "TTS service cannot release GPU resources.")
            return False
        self._progress(task, "avatar", max(task.progress, 0.04), message)
        release()
        return True

    def _avatar_result_is_cuda_oom(self, result: BaseModel) -> bool:
        error = getattr(result, "error", None)
        message = getattr(error, "message", None)
        return is_cuda_oom_error(message)

    def _video_memory_release_message(self, decision: VideoMemoryDecision) -> str:
        snapshot = decision.snapshot
        if snapshot is None:
            return "Releasing the TTS GPU model before avatar rendering because GPU memory status is unavailable."
        return (
            "Releasing the TTS GPU model before avatar rendering "
            f"({snapshot.free_mb}/{snapshot.total_mb} MB GPU memory free)."
        )

    def _extract_and_rewrite(self, task: _TaskRecord, payload: dict[str, Any]) -> RewriteResult:
        workspace = str(payload["workspace"])
        source = VideoSource.model_validate(payload["source"])
        self._progress(task, "content", 0.1, "Extracting source copy.")
        content_result = self.context.services.content.extract(
            ContentRequest(
                source=source,
                workspace=workspace,
                download_video=bool(payload.get("download_video", False)),
                extract_audio=bool(payload.get("extract_audio", False)),
                stream_transcription=bool(payload.get("stream_transcription", True)),
            )
        )
        if not content_result.success or not content_result.extracted_copy:
            return RewriteResult(
                success=False,
                error=content_result.error or ErrorInfo(code="content_failed", message="Content extraction failed."),
            )
        self._progress(task, "rewrite", 0.5, "Rewriting copy.")
        return self.context.services.rewrite.rewrite(
            RewriteRequest(
                text=content_result.extracted_copy.cleaned_text,
                mode=payload["rewrite_mode"],
                prompt=payload.get("rewrite_prompt"),
                model=payload.get("rewrite_model", "deepseek"),
                workspace=workspace,
            )
        )

    def _apply_result_to_state(self, kind: WebTaskKind, result: BaseModel) -> None:
        state = self.context.state
        payload = _model_to_json(result)
        if kind is WebTaskKind.content:
            state.workspace = str(payload.get("workspace") or state.workspace)
            state.source_video_path = str(payload.get("video_path") or state.source_video_path)
            extracted = payload.get("extracted_copy")
            if isinstance(extracted, dict):
                state.extracted_text = str(extracted.get("cleaned_text") or state.extracted_text)
        elif kind is WebTaskKind.rewrite:
            state.rewritten_text = str(payload.get("rewritten_text") or state.rewritten_text)
            state.rewritten_title = str(payload.get("title") or state.rewritten_title)
            tags = payload.get("tags")
            if isinstance(tags, list):
                state.tags = [str(tag) for tag in tags]
        elif kind is WebTaskKind.tts:
            state.audio_path = str(payload.get("audio_path") or state.audio_path)
        elif kind is WebTaskKind.subtitle:
            state.subtitle_path = str(payload.get("srt_path") or state.subtitle_path)
            state.final_video_path = str(payload.get("burned_video_path") or state.final_video_path)
        elif kind is WebTaskKind.avatar:
            video_path = str(payload.get("video_path") or "")
            state.avatar_video_path = video_path or state.avatar_video_path
            state.final_video_path = video_path or state.final_video_path
        elif kind is WebTaskKind.workflow:
            state.final_video_path = str(payload.get("final_video_path") or state.final_video_path)
        elif kind is WebTaskKind.postprocess:
            state.final_video_path = str(payload.get("final_video_path") or state.final_video_path)

    def _task_or_none(self, task_id: str) -> _TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _progress(self, task: _TaskRecord, stage: str, progress: float, message: str) -> None:
        self._update_task(
            task,
            status=WebTaskStatus.running,
            stage=stage,
            progress=progress,
            message=message,
        )

    def _update_task(
        self,
        task: _TaskRecord,
        *,
        status: WebTaskStatus,
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        with self._lock:
            task.status = status
            task.stage = stage
            task.progress = max(0.0, min(progress, 1.0))
            task.message = message
            if message:
                task.logs.append(message)
            task.updated_at = datetime.now(UTC)
            self._publish_event(task, message=message)

    def _finish_task(
        self,
        task: _TaskRecord,
        status: WebTaskStatus,
        *,
        stage: str,
        progress: float,
        message: str,
        result: dict[str, Any] | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        with self._lock:
            task.status = status
            task.stage = stage
            task.progress = max(0.0, min(progress, 1.0))
            task.message = message
            if message:
                task.logs.append(message)
            task.result = result
            task.error = error
            task.updated_at = datetime.now(UTC)
            self._publish_event(task, message=message, payload=result or {}, error=error)

    def _publish_event(
        self,
        task: _TaskRecord,
        *,
        message: str,
        payload: dict[str, Any] | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        event = TaskEventResponse(
            task_id=task.task_id,
            kind=task.kind,
            status=task.status,
            stage=task.stage,
            progress=task.progress,
            message=message,
            payload=payload or {},
            error=error,
            created_at=datetime.now(UTC),
        ).model_dump(mode="json")
        for subscriber in list(self._subscribers):
            subscriber.put(event)


def _model_to_json(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
