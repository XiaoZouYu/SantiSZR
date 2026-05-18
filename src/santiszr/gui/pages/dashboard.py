from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from santiszr.app import AppContext
from santiszr.domain.schemas.audio import RewriteMode
from santiszr.domain.schemas.content import VideoSource
from santiszr.domain.schemas.publish import GenerateVideoWorkflowRequest
from santiszr.gui.i18n import rewrite_mode_text, stage_text, status_text, task_kind_text, voice_text
from santiszr.gui.state.session import PipelineState
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerTaskKind


class DashboardPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("工作台")
        title.setObjectName("pageTitle")
        desc = QLabel("在不阻塞界面的情况下运行完整 MVP 流程。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        form = QFormLayout()
        self._source_input = QPlainTextEdit()
        self._source_input.setPlaceholderText("请输入抖音分享文本、直接链接或本地媒体路径。")
        self._source_input.setPlainText(app_context.state.source_input)
        self._workspace_input = QLineEdit(app_context.state.workspace)
        self._voice_input = QComboBox()
        for voice in app_context.services.tts.client.list_voices():
            self._voice_input.addItem(voice_text(voice), voice)
        self._voice_input.setEditable(True)
        default_voice = app_context.settings.tts.default_voice
        current_index = self._voice_input.findData(default_voice)
        if current_index >= 0:
            self._voice_input.setCurrentIndex(current_index)
        else:
            self._voice_input.setEditText(default_voice)
        self._mode_input = QComboBox()
        for mode in RewriteMode:
            self._mode_input.addItem(rewrite_mode_text(mode), mode)
        self._prompt_input = QLineEdit("突出冲突、结果和下一步行动。")
        self._avatar_input = QLineEdit(app_context.settings.avatar.default_model_id)

        self._run_button = QPushButton("运行完整流程")
        self._run_button.clicked.connect(self._run_workflow)
        self._cancel_button = QPushButton("取消当前任务")
        self._cancel_button.clicked.connect(self._cancel_task)

        action_row = QHBoxLayout()
        action_row.addWidget(self._run_button)
        action_row.addWidget(self._cancel_button)

        self._result_view = QPlainTextEdit()
        self._result_view.setReadOnly(True)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)

        form.addRow("来源", self._source_input)
        form.addRow("工作区", self._workspace_input)
        form.addRow("改写模式", self._mode_input)
        form.addRow("改写提示词", self._prompt_input)
        form.addRow("音色", self._voice_input)
        form.addRow("数字人模型", self._avatar_input)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addWidget(QLabel("״̬"))
        layout.addWidget(self._result_view)
        layout.addWidget(QLabel("日志"))
        layout.addWidget(self._log_view, 1)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def _run_workflow(self) -> None:
        source_text = self._source_input.toPlainText().strip()
        selected_voice = self._voice_input.currentData()
        current_voice_text = self._voice_input.currentText().strip()
        voice = (
            str(selected_voice)
            if selected_voice and current_voice_text == voice_text(str(selected_voice))
            else current_voice_text
        )
        if not source_text:
            self._result_view.setPlainText("请输入来源内容。")
            return

        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._result_view.setPlainText(str(exc))
            return
        self._app_context.state.source_input = source_text
        request = GenerateVideoWorkflowRequest(
            source=VideoSource(
                source_type=self._detect_source_type(source_text),
                raw_input=source_text,
            ),
            rewrite_mode=self._mode_input.currentData(),
            rewrite_prompt=self._prompt_input.text().strip() or None,
            voice=voice,
            reference_audio_path=self._app_context.state.preferred_audio or None,
            avatar_model_id=self._avatar_input.text().strip(),
            workspace=workspace,
        )
        self._app_context.task_controller.submit_task(WorkerTaskKind.full_workflow, request)

    def _cancel_task(self) -> None:
        self._app_context.task_controller.cancel_active_task()

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        self._run_button.setEnabled(not state.is_running)
        self._cancel_button.setEnabled(state.is_running and state.is_cancellable)
        status_lines = [
            f"状态：{status_text(state.status)}",
            f"任务：{task_kind_text(state.active_task_kind or state.last_task_kind)}",
            f"阶段：{stage_text(state.active_stage)}",
            f"进度：{int(state.progress * 100)}%",
        ]
        if state.rewritten_title:
            status_lines.append(f"标题：{state.rewritten_title}")
        if state.tags:
            status_lines.append(f"标签：{' '.join(state.tags)}")
        if state.audio_path:
            status_lines.append(f"音频：{state.audio_path}")
        if state.subtitle_path:
            status_lines.append(f"字幕：{state.subtitle_path}")
        if state.final_video_path:
            status_lines.append(f"最终视频：{state.final_video_path}")
        if state.last_error:
            status_lines.append(f"错误：{state.last_error}")
        self._result_view.setPlainText("\n".join(status_lines))
        self._log_view.setPlainText("\n".join(state.logs[-200:]))

    def _detect_source_type(self, raw_input: str) -> str:
        source_path = Path(raw_input)
        if source_path.exists():
            suffix = source_path.suffix.lower()
            if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                return "local_video"
            if suffix in {".wav", ".mp3", ".m4a"}:
                return "local_audio"
        if "douyin.com" in raw_input or "iesdouyin.com" in raw_input:
            return "douyin_share_text"
        if raw_input.startswith(("http://", "https://")):
            return "url"
        return "raw_text"
