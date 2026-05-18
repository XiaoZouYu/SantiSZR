from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from santiszr.domain.schemas.avatar import AvatarEngine, AvatarRequest
from santiszr.gui.i18n import status_text, task_kind_text
from santiszr.gui.state.session import PipelineState
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerTaskKind


class AvatarPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("数字人")
        title.setObjectName("pageTitle")
        desc = QLabel("通过后台任务流水线渲染数字人视频。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        form = QFormLayout()
        self._workspace_input = QLineEdit(app_context.state.workspace)
        self._audio_input = QLineEdit(app_context.state.audio_path)
        self._subtitle_input = QLineEdit(app_context.state.subtitle_path)
        self._background_input = QLineEdit(app_context.state.source_video_path)
        self._model_input = QLineEdit(app_context.settings.avatar.default_model_id)
        self._run_button = QPushButton("渲染数字人视频")
        self._run_button.clicked.connect(self._render_avatar)

        fill_audio = QPushButton("使用最近音频")
        fill_audio.clicked.connect(lambda: self._audio_input.setText(self._app_context.state.audio_path))
        fill_subtitle = QPushButton("使用最近字幕")
        fill_subtitle.clicked.connect(lambda: self._subtitle_input.setText(self._app_context.state.subtitle_path))
        fill_background = QPushButton("使用源视频")
        fill_background.clicked.connect(lambda: self._background_input.setText(self._app_context.state.source_video_path))

        actions = QHBoxLayout()
        actions.addWidget(fill_audio)
        actions.addWidget(fill_subtitle)
        actions.addWidget(fill_background)
        actions.addWidget(self._run_button)
        self._result_view = QPlainTextEdit()
        self._result_view.setReadOnly(True)

        form.addRow("工作区", self._workspace_input)
        form.addRow("音频路径", self._audio_input)
        form.addRow("字幕路径", self._subtitle_input)
        form.addRow("背景视频", self._background_input)
        form.addRow("模型编号", self._model_input)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self._result_view)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def _render_avatar(self) -> None:
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
            WorkerTaskKind.avatar,
            AvatarRequest(
                audio_path=audio_path,
                model_id=self._model_input.text().strip(),
                engine=AvatarEngine.tuilionnx,
                workspace=workspace,
                subtitle_path=self._subtitle_input.text().strip() or None,
                background_video_path=self._background_input.text().strip() or None,
                overlay_text=self._app_context.state.rewritten_title or None,
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
            f"数字人视频：{state.avatar_video_path or '无'}",
            f"最终视频：{state.final_video_path or '无'}",
        ]
        if state.last_error:
            lines.append(f"错误：{state.last_error}")
        self._result_view.setPlainText("\n".join(lines))
