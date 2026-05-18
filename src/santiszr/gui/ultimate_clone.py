from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from santiszr.app import AppContext
from santiszr.infra.transcription import WhisperTranscriber


def cached_ultimate_clone_prompt_text(app_context: AppContext, reference_audio_path: str) -> str | None:
    audio_path = Path(reference_audio_path).expanduser().resolve()
    if not audio_path.exists() or not audio_path.is_file():
        return None
    cache_key = app_context.state.reference_transcript_key(str(audio_path))
    cached = app_context.state.reference_transcript_cache.get(cache_key, "").strip()
    return cached or None


def resolve_ultimate_clone_prompt_text(app_context: AppContext, reference_audio_path: str) -> str:
    audio_path = Path(reference_audio_path).expanduser().resolve()
    if not audio_path.exists() or not audio_path.is_file():
        raise RuntimeError(f"参考音频不存在：{audio_path}")

    cache_key = app_context.state.reference_transcript_key(str(audio_path))
    cached = app_context.state.reference_transcript_cache.get(cache_key, "").strip()
    if cached:
        return cached

    transcriber = getattr(app_context.services.content, "transcriber", None)
    if transcriber is None:
        transcriber = WhisperTranscriber()

    ensure_ready = getattr(transcriber, "ensure_ready", None)
    if callable(ensure_ready):
        ensure_ready()

    transcript = str(transcriber.transcribe(str(audio_path)) or "").strip()
    if not transcript:
        raise RuntimeError("参考音频文字识别为空，无法启用极致克隆。")

    app_context.state.reference_transcript_cache[cache_key] = transcript
    return transcript


class UltimateClonePromptWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, app_context: AppContext, reference_audio_path: str) -> None:
        super().__init__()
        self._app_context = app_context
        self._reference_audio_path = reference_audio_path

    def run(self) -> None:
        try:
            prompt_text = resolve_ultimate_clone_prompt_text(self._app_context, self._reference_audio_path)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(prompt_text)
        finally:
            self.finished.emit()


class _UltimateClonePromptCallbackDispatcher(QObject):
    succeeded = Signal(str)
    failed = Signal(str)


def prepare_ultimate_clone_prompt_text_async(
    owner: QObject,
    app_context: AppContext,
    reference_audio_path: str,
    *,
    on_ready: Callable[[str], None],
    on_failed: Callable[[str], None],
) -> None:
    cached = cached_ultimate_clone_prompt_text(app_context, reference_audio_path)
    if cached:
        on_ready(cached)
        return

    thread = QThread(owner)
    dispatcher = _UltimateClonePromptCallbackDispatcher(owner)
    worker = UltimateClonePromptWorker(app_context, reference_audio_path)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.succeeded.connect(dispatcher.succeeded)
    worker.failed.connect(dispatcher.failed)
    dispatcher.succeeded.connect(on_ready)
    dispatcher.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(dispatcher.deleteLater)

    active_workers = getattr(owner, "_ultimate_clone_prompt_workers", None)
    if active_workers is None:
        active_workers = []
        setattr(owner, "_ultimate_clone_prompt_workers", active_workers)
    active_workers.append((thread, worker, dispatcher))

    def _forget_worker() -> None:
        try:
            active_workers.remove((thread, worker, dispatcher))
        except ValueError:
            pass

    thread.finished.connect(_forget_worker)
    thread.start()
