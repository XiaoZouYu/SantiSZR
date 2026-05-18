from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication

from santiszr.config.settings import AppSettings, AvatarSettings, LLMSettings, MediaSettings, ModelSettings, TTSSettings
from santiszr.domain.schemas.audio import AudioMeta, TTSResult
from santiszr.domain.schemas.common import TaskStatus
from santiszr.gui.state import PipelineState, TaskController
from santiszr.gui.state.task_controller import _sanitize_worker_environment
from santiszr.workers.protocol import WorkerEvent, WorkerEventType, WorkerTaskKind, WorkerTaskRequest


class FakeRunner:
    def __init__(self, task_id: str = "task-avatar", task_kind: WorkerTaskKind = WorkerTaskKind.avatar) -> None:
        self.was_cancelled = False
        self.cancel_requested = False
        self.task_request = WorkerTaskRequest(task_id=task_id, task_kind=task_kind, payload={})

    def cancel(self) -> None:
        self.was_cancelled = True
        self.cancel_requested = True


def _app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def test_task_controller_applies_success_event() -> None:
    _app()
    state = PipelineState(workspace="D:/tmp/test")
    controller = TaskController(state=state)
    state.begin_task("task-tts", WorkerTaskKind.tts.value)

    controller._handle_worker_event(
        WorkerEvent(
            event=WorkerEventType.succeeded,
            task_id="task-tts",
            task_kind=WorkerTaskKind.tts,
            stage="tts",
            progress=1.0,
            message="TTS finished.",
            payload={
                "tts": TTSResult(
                    success=True,
                    audio_path="D:/tmp/audio.wav",
                    meta=AudioMeta(duration_sec=1.0, sample_rate=22050, channels=1),
                    provider="test",
                ).model_dump(mode="json")
            },
        )
    )

    assert state.audio_path == "D:/tmp/audio.wav"
    assert state.status is TaskStatus.succeeded
    assert state.is_running is False


def test_task_controller_cancel_marks_state() -> None:
    _app()
    state = PipelineState(workspace="D:/tmp/test")
    controller = TaskController(state=state)
    state.begin_task("task-avatar", WorkerTaskKind.avatar.value)
    controller._active_runners["task-avatar"] = FakeRunner()

    cancelled = controller.cancel_active_task()

    assert cancelled is True
    assert controller._active_runners["task-avatar"].was_cancelled is True
    assert state.is_cancellable is False
    assert "已请求取消当前任务。" in state.logs[-1]


def test_task_controller_emits_failure_event_for_process_exit() -> None:
    _app()
    state = PipelineState(workspace="D:/tmp/test")
    controller = TaskController(state=state)
    state.begin_task("task-content", WorkerTaskKind.content.value)
    controller._active_runners["task-content"] = FakeRunner(
        task_id="task-content",
        task_kind=WorkerTaskKind.content,
    )

    events: list[WorkerEvent] = []
    controller.task_event.connect(events.append)

    controller._handle_worker_finished("task-content", 7, "traceback line")

    assert state.status is TaskStatus.failed
    assert state.last_error == "traceback line"
    assert events
    assert events[-1].event is WorkerEventType.failed
    assert events[-1].task_kind is WorkerTaskKind.content
    assert events[-1].error is not None
    assert events[-1].error.code == "worker_process_exit"
    assert events[-1].error.detail["exit_code"] == 7


def test_task_controller_builds_worker_env_from_current_settings() -> None:
    _app()
    settings = AppSettings(
        media=MediaSettings(
            ffmpeg_path=Path("D:/tools/ffmpeg/bin/ffmpeg.exe"),
            ffprobe_path=Path("D:/tools/ffmpeg/bin/ffprobe.exe"),
        ),
        models=ModelSettings(
            root_dir=Path("D:/models"),
            voxcpm_model_dir=Path("D:/models/voxcpm/VoxCPM2"),
            whisper_model_dir=Path("D:/models/whisper"),
            tuilionnx_model_dir=Path("D:/models/tuilionnx"),
        ),
        avatar=AvatarSettings(
            tuilionnx_root=Path("D:/models/tuilionnx"),
            tuilionnx_python=Path("D:/tools/cosyvoice_python/python.exe"),
        ),
        llm=LLMSettings(api_base="https://example.invalid/v1", model="demo-model", api_key="demo-key"),
        tts=TTSSettings(base_url="http://127.0.0.1:9880", provider="voxcpm2"),
    )
    controller = TaskController(
        state=PipelineState(workspace="D:/tmp/test"),
        app_settings=settings,
        project_root=Path("D:/repo"),
    )

    env = controller._worker_env_overrides()

    assert env["SANTISZR_FFMPEG_PATH"] == str(Path("D:/tools/ffmpeg/bin/ffmpeg.exe"))
    assert env["SANTISZR_FFPROBE_PATH"] == str(Path("D:/tools/ffmpeg/bin/ffprobe.exe"))
    assert env["SANTISZR_VOXCPM_MODEL_DIR"] == str(Path("D:/models/voxcpm/VoxCPM2"))
    assert env["SANTISZR_WHISPER_MODEL_DIR"] == str(Path("D:/models/whisper"))
    assert env["SANTISZR_TUILIONNX_MODEL_DIR"] == str(Path("D:/models/tuilionnx"))
    assert env["SANTISZR_TUILIONNX_ROOT"] == str(Path("D:/models/tuilionnx"))
    assert env["SANTISZR_TUILIONNX_PYTHON"] == str(Path("D:/tools/cosyvoice_python/python.exe"))
    assert env["SANTISZR_LLM_MODEL"] == "demo-model"
    assert env["SANTISZR_TTS_PROVIDER"] == "voxcpm2"
    assert env["SANTISZR_TTS_BASE_URL"] == "http://127.0.0.1:9880"
    assert env["SANTISZR_VOXCPM_PYTHON"] == str(Path("D:/repo/tools/voxcpm_python/python.exe"))


def test_sanitize_worker_environment_strips_conda_and_kmp_workarounds() -> None:
    source = {
        "PATH": ";".join(
            [
                r"C:\Users\Administrator\miniconda3",
                r"C:\Users\Administrator\miniconda3\Library\bin",
                r"C:\Users\Administrator\miniconda3\Scripts",
                r"D:\tools\ffmpeg\bin",
            ]
        ),
        "CONDA_PREFIX": r"C:\Users\Administrator\miniconda3",
        "CONDA_DEFAULT_ENV": "base",
        "KMP_DUPLICATE_LIB_OK": "TRUE",
        "KEEP_ME": "1",
    }

    sanitized = _sanitize_worker_environment(source)

    assert sanitized["PATH"] == r"D:\tools\ffmpeg\bin"
    assert "CONDA_PREFIX" not in sanitized
    assert "CONDA_DEFAULT_ENV" not in sanitized
    assert "KMP_DUPLICATE_LIB_OK" not in sanitized
    assert sanitized["KEEP_ME"] == "1"
