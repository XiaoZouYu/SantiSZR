from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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
from santiszr.domain.schemas.subtitle import SubtitleRequest
from santiszr.gui.i18n import status_text, task_kind_text
from santiszr.gui.state.session import PipelineState
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerTaskKind


class SubtitlePage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("字幕")
        title.setObjectName("pageTitle")
        desc = QLabel("后台异步生成字幕，并可选择烧录到视频中。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        form = QFormLayout()
        self._workspace_input = QLineEdit(app_context.state.workspace)
        self._audio_input = QLineEdit(app_context.state.audio_path)
        self._video_input = QLineEdit(app_context.state.source_video_path)
        self._text_input = QPlainTextEdit()
        self._text_input.setPlainText(app_context.state.rewritten_text)
        self._text_input.setPlaceholderText("用于字幕切分的参考文本。")
        self._burn_in = QCheckBox("将字幕烧录到视频输出")
        self._burn_in.setChecked(True)

        fill_audio = QPushButton("使用最近音频")
        fill_audio.clicked.connect(lambda: self._audio_input.setText(self._app_context.state.audio_path))
        fill_text = QPushButton("使用最近改写")
        fill_text.clicked.connect(lambda: self._text_input.setPlainText(self._app_context.state.rewritten_text))
        self._run_button = QPushButton("生成字幕")
        self._run_button.clicked.connect(self._generate_subtitle)

        actions = QHBoxLayout()
        actions.addWidget(fill_audio)
        actions.addWidget(fill_text)
        actions.addWidget(self._run_button)
        self._result_view = QPlainTextEdit()
        self._result_view.setReadOnly(True)

        form.addRow("工作区", self._workspace_input)
        form.addRow("音频路径", self._audio_input)
        form.addRow("视频路径", self._video_input)
        form.addRow("参考文本", self._text_input)
        form.addRow("", self._burn_in)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self._result_view)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def _generate_subtitle(self) -> None:
        audio_path = self._audio_input.text().strip()
        if not audio_path:
            self._result_view.setPlainText("请输入音频路径。")
            return

        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._result_view.setPlainText(str(exc))
            return
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.subtitle,
            SubtitleRequest(
                audio_path=audio_path,
                video_path=self._video_input.text().strip() or None,
                reference_text=self._text_input.toPlainText().strip() or None,
                burn_in=self._burn_in.isChecked(),
                workspace=workspace,
                output_name="subtitle-page",
            ),
        )

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        self._run_button.setEnabled(not state.is_running)
        lines = [
            f"状态：{status_text(state.status)}",
            f"任务：{task_kind_text(state.active_task_kind or state.last_task_kind)}",
            f"进度：{int(state.progress * 100)}%",
            f"字幕：{state.subtitle_path or '无'}",
            f"视频：{state.final_video_path or '无'}",
        ]
        if state.last_error:
            lines.append(f"错误：{state.last_error}")
        self._result_view.setPlainText("\n".join(lines))
