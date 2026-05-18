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
from santiszr.domain.schemas.content import ContentRequest, VideoSource
from santiszr.gui.i18n import rewrite_mode_text, status_text, task_kind_text
from santiszr.gui.state.session import PipelineState
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerTaskKind


class CopywritingPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("文案")
        title.setObjectName("pageTitle")
        desc = QLabel("异步提取文案并执行改写，不阻塞界面。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        form = QFormLayout()
        self._source_input = QPlainTextEdit()
        self._source_input.setPlaceholderText("请输入源文本、链接或本地文件路径。")
        self._source_input.setPlainText(app_context.state.source_input)
        self._workspace_input = QLineEdit(app_context.state.workspace)
        self._mode_input = QComboBox()
        for mode in RewriteMode:
            self._mode_input.addItem(rewrite_mode_text(mode), mode)
        self._prompt_input = QLineEdit("保留事实信息，但把开头钩子写得更强。")
        self._extract_button = QPushButton("仅提取")
        self._extract_button.clicked.connect(self._extract_only)
        self._rewrite_button = QPushButton("提取并改写")
        self._rewrite_button.clicked.connect(self._extract_and_rewrite)

        action_row = QHBoxLayout()
        action_row.addWidget(self._extract_button)
        action_row.addWidget(self._rewrite_button)

        self._extracted_view = QPlainTextEdit()
        self._rewritten_view = QPlainTextEdit()
        self._meta_view = QPlainTextEdit()
        self._meta_view.setReadOnly(True)

        form.addRow("来源", self._source_input)
        form.addRow("工作区", self._workspace_input)
        form.addRow("改写模式", self._mode_input)
        form.addRow("改写提示词", self._prompt_input)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addWidget(QLabel("提取结果"))
        layout.addWidget(self._extracted_view)
        layout.addWidget(QLabel("改写结果"))
        layout.addWidget(self._rewritten_view)
        layout.addWidget(QLabel("״̬"))
        layout.addWidget(self._meta_view)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def _extract_only(self) -> None:
        source_text = self._source_input.toPlainText().strip()
        if not source_text:
            self._meta_view.setPlainText("请输入来源内容。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._meta_view.setPlainText(str(exc))
            return
        self._app_context.state.source_input = source_text
        request = ContentRequest(
            source=VideoSource(
                source_type=self._detect_source_type(source_text),
                raw_input=source_text,
            ),
            workspace=workspace,
        )
        self._app_context.task_controller.submit_task(WorkerTaskKind.content, request)

    def _extract_and_rewrite(self) -> None:
        source_text = self._source_input.toPlainText().strip()
        if not source_text:
            self._meta_view.setPlainText("请输入来源内容。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._meta_view.setPlainText(str(exc))
            return
        self._app_context.state.source_input = source_text
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.rewrite,
            {
                "source": VideoSource(
                    source_type=self._detect_source_type(source_text),
                    raw_input=source_text,
                ).model_dump(mode="json"),
                "workspace": workspace,
                "rewrite_mode": self._mode_input.currentData().value,
                "rewrite_prompt": self._prompt_input.text().strip() or None,
                "rewrite_model": self._app_context.settings.llm.model,
            },
        )

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        busy = state.is_running
        self._extract_button.setEnabled(not busy)
        self._rewrite_button.setEnabled(not busy)
        self._extracted_view.setPlainText(state.extracted_text)
        self._rewritten_view.setPlainText(state.rewritten_text)
        lines = [
            f"状态：{status_text(state.status)}",
            f"任务：{task_kind_text(state.active_task_kind or state.last_task_kind)}",
            f"进度：{int(state.progress * 100)}%",
            f"标题：{state.rewritten_title or '无'}",
            f"标签：{' '.join(state.tags) if state.tags else '无'}",
        ]
        if state.last_error:
            lines.append(f"错误：{state.last_error}")
        self._meta_view.setPlainText("\n".join(lines))

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
