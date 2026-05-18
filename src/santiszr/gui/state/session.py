from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from santiszr.domain.schemas.common import TaskStatus


@dataclass(slots=True)
class AudioVariant:
    path: str
    label: str = ""
    voice: str = ""
    speed: float | None = None
    source: str = "generated"
    duration_sec: float | None = None


@dataclass(slots=True)
class PipelineState:
    workspace: str = ""
    preferred_voice: str = ""
    preferred_audio: str = ""
    preferred_avatar_model_id: str = ""
    preferred_reference_video: str = ""
    preferred_bgm: str = ""
    source_input: str = ""
    source_video_path: str = ""
    extracted_text: str = ""
    rewritten_text: str = ""
    rewritten_title: str = ""
    tags: list[str] = field(default_factory=list)
    audio_path: str = ""
    audio_variants: list[AudioVariant] = field(default_factory=list)
    selected_audio_variant_path: str = ""
    subtitle_path: str = ""
    avatar_video_path: str = ""
    final_video_path: str = ""
    last_error: str = ""
    last_task_kind: str = ""
    last_message: str = ""
    ultimate_clone_enabled: bool = False
    reference_transcript_cache: dict[str, str] = field(default_factory=dict)
    active_task_id: str = ""
    active_task_kind: str = ""
    active_stage: str = ""
    progress: float = 0.0
    status: TaskStatus = TaskStatus.pending
    logs: list[str] = field(default_factory=list)
    is_running: bool = False
    is_cancellable: bool = False

    def upsert_audio_variant(
        self,
        *,
        path: str,
        label: str = "",
        voice: str = "",
        speed: float | None = None,
        source: str = "generated",
        duration_sec: float | None = None,
        make_selected: bool = True,
    ) -> AudioVariant | None:
        normalized = path.strip()
        if not normalized:
            return None

        existing = next((item for item in self.audio_variants if item.path == normalized), None)
        if existing is None:
            existing = AudioVariant(path=normalized)
            self.audio_variants.insert(0, existing)
        else:
            self.audio_variants = [item for item in self.audio_variants if item.path != normalized]
            self.audio_variants.insert(0, existing)

        if label:
            existing.label = label
        if voice:
            existing.voice = voice
        if speed is not None:
            existing.speed = speed
        if source:
            existing.source = source
        if duration_sec is not None:
            existing.duration_sec = duration_sec

        if make_selected:
            self.select_audio_variant(normalized, preferred_audio=source != "generated")
        return existing

    def select_audio_variant(self, path: str, *, preferred_audio: bool) -> None:
        normalized = path.strip()
        if not normalized:
            self.audio_path = ""
            self.selected_audio_variant_path = ""
            if preferred_audio:
                self.preferred_audio = ""
            return

        if not any(item.path == normalized for item in self.audio_variants):
            self.audio_variants.insert(0, AudioVariant(path=normalized, label=normalized, source="library"))

        self.audio_path = normalized
        self.selected_audio_variant_path = normalized
        self.preferred_audio = normalized if preferred_audio else ""

    def remove_audio_variant(self, path: str) -> None:
        normalized = path.strip()
        if not normalized:
            return
        self.audio_variants = [item for item in self.audio_variants if item.path != normalized]
        if self.selected_audio_variant_path == normalized:
            next_path = self.audio_variants[0].path if self.audio_variants else ""
            self.selected_audio_variant_path = next_path
            self.audio_path = next_path
        if self.audio_path == normalized:
            self.audio_path = self.selected_audio_variant_path
        if self.preferred_audio == normalized:
            self.preferred_audio = ""

    def current_audio_variant(self) -> AudioVariant | None:
        current_path = self.selected_audio_variant_path or self.audio_path
        if not current_path:
            return None
        return next((item for item in self.audio_variants if item.path == current_path), None)

    def reference_transcript_key(self, audio_path: str) -> str:
        path = Path(audio_path).expanduser().resolve()
        stat = path.stat()
        return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"

    def begin_task(self, task_id: str, task_kind: str) -> None:
        self.active_task_id = task_id
        self.active_task_kind = task_kind
        self.active_stage = ""
        self.progress = 0.0
        self.status = TaskStatus.running
        self.is_running = True
        self.is_cancellable = True
        self.last_error = ""
        self.last_message = ""
        self.logs.clear()

    def update_progress(self, *, stage: str, progress: float, message: str | None = None) -> None:
        self.active_stage = stage
        self.progress = max(0.0, min(progress, 1.0))
        self.status = TaskStatus.running
        if message:
            self.last_message = message

    def append_log(self, message: str) -> None:
        if message:
            self.logs.append(message)
            self.last_message = message

    def complete_task(self, *, task_kind: str, status: TaskStatus, message: str | None = None) -> None:
        self.last_task_kind = task_kind
        self.status = status
        self.is_running = False
        self.is_cancellable = False
        if status is TaskStatus.succeeded:
            self.progress = 1.0
        if message:
            self.last_message = message
            self.logs.append(message)
        self.active_task_id = ""
        self.active_task_kind = ""
        self.active_stage = ""

    def fail_task(self, *, task_kind: str, message: str) -> None:
        self.last_error = message
        self.complete_task(task_kind=task_kind, status=TaskStatus.failed, message=message)

    def cancel_task(self, *, task_kind: str, message: str) -> None:
        self.complete_task(task_kind=task_kind, status=TaskStatus.cancelled, message=message)
