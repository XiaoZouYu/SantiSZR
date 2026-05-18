from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from PySide6.QtCore import QObject, QThreadPool, Signal
from pydantic import BaseModel

from santiszr.config.settings import AppSettings
from santiszr.domain.schemas.audio import RewriteResult, TTSResult
from santiszr.domain.schemas.avatar import AvatarResult
from santiszr.domain.schemas.common import ErrorInfo, TaskStatus
from santiszr.domain.schemas.content import ContentResult
from santiszr.domain.schemas.publish import GenerateVideoWorkflowResult
from santiszr.domain.schemas.subtitle import SubtitleResult
from santiszr.gui.i18n import task_kind_text
from santiszr.gui.state.session import PipelineState
from santiszr.workers.protocol import (
    WorkerEvent,
    WorkerEventType,
    WorkerTaskKind,
    WorkerTaskRequest,
    encode_json_line,
    parse_worker_event,
)


_FINAL_WORKER_EVENTS = {
    WorkerEventType.succeeded,
    WorkerEventType.failed,
    WorkerEventType.cancelled,
}


def _sanitize_worker_environment(source_env: dict[str, str]) -> dict[str, str]:
    sanitized = dict(source_env)
    conda_prefix = str(source_env.get("CONDA_PREFIX", "") or "").strip()
    normalized_conda_prefix = conda_prefix.lower().replace("/", "\\")

    for name in list(sanitized):
        if name.startswith("CONDA"):
            sanitized.pop(name, None)
    sanitized.pop("KMP_DUPLICATE_LIB_OK", None)

    filtered_paths: list[str] = []
    for entry in sanitized.get("PATH", "").split(os.pathsep):
        candidate = entry.strip()
        if not candidate:
            continue
        normalized_candidate = candidate.lower().replace("/", "\\")
        if normalized_conda_prefix and (
            normalized_candidate == normalized_conda_prefix
            or normalized_candidate.startswith(f"{normalized_conda_prefix}\\")
        ):
            continue
        filtered_paths.append(candidate)

    sanitized["PATH"] = os.pathsep.join(filtered_paths)
    return sanitized


class TaskProcessSignals(QObject):
    event_received = Signal(object)
    process_finished = Signal(str, int, str)


class TaskProcessSession:
    def __init__(
        self,
        *,
        python_executable: str,
        project_root: Path,
        env_overrides: dict[str, str],
    ) -> None:
        self.python_executable = python_executable
        self.project_root = project_root
        self.signals = TaskProcessSignals()
        self._env_overrides = dict(env_overrides)
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._current_task_request: WorkerTaskRequest | None = None
        self._stderr_lines: list[str] = []
        self._cancel_requested = False
        self._lock = threading.RLock()

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    @property
    def task_request(self) -> WorkerTaskRequest | None:
        with self._lock:
            return self._current_task_request

    def update_env_overrides(self, env_overrides: dict[str, str]) -> None:
        should_restart = False
        normalized = dict(env_overrides)
        with self._lock:
            if self._env_overrides == normalized:
                return
            self._env_overrides = normalized
            process = self._process
            should_restart = (
                process is not None
                and process.poll() is None
                and self._current_task_request is None
            )
        if should_restart:
            self.shutdown()

    def submit(self, task_request: WorkerTaskRequest) -> None:
        self._ensure_process()
        with self._lock:
            if self._current_task_request is not None:
                raise RuntimeError("Worker 已有正在执行的任务。")

            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise RuntimeError("Worker 进程不可用。")

            self._current_task_request = task_request
            self._stderr_lines = []
            self._cancel_requested = False

        try:
            process.stdin.write(encode_json_line(task_request))
            process.stdin.flush()
        except Exception as exc:
            self._clear_task(task_request.task_id)
            self.shutdown()
            raise RuntimeError(f"提交任务到 worker 失败: {exc}") from exc

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)

    def _ensure_process(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                return
            self._process = self._start_process()
            process = self._process

        assert process is not None
        self._stdout_thread = threading.Thread(
            target=self._consume_stdout,
            args=(process,),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._consume_stderr,
            args=(process,),
            daemon=True,
        )
        self._watcher_thread = threading.Thread(
            target=self._watch_process,
            args=(process,),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._watcher_thread.start()

    def _start_process(self) -> subprocess.Popen[str]:
        env = _sanitize_worker_environment(os.environ.copy())
        src_path = str(self.project_root / "src")
        env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(self._env_overrides)
        return subprocess.Popen(
            [self.python_executable, "-u", "-m", "santiszr.workers.runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.project_root),
            env=env,
        )

    def _consume_stdout(self, process: subprocess.Popen[str]) -> None:
        if not process.stdout:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = parse_worker_event(line)
            except Exception:
                task_request = self.task_request
                if task_request is None:
                    continue
                event = WorkerEvent(
                    event=WorkerEventType.log,
                    task_id=task_request.task_id,
                    task_kind=task_request.task_kind,
                    stage="worker",
                    message=line,
                )
            else:
                if event.event in _FINAL_WORKER_EVENTS:
                    self._clear_task(event.task_id)
            self.signals.event_received.emit(event)

    def _consume_stderr(self, process: subprocess.Popen[str]) -> None:
        if not process.stderr:
            return
        for raw_line in process.stderr:
            line = raw_line.strip()
            if not line:
                continue
            with self._lock:
                self._stderr_lines.append(line)
                task_request = self._current_task_request
            if task_request is None:
                continue
            self.signals.event_received.emit(
                WorkerEvent(
                    event=WorkerEventType.log,
                    task_id=task_request.task_id,
                    task_kind=task_request.task_kind,
                    stage="stderr",
                    message=line,
                )
            )

    def _watch_process(self, process: subprocess.Popen[str]) -> None:
        exit_code = process.wait()
        current_thread = threading.current_thread()
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is None or thread is current_thread:
                continue
            thread.join(timeout=1.0)

        with self._lock:
            task_request = self._current_task_request
            stderr_output = "\n".join(self._stderr_lines).strip()
            if self._process is process:
                self._process = None
                self._stdout_thread = None
                self._stderr_thread = None
                self._watcher_thread = None
            task_id = task_request.task_id if task_request else ""

        self.signals.process_finished.emit(task_id, exit_code, stderr_output)

    def _clear_task(self, task_id: str) -> None:
        with self._lock:
            task_request = self._current_task_request
            if task_request is None or task_request.task_id != task_id:
                return
            self._current_task_request = None
            self._stderr_lines = []
            self._cancel_requested = False


class TaskController(QObject):
    state_changed = Signal(object)
    task_event = Signal(object)

    def __init__(
        self,
        *,
        state: PipelineState,
        app_settings: AppSettings | None = None,
        python_executable: str | None = None,
        project_root: Path | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.app_settings = app_settings
        self.python_executable = python_executable or sys.executable
        self.project_root = project_root or Path(__file__).resolve().parents[3]
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._active_runners: dict[str, object] = {}
        self._worker_session = TaskProcessSession(
            python_executable=self.python_executable,
            project_root=self.project_root,
            env_overrides=self._worker_env_overrides(),
        )
        self._worker_session.signals.event_received.connect(self._handle_worker_event)
        self._worker_session.signals.process_finished.connect(self._handle_worker_finished)

    def shutdown(self) -> None:
        self._worker_session.shutdown()

    def publish_state(self) -> None:
        self.state_changed.emit(self.state)

    def submit_task(self, task_kind: WorkerTaskKind, payload: BaseModel | dict[str, object]) -> str:
        if self.state.is_running:
            self.state.last_error = "已有任务正在运行，请先取消当前任务再启动新任务。"
            self.state.append_log(self.state.last_error)
            self.publish_state()
            return ""

        task_id = uuid.uuid4().hex
        body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
        task_request = WorkerTaskRequest(task_id=task_id, task_kind=task_kind, payload=body)

        self._worker_session.update_env_overrides(self._worker_env_overrides())
        self._active_runners[task_id] = self._worker_session
        self.state.begin_task(task_id=task_id, task_kind=task_kind.value)
        self.state.append_log(f"已提交 {task_kind_text(task_kind.value)} 任务。")
        self.publish_state()

        try:
            self._worker_session.submit(task_request)
        except Exception as exc:
            error_message = str(exc)
            self._active_runners.pop(task_id, None)
            self.state.fail_task(task_kind=task_kind.value, message=error_message)
            self.task_event.emit(
                WorkerEvent(
                    event=WorkerEventType.failed,
                    task_id=task_id,
                    task_kind=task_kind,
                    stage="worker",
                    message=error_message,
                    error=ErrorInfo(
                        code="worker_submit_failed",
                        message=error_message,
                    ),
                )
            )
            self.publish_state()
            return ""
        return task_id

    def cancel_active_task(self) -> bool:
        task_id = self.state.active_task_id
        if not task_id:
            return False
        runner = self._active_runners.get(task_id)
        if not runner:
            return False
        runner.cancel()
        self.state.is_cancellable = False
        self.state.append_log("已请求取消当前任务。")
        self.publish_state()
        return True

    def _handle_worker_event(self, event: WorkerEvent) -> None:
        if event.task_id != self.state.active_task_id and event.event is not WorkerEventType.log:
            return

        if event.event is WorkerEventType.started:
            task_name = task_kind_text(event.task_kind.value)
            self.state.update_progress(
                stage=event.stage or event.task_kind.value,
                progress=max(event.progress, 0.01),
                message=event.message or f"{task_name} 已启动。",
            )
            if event.message:
                self.state.append_log(event.message)
        elif event.event is WorkerEventType.progress:
            self.state.update_progress(
                stage=event.stage or self.state.active_stage,
                progress=event.progress,
                message=event.message or self.state.last_message,
            )
            if event.message:
                self.state.append_log(event.message)
        elif event.event is WorkerEventType.log:
            self.state.append_log(event.message)
        elif event.event is WorkerEventType.succeeded:
            self._apply_payload(event.task_kind, event.payload)
            self.state.complete_task(
                task_kind=event.task_kind.value,
                status=TaskStatus.succeeded,
                message=event.message or f"{task_kind_text(event.task_kind.value)} 已完成。",
            )
        elif event.event is WorkerEventType.failed:
            error_message = event.error.message if event.error else (
                event.message or f"{task_kind_text(event.task_kind.value)} 失败。"
            )
            self.state.fail_task(task_kind=event.task_kind.value, message=error_message)
        elif event.event is WorkerEventType.cancelled:
            self.state.cancel_task(
                task_kind=event.task_kind.value,
                message=event.message or f"{task_kind_text(event.task_kind.value)} 已取消。",
            )

        if event.event in _FINAL_WORKER_EVENTS:
            self._active_runners.pop(event.task_id, None)

        self.task_event.emit(event)
        self.publish_state()

    def _handle_worker_finished(self, task_id: str, exit_code: int, stderr_output: str) -> None:
        if not task_id:
            return

        runner = self._active_runners.pop(task_id, None)
        if stderr_output:
            for line in stderr_output.splitlines():
                self.state.append_log(line)

        if runner and getattr(runner, "cancel_requested", False) and self.state.status is TaskStatus.running:
            self.state.cancel_task(task_kind=self.state.active_task_kind or "task", message="任务已取消。")
        elif exit_code != 0 and self.state.status is TaskStatus.running:
            task_kind = runner.task_request.task_kind if runner and runner.task_request else WorkerTaskKind(
                self.state.active_task_kind
            )
            error_message = stderr_output or f"任务进程异常退出，退出码：{exit_code}。"
            self.state.fail_task(
                task_kind=task_kind.value,
                message=error_message,
            )
            self.task_event.emit(
                WorkerEvent(
                    event=WorkerEventType.failed,
                    task_id=task_id,
                    task_kind=task_kind,
                    stage="worker",
                    message=error_message,
                    error=ErrorInfo(
                        code="worker_process_exit",
                        message=error_message,
                        detail={"exit_code": exit_code},
                    ),
                )
            )
        self.publish_state()

    def _apply_payload(self, task_kind: WorkerTaskKind, payload: dict[str, object]) -> None:
        if task_kind is WorkerTaskKind.content and "content" in payload:
            self._apply_content_result(ContentResult.model_validate(payload["content"]))
            return
        if task_kind is WorkerTaskKind.rewrite:
            if "content" in payload:
                self._apply_content_result(ContentResult.model_validate(payload["content"]))
            if "rewrite" in payload:
                self._apply_rewrite_result(RewriteResult.model_validate(payload["rewrite"]))
            return
        if task_kind is WorkerTaskKind.rewrite_text and "rewrite" in payload:
            self._apply_rewrite_result(RewriteResult.model_validate(payload["rewrite"]))
            return
        if task_kind is WorkerTaskKind.tts and "tts" in payload:
            self._apply_tts_result(TTSResult.model_validate(payload["tts"]))
            return
        if task_kind is WorkerTaskKind.subtitle and "subtitle" in payload:
            self._apply_subtitle_result(SubtitleResult.model_validate(payload["subtitle"]))
            return
        if task_kind is WorkerTaskKind.avatar and "avatar" in payload:
            self._apply_avatar_result(AvatarResult.model_validate(payload["avatar"]))
            return
        if task_kind is WorkerTaskKind.full_workflow and "workflow" in payload:
            workflow_result = GenerateVideoWorkflowResult.model_validate(payload["workflow"])
            if workflow_result.artifacts.content:
                self._apply_content_result(workflow_result.artifacts.content)
            if workflow_result.artifacts.rewrite:
                self._apply_rewrite_result(workflow_result.artifacts.rewrite)
            if workflow_result.artifacts.tts:
                self._apply_tts_result(workflow_result.artifacts.tts)
            if workflow_result.artifacts.subtitle:
                self._apply_subtitle_result(workflow_result.artifacts.subtitle)
            if workflow_result.artifacts.avatar:
                self._apply_avatar_result(workflow_result.artifacts.avatar)
            self.state.final_video_path = workflow_result.final_video_path or self.state.final_video_path
            if workflow_result.summary:
                self.state.append_log(workflow_result.summary)

    def _apply_content_result(self, result: ContentResult) -> None:
        self.state.workspace = result.workspace or self.state.workspace
        self.state.source_video_path = result.video_path or self.state.source_video_path
        if result.extracted_copy:
            self.state.extracted_text = result.extracted_copy.cleaned_text

    def _apply_rewrite_result(self, result: RewriteResult) -> None:
        self.state.rewritten_text = result.rewritten_text or self.state.rewritten_text
        self.state.rewritten_title = result.title or self.state.rewritten_title
        self.state.tags = result.tags or self.state.tags

    def _apply_tts_result(self, result: TTSResult) -> None:
        self.state.audio_path = result.audio_path or self.state.audio_path

    def _apply_subtitle_result(self, result: SubtitleResult) -> None:
        self.state.subtitle_path = result.srt_path or self.state.subtitle_path
        if result.burned_video_path:
            self.state.final_video_path = result.burned_video_path

    def _apply_avatar_result(self, result: AvatarResult) -> None:
        self.state.avatar_video_path = result.video_path or self.state.avatar_video_path
        self.state.final_video_path = result.video_path or self.state.final_video_path

    def _worker_env_overrides(self) -> dict[str, str]:
        settings = self.app_settings
        if settings is None:
            return {}

        env: dict[str, str] = {}

        def assign(name: str, value: object | None) -> None:
            if value is None:
                return
            text = str(value).strip()
            if text:
                env[name] = text

        assign("SANTISZR_FFMPEG_PATH", settings.media.ffmpeg_path)
        assign("SANTISZR_FFPROBE_PATH", settings.media.ffprobe_path)
        assign("SANTISZR_MODEL_ROOT", settings.models.root_dir)
        assign("SANTISZR_COSYVOICE_MODEL_DIR", settings.models.cosyvoice_model_dir)
        assign("SANTISZR_VOXCPM_MODEL_DIR", settings.models.voxcpm_model_dir)
        assign("SANTISZR_WHISPER_MODEL_DIR", settings.models.whisper_model_dir)
        assign("SANTISZR_TUILIONNX_MODEL_DIR", settings.models.tuilionnx_model_dir)
        assign("SANTISZR_TUILIONNX_ROOT", settings.avatar.tuilionnx_root)
        assign("SANTISZR_TUILIONNX_PYTHON", settings.avatar.tuilionnx_python)
        assign("SANTISZR_LLM_API_BASE", settings.llm.api_base)
        assign("SANTISZR_LLM_MODEL", settings.llm.model)
        assign("SANTISZR_LLM_API_KEY", settings.llm.api_key)
        assign("SANTISZR_TTS_PROVIDER", settings.tts.provider)
        assign("SANTISZR_TTS_BASE_URL", settings.tts.base_url)
        assign("SANTISZR_TTS_MODEL_NAME", settings.tts.model_name)
        assign("SANTISZR_TTS_PROMPT_MAX_SECONDS", settings.tts.prompt_max_seconds)
        assign("SANTISZR_TTS_INSTRUCT_TEXT", settings.tts.instruct_text)
        assign("SANTISZR_TTS_PREFER_FP16", settings.tts.prefer_fp16)
        assign("SANTISZR_TTS_VOXCPM_CFG_VALUE", settings.tts.voxcpm_cfg_value)
        assign("SANTISZR_TTS_VOXCPM_INFERENCE_TIMESTEPS", settings.tts.voxcpm_inference_timesteps)
        assign("SANTISZR_TTS_VOXCPM_RETRY_BADCASE", settings.tts.voxcpm_retry_badcase)
        assign("SANTISZR_VOXCPM_PYTHON", self.project_root / "tools" / "voxcpm_python" / "python.exe")
        return env
