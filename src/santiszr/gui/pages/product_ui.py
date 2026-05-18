from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QTextOption

from santiszr.app import AppContext
from santiszr.config.settings import AppSettings
from santiszr.core.asset_library import AssetCategory
from santiszr.core.diagnostics import format_diagnostic_report, run_startup_diagnostics
from santiszr.domain.schemas.audio import RewriteMode, RewriteRequest, TTSRequest
from santiszr.domain.schemas.avatar import AvatarEngine, AvatarRequest
from santiszr.domain.schemas.common import TaskStatus
from santiszr.domain.schemas.content import ContentRequest, VideoSource
from santiszr.domain.schemas.publish import GenerateVideoWorkflowRequest
from santiszr.gui.i18n import (
    display_text,
    rewrite_mode_text,
    stage_text,
    status_text,
    task_kind_text,
    voice_text,
)
from santiszr.gui.state.session import PipelineState
from santiszr.gui.ultimate_clone import (
    cached_ultimate_clone_prompt_text,
    prepare_ultimate_clone_prompt_text_async,
)
from santiszr.gui.workspace import ensure_workspace as ensure_selected_workspace
from santiszr.workers.protocol import WorkerTaskKind


VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a"}


def detect_source_type(raw_input: str) -> str:
    source_path = Path(raw_input)
    if source_path.exists():
        suffix = source_path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            return "local_video"
        if suffix in AUDIO_SUFFIXES:
            return "local_audio"
    if "douyin.com" in raw_input or "iesdouyin.com" in raw_input:
        return "douyin_share_text"
    if raw_input.startswith(("http://", "https://")):
        return "url"
    return "raw_text"


def ensure_workspace(app_context: AppContext, raw_workspace: str) -> str:
    return ensure_selected_workspace(app_context, raw_workspace)


def current_voice(app_context: AppContext) -> str:
    return app_context.state.preferred_voice or app_context.settings.tts.default_voice


def current_avatar_model(app_context: AppContext) -> str:
    return app_context.state.preferred_avatar_model_id or app_context.settings.avatar.default_model_id


def resolve_tts_clone_options(app_context: AppContext, reference_audio_path: str | None) -> tuple[bool, str | None]:
    reference_audio = (reference_audio_path or "").strip()
    if not app_context.state.ultimate_clone_enabled or not reference_audio:
        return False, None
    return True, cached_ultimate_clone_prompt_text(app_context, reference_audio)


def list_voices(app_context: AppContext) -> list[str]:
    voices: list[str] = []
    try:
        voices = list(app_context.services.tts.client.list_voices())
    except Exception:
        voices = []
    fallback = current_voice(app_context)
    if fallback and fallback not in voices:
        voices.append(fallback)
    return voices or ["neutral"]


def list_avatar_models(app_context: AppContext) -> list[str]:
    models = {current_avatar_model(app_context)}
    root = app_context.settings.avatar.tuilionnx_root
    if root and root.exists():
        for child in root.iterdir():
            if child.is_dir():
                models.add(child.name)
            elif child.is_file() and child.suffix.lower() in {".onnx", ".bin", ".ckpt"}:
                models.add(child.stem)
    return sorted(model for model in models if model)


def list_reference_videos(app_context: AppContext) -> list[str]:
    return app_context.media_library.list_paths(AssetCategory.reference_video)


def _unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def fill_voice_combo(
    combo: QComboBox,
    app_context: AppContext,
    selected: str | None = None,
    *,
    voices: list[str] | None = None,
) -> None:
    current_value = (selected or combo.currentText() or current_voice(app_context)).strip()
    combo.blockSignals(True)
    combo.clear()
    for voice in _unique_values(voices if voices is not None else list_voices(app_context)):
        combo.addItem(f"{voice_text(voice)} / {voice}", voice)
    combo.setEditable(True)
    _set_combo_value(combo, current_value)
    combo.blockSignals(False)


def fill_avatar_combo(
    combo: QComboBox,
    app_context: AppContext,
    selected: str | None = None,
    *,
    avatar_models: list[str] | None = None,
) -> None:
    current_value = (selected or combo.currentText() or current_avatar_model(app_context)).strip()
    combo.blockSignals(True)
    combo.clear()
    for model_id in _unique_values(
        avatar_models if avatar_models is not None else list_avatar_models(app_context)
    ):
        combo.addItem(model_id, model_id)
    combo.setEditable(True)
    _set_combo_value(combo, current_value)
    combo.blockSignals(False)


def fill_reference_combo(
    combo: QComboBox,
    app_context: AppContext,
    selected: str | None = None,
    *,
    reference_videos: list[str] | None = None,
) -> None:
    current_value = (
        selected or combo.currentText() or app_context.state.preferred_reference_video or ""
    ).strip()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("自动使用导入视频", "")
    for path in _unique_values(
        reference_videos if reference_videos is not None else list_reference_videos(app_context)
    ):
        combo.addItem(Path(path).name, path)
    combo.setEditable(True)
    _set_combo_value(combo, current_value)
    combo.blockSignals(False)


def _set_combo_value(combo: QComboBox, value: str) -> None:
    if not value:
        combo.setCurrentIndex(0)
        return
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    combo.setEditText(value)


def combo_value(combo: QComboBox) -> str:
    index = combo.currentIndex()
    text = combo.currentText().strip()
    if index >= 0 and text == combo.itemText(index).strip():
        data = combo.itemData(index)
        if data is not None:
            return str(data).strip()
    return text


def file_details(path_text: str) -> str:
    path = Path(path_text)
    if not path.exists():
        return f"路径：{path_text}\n状态：文件不存在"
    stat = path.stat()
    size_mb = stat.st_size / (1024 * 1024)
    updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    return f"路径：{path}\n大小：{size_mb:.2f} MB\n更新时间：{updated}"


@dataclass(slots=True)
class SettingsOptionSnapshot:
    voices: list[str]
    avatar_models: list[str]
    reference_videos: list[str]


class SettingsOptionLoader(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, app_context: AppContext) -> None:
        super().__init__()
        self._app_context = app_context

    def run(self) -> None:
        try:
            self.loaded.emit(
                SettingsOptionSnapshot(
                    voices=list_voices(self._app_context),
                    avatar_models=list_avatar_models(self._app_context),
                    reference_videos=list_reference_videos(self._app_context),
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ResultPanel(QFrame):
    def __init__(self, app_context: AppContext, *, show_logs: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_logs = show_logs
        self.setObjectName("panelCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("结果与进度")
        title.setObjectName("sectionTitle")

        steps_row = QHBoxLayout()
        steps_row.setSpacing(8)
        self._step_labels: list[QLabel] = []
        for text in ["导入", "改写", "配音", "数字人", "完成"]:
            label = QLabel(text)
            label.setProperty("phase", "idle")
            label.setObjectName("progressStep")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_labels.append(label)
            steps_row.addWidget(label)

        self._status_label = QLabel()
        self._status_label.setObjectName("statusHeadline")
        self._message_label = QLabel()
        self._message_label.setObjectName("mutedText")
        self._message_label.setWordWrap(True)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)

        self._summary_view = QPlainTextEdit()
        self._summary_view.setReadOnly(True)
        self._summary_view.setMaximumHeight(220)

        layout.addWidget(title)
        layout.addLayout(steps_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._message_label)
        layout.addWidget(self._progress_bar)

        self._log_view: QPlainTextEdit | None = None
        if self._show_logs:
            log_label = QLabel("简要日志")
            log_label.setObjectName("sectionCaption")
            self._log_view = QPlainTextEdit()
            self._log_view.setReadOnly(True)
            self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._log_view.setWordWrapMode(QTextOption.WrapMode.NoWrap)
            self._log_view.setMaximumHeight(160)
            layout.addWidget(log_label)
            layout.addWidget(self._log_view)

        result_label = QLabel("结果")
        result_label.setObjectName("sectionCaption")
        layout.addWidget(result_label)
        layout.addWidget(self._summary_view)

        self.sync_state(app_context.state)

    def sync_state(self, state: PipelineState) -> None:
        self._status_label.setText(
            f"{status_text(state.status)} · {task_kind_text(state.active_task_kind or state.last_task_kind)}"
        )
        self._message_label.setText(state.last_message or "等待启动新任务。")
        self._progress_bar.setValue(int(state.progress * 100))

        active_step = self._active_step_index(state)
        complete_all = state.status is TaskStatus.succeeded and bool(state.final_video_path or state.avatar_video_path)
        for index, label in enumerate(self._step_labels):
            phase = "idle"
            if complete_all and index < 4:
                phase = "done"
            elif complete_all and index == 4:
                phase = "active"
            elif active_step >= 0 and index < active_step:
                phase = "done"
            elif index == active_step:
                phase = "active"
            label.setProperty("phase", phase)
            label.style().unpolish(label)
            label.style().polish(label)

        summary_lines = [
            f"工作区：{display_text(state.workspace)}",
            f"当前阶段：{stage_text(state.active_stage)}",
            f"标题：{display_text(state.rewritten_title)}",
            f"音频：{display_text(state.audio_path)}",
            f"字幕：{display_text(state.subtitle_path)}",
            f"数字人：{display_text(state.avatar_video_path)}",
            f"成品：{display_text(state.final_video_path)}",
        ]
        preview_text = state.rewritten_text or state.extracted_text
        if preview_text:
            preview = preview_text.strip().replace("\n", " ")
            summary_lines.append(f"文案预览：{preview[:120]}{'...' if len(preview) > 120 else ''}")
        if state.last_error:
            summary_lines.append(f"错误：{state.last_error}")
        self._summary_view.setPlainText("\n".join(summary_lines))

        if self._log_view is not None:
            self._log_view.setPlainText("\n".join(state.logs[-80:]))

    def _active_step_index(self, state: PipelineState) -> int:
        stage = (state.active_stage or "").strip().lower()
        if stage == "content":
            return 0
        if stage == "rewrite":
            return 1
        if stage in {"tts", "subtitle"}:
            return 2
        if stage == "avatar":
            return 3
        if state.status is TaskStatus.succeeded and (state.final_video_path or state.avatar_video_path):
            return 4
        return -1



class QuickGeneratePage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token = 0
        self._run_button_default_text = "一键生成"
        self._run_button_prepare_text = "识别中..."
        self._run_button_running_text = "生成中..."

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        left = QFrame()
        left.setObjectName("panelCard")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(24, 24, 24, 24)
        left_layout.setSpacing(16)

        badge = QLabel("默认首页")
        badge.setObjectName("eyebrow")
        title = QLabel("一键生成")
        title.setObjectName("pageTitle")
        desc = QLabel("输入内容后直接启动完整流程，参数只保留前期最必要的部分。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        self._source_input = QPlainTextEdit()
        self._source_input.setPlaceholderText("粘贴视频链接、分享文案或原始输入。")
        self._source_input.setPlainText(app_context.state.source_input)
        self._source_input.setMinimumHeight(180)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self._voice_input = QComboBox()
        self._mode_input = QComboBox()
        for mode in RewriteMode:
            self._mode_input.addItem(rewrite_mode_text(mode), mode)
        self._avatar_input = QComboBox()
        self._reference_input = QComboBox()

        fill_voice_combo(self._voice_input, app_context, current_voice(app_context))
        fill_avatar_combo(self._avatar_input, app_context, current_avatar_model(app_context))
        fill_reference_combo(self._reference_input, app_context, app_context.state.preferred_reference_video)

        grid.addWidget(QLabel("语音"), 0, 0)
        grid.addWidget(self._voice_input, 1, 0)
        grid.addWidget(QLabel("改写方式"), 0, 1)
        grid.addWidget(self._mode_input, 1, 1)
        grid.addWidget(QLabel("数字人形象"), 2, 0)
        grid.addWidget(self._avatar_input, 3, 0)
        grid.addWidget(QLabel("参考视频"), 2, 1)
        grid.addWidget(self._reference_input, 3, 1)

        self._toggle_more = QToolButton()
        self._toggle_more.setText("更多设置")
        self._toggle_more.setCheckable(True)
        self._toggle_more.setChecked(False)

        self._advanced_box = QFrame()
        self._advanced_box.setObjectName("subCard")
        self._advanced_box.setVisible(False)
        advanced_layout = QFormLayout(self._advanced_box)
        advanced_layout.setContentsMargins(16, 16, 16, 16)
        advanced_layout.setSpacing(10)
        self._workspace_input = QLineEdit(app_context.state.workspace)
        self._prompt_input = QLineEdit("突出冲突、结果和下一步行动。")
        self._speed_input = QDoubleSpinBox()
        self._speed_input.setRange(0.5, 2.0)
        self._speed_input.setSingleStep(0.1)
        self._speed_input.setValue(1.0)
        self._subtitle_checkbox = QCheckBox("数字人阶段带字幕")
        self._subtitle_checkbox.setChecked(True)
        advanced_layout.addRow("工作区", self._workspace_input)
        advanced_layout.addRow("补充提示", self._prompt_input)
        advanced_layout.addRow("语速", self._speed_input)
        advanced_layout.addRow("", self._subtitle_checkbox)
        self._toggle_more.clicked.connect(self._advanced_box.setVisible)

        actions = QHBoxLayout()
        self._run_button = QPushButton("一键生成")
        self._run_button.setObjectName("primaryButton")
        self._run_button.clicked.connect(self._run_workflow)
        self._cancel_button = QPushButton("取消任务")
        self._cancel_button.setObjectName("subtleButton")
        self._cancel_button.clicked.connect(self._app_context.task_controller.cancel_active_task)
        actions.addWidget(self._run_button)
        actions.addWidget(self._cancel_button)
        actions.addStretch(1)

        left_layout.addWidget(badge)
        left_layout.addWidget(title)
        left_layout.addWidget(desc)
        left_layout.addWidget(self._source_input)
        left_layout.addLayout(grid)
        left_layout.addWidget(self._toggle_more)
        left_layout.addWidget(self._advanced_box)
        left_layout.addLayout(actions)
        left_layout.addStretch(1)

        self._result_panel = ResultPanel(app_context, show_logs=True)
        self._result_panel.setMinimumWidth(500)

        layout.addWidget(left, 3)
        layout.addWidget(self._result_panel, 2)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def refresh_options(self) -> None:
        fill_voice_combo(self._voice_input, self._app_context)
        fill_avatar_combo(self._avatar_input, self._app_context)
        fill_reference_combo(self._reference_input, self._app_context)

    def _refresh_run_button_state(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._run_button.setEnabled(False)
            self._run_button.setText(self._run_button_prepare_text)
            return
        if self._app_context.state.is_running:
            self._run_button.setEnabled(False)
            self._run_button.setText(self._run_button_running_text)
            return
        self._run_button.setEnabled(True)
        self._run_button.setText(self._run_button_default_text)

    def _finish_ultimate_clone_prepare(self, token: int) -> bool:
        if token != self._ultimate_clone_prepare_token:
            return False
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token += 1
        self._refresh_run_button_state()
        return True

    def _begin_ultimate_clone_prepare(self, reference_audio_path: str) -> None:
        self._ultimate_clone_prepare_token += 1
        token = self._ultimate_clone_prepare_token
        self._ultimate_clone_prepare_in_progress = True
        self._refresh_run_button_state()
        self._result_panel._summary_view.setPlainText("正在识别参考音频文字，用于精准匹配...")
        prepare_ultimate_clone_prompt_text_async(
            self,
            self._app_context,
            str(reference_audio_path or ""),
            on_ready=lambda _prepared_text: self._handle_ultimate_clone_prompt_ready(token),
            on_failed=lambda message: self._handle_ultimate_clone_prompt_error(token, message),
        )

    def _handle_ultimate_clone_prompt_ready(self, token: int) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._run_workflow()

    def _handle_ultimate_clone_prompt_error(self, token: int, message: str) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._result_panel._summary_view.setPlainText(f"极致克隆准备失败：{message}")

    def _run_workflow(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._result_panel._summary_view.setPlainText("正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._result_panel._summary_view.setPlainText("已有任务正在运行，请稍候或取消当前任务。")
            return

        source_text = self._source_input.toPlainText().strip()
        if not source_text:
            self._result_panel._summary_view.setPlainText("请输入链接、分享文案或原始输入。")
            return

        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._result_panel._summary_view.setPlainText(str(exc))
            return
        self._app_context.state.source_input = source_text
        reference_audio_path = self._app_context.state.preferred_audio or None
        ultimate_clone, prompt_text = resolve_tts_clone_options(self._app_context, reference_audio_path)
        if ultimate_clone and not prompt_text:
            self._begin_ultimate_clone_prepare(str(reference_audio_path or ""))
            return

        self._submit_workflow_request(
            source_text=source_text,
            workspace=workspace,
            reference_audio_path=reference_audio_path,
            ultimate_clone=ultimate_clone,
            prompt_text=prompt_text,
        )

    def _submit_workflow_request(
        self,
        *,
        source_text: str,
        workspace: str,
        reference_audio_path: str | None,
        ultimate_clone: bool,
        prompt_text: str | None,
    ) -> str:
        self._refresh_run_button_state()
        self._run_button.setEnabled(False)
        self._run_button.setText(self._run_button_running_text)
        request = GenerateVideoWorkflowRequest(
            source=VideoSource(source_type=detect_source_type(source_text), raw_input=source_text),
            rewrite_mode=self._mode_input.currentData(),
            rewrite_prompt=self._prompt_input.text().strip() or None,
            rewrite_model=self._app_context.settings.llm.model,
            voice=combo_value(self._voice_input) or current_voice(self._app_context),
            reference_audio_path=reference_audio_path,
            ultimate_clone=ultimate_clone,
            prompt_text=prompt_text,
            voice_speed=float(self._speed_input.value()),
            avatar_model_id=combo_value(self._avatar_input) or current_avatar_model(self._app_context),
            subtitle_burn_in=self._subtitle_checkbox.isChecked(),
            reference_video_path=combo_value(self._reference_input) or None,
            workspace=workspace,
        )
        task_id = self._app_context.task_controller.submit_task(WorkerTaskKind.full_workflow, request)
        if not task_id:
            self._result_panel._summary_view.setPlainText(
                self._app_context.state.last_error or "工作流任务提交失败，请稍后重试。"
            )
            self._refresh_run_button_state()
        return task_id

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        self._refresh_run_button_state()
        self._cancel_button.setEnabled(state.is_running and state.is_cancellable)
        if self._ultimate_clone_prepare_in_progress and not state.is_running:
            return
        self._result_panel.sync_state(state)


class StepWorkflowPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token = 0
        self._tts_button_default_text = "生成语音"
        self._tts_button_prepare_text = "识别中..."
        self._tts_button_running_text = "生成中..."

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        title = QLabel("分步制作")
        title.setObjectName("pageTitle")
        desc = QLabel("按导入、改写、配音、数字人四步推进，每步都可直接复用已有结果。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        root.addWidget(title)
        root.addWidget(desc)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_body = QWidget()
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(14)

        workspace_card = QFrame()
        workspace_card.setObjectName("panelCard")
        workspace_layout = QFormLayout(workspace_card)
        workspace_layout.setContentsMargins(20, 20, 20, 20)
        self._workspace_input = QLineEdit(app_context.state.workspace)
        workspace_layout.addRow("工作区", self._workspace_input)
        scroll_layout.addWidget(workspace_card)

        self._source_input = QPlainTextEdit()
        self._source_input.setPlaceholderText("导入视频链接、分享文案或本地文件路径。")
        self._source_input.setPlainText(app_context.state.source_input)
        self._import_button = QPushButton("开始导入")
        self._import_button.setObjectName("primaryButton")
        self._import_button.clicked.connect(self._run_import)
        scroll_layout.addWidget(
            self._build_card("1. 导入视频", "提取源视频与文案，自动更新后续步骤输入。", self._source_input, self._import_button)
        )

        rewrite_body = QWidget()
        rewrite_layout = QVBoxLayout(rewrite_body)
        rewrite_layout.setContentsMargins(0, 0, 0, 0)
        rewrite_layout.setSpacing(10)
        self._rewrite_text_input = QPlainTextEdit()
        self._rewrite_text_input.setPlaceholderText("默认使用上一步提取文案，也可手动调整后再改写。")
        self._rewrite_text_input.setPlainText(app_context.state.extracted_text)
        self._rewrite_mode_input = QComboBox()
        for mode in RewriteMode:
            self._rewrite_mode_input.addItem(rewrite_mode_text(mode), mode)
        self._rewrite_prompt_input = QLineEdit("保留事实，但把开头钩子写得更强。")
        rewrite_form = QFormLayout()
        rewrite_form.addRow("改写方式", self._rewrite_mode_input)
        rewrite_form.addRow("补充提示", self._rewrite_prompt_input)
        fill_rewrite = QPushButton("使用导入结果")
        fill_rewrite.setObjectName("subtleButton")
        fill_rewrite.clicked.connect(self._fill_rewrite_text)
        self._rewrite_button = QPushButton("开始改写")
        self._rewrite_button.setObjectName("primaryButton")
        self._rewrite_button.clicked.connect(self._run_rewrite)
        rewrite_actions = QHBoxLayout()
        rewrite_actions.addWidget(fill_rewrite)
        rewrite_actions.addWidget(self._rewrite_button)
        rewrite_layout.addWidget(self._rewrite_text_input)
        rewrite_layout.addLayout(rewrite_form)
        rewrite_layout.addLayout(rewrite_actions)
        scroll_layout.addWidget(self._build_card("2. 文案改写", "支持直接使用导入结果，保留手动微调入口。", rewrite_body))

        voice_body = QWidget()
        voice_layout = QVBoxLayout(voice_body)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(10)
        self._tts_text_input = QPlainTextEdit()
        self._tts_text_input.setPlaceholderText("默认使用最近改写结果。")
        self._tts_text_input.setPlainText(app_context.state.rewritten_text or app_context.state.extracted_text)
        self._tts_voice_input = QComboBox()
        fill_voice_combo(self._tts_voice_input, app_context)
        self._tts_speed_input = QDoubleSpinBox()
        self._tts_speed_input.setRange(0.5, 2.0)
        self._tts_speed_input.setSingleStep(0.1)
        self._tts_speed_input.setValue(1.0)
        voice_form = QFormLayout()
        voice_form.addRow("语音", self._tts_voice_input)
        voice_form.addRow("语速", self._tts_speed_input)
        fill_tts_text = QPushButton("使用上一步文案")
        fill_tts_text.setObjectName("subtleButton")
        fill_tts_text.clicked.connect(self._fill_tts_text)
        self._tts_button = QPushButton("生成语音")
        self._tts_button.setObjectName("primaryButton")
        self._tts_button.clicked.connect(self._run_tts)
        voice_actions = QHBoxLayout()
        voice_actions.addWidget(fill_tts_text)
        voice_actions.addWidget(self._tts_button)
        voice_layout.addWidget(self._tts_text_input)
        voice_layout.addLayout(voice_form)
        voice_layout.addLayout(voice_actions)
        scroll_layout.addWidget(self._build_card("3. 语音生成", "优先带入最近改写文案，生成后结果会自动回填。", voice_body))

        avatar_body = QWidget()
        avatar_layout = QVBoxLayout(avatar_body)
        avatar_layout.setContentsMargins(0, 0, 0, 0)
        avatar_layout.setSpacing(10)
        avatar_form = QFormLayout()
        self._avatar_model_input = QComboBox()
        fill_avatar_combo(self._avatar_model_input, app_context)
        self._avatar_audio_input = QLineEdit(app_context.state.audio_path)
        self._avatar_subtitle_input = QLineEdit(app_context.state.subtitle_path)
        self._avatar_reference_input = QComboBox()
        fill_reference_combo(self._avatar_reference_input, app_context)
        avatar_form.addRow("数字人形象", self._avatar_model_input)
        avatar_form.addRow("音频路径", self._avatar_audio_input)
        avatar_form.addRow("字幕路径", self._avatar_subtitle_input)
        avatar_form.addRow("背景/参考视频", self._avatar_reference_input)
        fill_audio = QPushButton("使用最近音频")
        fill_audio.setObjectName("subtleButton")
        fill_audio.clicked.connect(lambda: self._avatar_audio_input.setText(self._app_context.state.audio_path))
        fill_subtitle = QPushButton("使用最近字幕")
        fill_subtitle.setObjectName("subtleButton")
        fill_subtitle.clicked.connect(lambda: self._avatar_subtitle_input.setText(self._app_context.state.subtitle_path))
        self._avatar_button = QPushButton("生成数字人")
        self._avatar_button.setObjectName("primaryButton")
        self._avatar_button.clicked.connect(self._run_avatar)
        avatar_actions = QHBoxLayout()
        avatar_actions.addWidget(fill_audio)
        avatar_actions.addWidget(fill_subtitle)
        avatar_actions.addWidget(self._avatar_button)
        avatar_layout.addLayout(avatar_form)
        avatar_layout.addLayout(avatar_actions)
        scroll_layout.addWidget(self._build_card("4. 数字人生成", "字幕作为可选输入弱化处理，重点保持生成入口直接。", avatar_body))
        scroll_layout.addStretch(1)

        scroll.setWidget(scroll_body)

        right = QFrame()
        right.setObjectName("panelCard")
        right.setMinimumWidth(520)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(12)
        self._result_panel = ResultPanel(app_context, show_logs=True)
        self._text_result_view = QPlainTextEdit()
        self._text_result_view.setReadOnly(True)
        self._text_result_view.setPlaceholderText("这里会显示导入文本与改写结果摘要。")
        right_layout.addWidget(self._result_panel)
        right_layout.addWidget(QLabel("文本结果"))
        right_layout.addWidget(self._text_result_view, 1)

        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 560])

        root.addWidget(splitter, 1)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def refresh_options(self) -> None:
        fill_voice_combo(self._tts_voice_input, self._app_context)
        fill_avatar_combo(self._avatar_model_input, self._app_context)
        fill_reference_combo(self._avatar_reference_input, self._app_context)

    def _build_card(
        self,
        title_text: str,
        desc_text: str,
        content: QWidget,
        action_button: QPushButton | None = None,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        desc = QLabel(desc_text)
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(content)
        if action_button is not None:
            layout.addWidget(action_button)
        return card

    def _run_import(self) -> None:
        source_text = self._source_input.toPlainText().strip()
        if not source_text:
            self._text_result_view.setPlainText("请输入要导入的内容。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._text_result_view.setPlainText(str(exc))
            return
        self._app_context.state.source_input = source_text
        request = ContentRequest(
            source=VideoSource(source_type=detect_source_type(source_text), raw_input=source_text),
            workspace=workspace,
        )
        self._app_context.task_controller.submit_task(WorkerTaskKind.content, request)

    def _fill_rewrite_text(self) -> None:
        self._rewrite_text_input.setPlainText(self._app_context.state.extracted_text or self._app_context.state.source_input)

    def _run_rewrite(self) -> None:
        text = self._rewrite_text_input.toPlainText().strip()
        if not text:
            self._text_result_view.setPlainText("请先导入内容，或直接输入待改写文本。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._text_result_view.setPlainText(str(exc))
            return
        request = RewriteRequest(
            text=text,
            mode=self._rewrite_mode_input.currentData(),
            prompt=self._rewrite_prompt_input.text().strip() or None,
            model=self._app_context.settings.llm.model,
            workspace=workspace,
        )
        self._app_context.task_controller.submit_task(WorkerTaskKind.rewrite_text, request)

    def _fill_tts_text(self) -> None:
        self._tts_text_input.setPlainText(self._app_context.state.rewritten_text or self._app_context.state.extracted_text)

    def _refresh_tts_button_state(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._tts_button.setEnabled(False)
            self._tts_button.setText(self._tts_button_prepare_text)
            return
        if self._app_context.state.is_running:
            self._tts_button.setEnabled(False)
            self._tts_button.setText(self._tts_button_running_text)
            return
        self._tts_button.setEnabled(True)
        self._tts_button.setText(self._tts_button_default_text)

    def _finish_ultimate_clone_prepare(self, token: int) -> bool:
        if token != self._ultimate_clone_prepare_token:
            return False
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token += 1
        self._refresh_tts_button_state()
        return True

    def _begin_ultimate_clone_prepare(self, reference_audio_path: str) -> None:
        self._ultimate_clone_prepare_token += 1
        token = self._ultimate_clone_prepare_token
        self._ultimate_clone_prepare_in_progress = True
        self._refresh_tts_button_state()
        self._text_result_view.setPlainText("正在识别参考音频文字，用于精准匹配...")
        prepare_ultimate_clone_prompt_text_async(
            self,
            self._app_context,
            str(reference_audio_path or ""),
            on_ready=lambda _prepared_text: self._handle_ultimate_clone_prompt_ready(token),
            on_failed=lambda message: self._handle_ultimate_clone_prompt_error(token, message),
        )

    def _handle_ultimate_clone_prompt_ready(self, token: int) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._run_tts()

    def _handle_ultimate_clone_prompt_error(self, token: int, message: str) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._text_result_view.setPlainText(f"极致克隆准备失败：{message}")

    def _run_tts(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._text_result_view.setPlainText("正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._text_result_view.setPlainText("已有任务正在运行，请稍候或取消当前任务。")
            return

        text = self._tts_text_input.toPlainText().strip()
        if not text:
            self._text_result_view.setPlainText("请先准备改写文案，再生成语音。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._text_result_view.setPlainText(str(exc))
            return
        reference_audio_path = self._app_context.state.preferred_audio or None
        ultimate_clone, prompt_text = resolve_tts_clone_options(self._app_context, reference_audio_path)
        if ultimate_clone and not prompt_text:
            self._begin_ultimate_clone_prepare(str(reference_audio_path or ""))
            return

        self._submit_tts_request(
            text=text,
            workspace=workspace,
            reference_audio_path=reference_audio_path,
            ultimate_clone=ultimate_clone,
            prompt_text=prompt_text,
        )

    def _submit_tts_request(
        self,
        *,
        text: str,
        workspace: str,
        reference_audio_path: str | None,
        ultimate_clone: bool,
        prompt_text: str | None,
    ) -> str:
        self._refresh_tts_button_state()
        self._tts_button.setEnabled(False)
        self._tts_button.setText(self._tts_button_running_text)
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=combo_value(self._tts_voice_input) or current_voice(self._app_context),
                reference_audio_path=reference_audio_path,
                ultimate_clone=ultimate_clone,
                prompt_text=prompt_text,
                speed=float(self._tts_speed_input.value()),
                workspace=workspace,
                output_name="step-voice",
            ),
        )
        if not task_id:
            self._text_result_view.setPlainText(self._app_context.state.last_error or "语音任务提交失败，请稍后重试。")
            self._refresh_tts_button_state()
        return task_id

    def _run_avatar(self) -> None:
        audio_path = self._avatar_audio_input.text().strip()
        if not audio_path:
            self._text_result_view.setPlainText("请先生成语音，或手动填入音频路径。")
            return
        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._text_result_view.setPlainText(str(exc))
            return
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.avatar,
            AvatarRequest(
                audio_path=audio_path,
                model_id=combo_value(self._avatar_model_input) or current_avatar_model(self._app_context),
                engine=AvatarEngine.tuilionnx,
                workspace=workspace,
                subtitle_path=self._avatar_subtitle_input.text().strip() or None,
                background_video_path=combo_value(self._avatar_reference_input) or None,
                overlay_text=self._app_context.state.rewritten_title or None,
            ),
        )

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        busy = state.is_running
        for button in (self._import_button, self._rewrite_button, self._avatar_button):
            button.setEnabled(not busy)
        self._refresh_tts_button_state()
        self._result_panel.sync_state(state)
        if state.audio_path and not self._avatar_audio_input.text().strip():
            self._avatar_audio_input.setText(state.audio_path)
        if state.subtitle_path and not self._avatar_subtitle_input.text().strip():
            self._avatar_subtitle_input.setText(state.subtitle_path)
        if self._ultimate_clone_prepare_in_progress and not state.is_running:
            return
        result_lines = []
        if state.extracted_text:
            result_lines.extend(["[导入结果]", state.extracted_text.strip(), ""])
        if state.rewritten_text:
            result_lines.extend(["[改写结果]", state.rewritten_text.strip()])
        self._text_result_view.setPlainText("\n".join(result_lines).strip())


class AssetManagementPage(QWidget):
    preferences_changed = Signal()

    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token = 0
        self._test_voice_button_default_text = "试听"
        self._test_voice_button_prepare_text = "识别中..."
        self._test_voice_button_running_text = "生成中..."

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("素材管理")
        title.setObjectName("pageTitle")
        desc = QLabel("把语音、参考视频和数字人形象集中到一个页面里，先做前期最需要的能力。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        tabs = QTabWidget()
        tabs.addTab(self._build_voice_tab(), "语音管理")
        tabs.addTab(self._build_reference_tab(), "参考视频管理")
        tabs.addTab(self._build_avatar_tab(), "数字人形象")
        layout.addWidget(tabs, 1)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def refresh_options(self) -> None:
        self._refresh_voice_list()
        self._refresh_reference_list()
        self._refresh_avatar_list()

    def _build_voice_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._voice_list = QListWidget()
        self._voice_list.currentItemChanged.connect(self._update_voice_info)
        side = QFrame()
        side.setObjectName("panelCard")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(20, 20, 20, 20)
        side_layout.setSpacing(12)
        self._voice_info = QPlainTextEdit()
        self._voice_info.setReadOnly(True)
        self._voice_sample_input = QLineEdit("这是一段语音试听文案。")
        refresh = QPushButton("刷新列表")
        refresh.setObjectName("subtleButton")
        refresh.clicked.connect(self._refresh_voice_list)
        set_default = QPushButton("设为默认")
        set_default.setObjectName("primaryButton")
        set_default.clicked.connect(self._set_default_voice)
        self._test_voice_button = QPushButton("试听")
        self._test_voice_button.setObjectName("subtleButton")
        self._test_voice_button.clicked.connect(self._test_voice)
        side_layout.addWidget(QLabel("当前语音"))
        side_layout.addWidget(self._voice_info)
        side_layout.addWidget(QLabel("试听文案"))
        side_layout.addWidget(self._voice_sample_input)
        side_layout.addWidget(refresh)
        side_layout.addWidget(set_default)
        side_layout.addWidget(self._test_voice_button)
        side_layout.addStretch(1)

        layout.addWidget(self._voice_list, 2)
        layout.addWidget(side, 3)
        self._refresh_voice_list()
        return tab

    def _build_reference_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._reference_list = QListWidget()
        self._reference_list.currentItemChanged.connect(self._update_reference_info)
        side = QFrame()
        side.setObjectName("panelCard")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(20, 20, 20, 20)
        side_layout.setSpacing(12)
        self._reference_info = QPlainTextEdit()
        self._reference_info.setReadOnly(True)
        refresh = QPushButton("刷新列表")
        refresh.setObjectName("subtleButton")
        refresh.clicked.connect(self._refresh_reference_list)
        set_default = QPushButton("设为默认")
        set_default.setObjectName("primaryButton")
        set_default.clicked.connect(self._set_default_reference)
        open_dir = QPushButton("打开目录")
        open_dir.setObjectName("subtleButton")
        open_dir.clicked.connect(self._open_selected_reference_dir)
        side_layout.addWidget(QLabel("视频信息"))
        side_layout.addWidget(self._reference_info)
        side_layout.addWidget(refresh)
        side_layout.addWidget(set_default)
        side_layout.addWidget(open_dir)
        side_layout.addStretch(1)

        layout.addWidget(self._reference_list, 2)
        layout.addWidget(side, 3)
        self._refresh_reference_list()
        return tab

    def _build_avatar_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._avatar_list = QListWidget()
        self._avatar_list.currentItemChanged.connect(self._update_avatar_info)
        side = QFrame()
        side.setObjectName("panelCard")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(20, 20, 20, 20)
        side_layout.setSpacing(12)
        self._avatar_info = QPlainTextEdit()
        self._avatar_info.setReadOnly(True)
        refresh = QPushButton("刷新列表")
        refresh.setObjectName("subtleButton")
        refresh.clicked.connect(self._refresh_avatar_list)
        set_default = QPushButton("设为默认")
        set_default.setObjectName("primaryButton")
        set_default.clicked.connect(self._set_default_avatar)
        side_layout.addWidget(QLabel("模型信息"))
        side_layout.addWidget(self._avatar_info)
        side_layout.addWidget(refresh)
        side_layout.addWidget(set_default)
        side_layout.addStretch(1)

        layout.addWidget(self._avatar_list, 2)
        layout.addWidget(side, 3)
        self._refresh_avatar_list()
        return tab

    def _refresh_voice_list(self) -> None:
        self._voice_list.clear()
        default_voice = current_voice(self._app_context)
        for voice in list_voices(self._app_context):
            item = QListWidgetItem(f"{voice_text(voice)} / {voice}")
            item.setData(Qt.ItemDataRole.UserRole, voice)
            if voice == default_voice:
                item.setText(f"{item.text()}  · 默认")
            self._voice_list.addItem(item)
        if self._voice_list.count():
            self._voice_list.setCurrentRow(0)

    def _refresh_reference_list(self) -> None:
        self._reference_list.clear()
        default_path = self._app_context.state.preferred_reference_video
        for path in list_reference_videos(self._app_context):
            label = Path(path).name
            if path == default_path:
                label = f"{label}  · 默认"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._reference_list.addItem(item)
        if self._reference_list.count():
            self._reference_list.setCurrentRow(0)
        else:
            self._reference_info.setPlainText("当前工作区还没有可用参考视频。")

    def _refresh_avatar_list(self) -> None:
        self._avatar_list.clear()
        default_model = current_avatar_model(self._app_context)
        for model_id in list_avatar_models(self._app_context):
            label = f"{model_id}  · 默认" if model_id == default_model else model_id
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, model_id)
            self._avatar_list.addItem(item)
        if self._avatar_list.count():
            self._avatar_list.setCurrentRow(0)

    def _update_voice_info(self, *_args: object) -> None:
        item = self._voice_list.currentItem()
        if not item:
            self._voice_info.setPlainText("未选择语音。")
            return
        voice = str(item.data(Qt.ItemDataRole.UserRole))
        is_default = voice == current_voice(self._app_context)
        self._voice_info.setPlainText(
            f"语音：{voice}\n显示名：{voice_text(voice)}\n默认：{'是' if is_default else '否'}"
        )

    def _update_reference_info(self, *_args: object) -> None:
        item = self._reference_list.currentItem()
        if not item:
            self._reference_info.setPlainText("未选择参考视频。")
            return
        path = str(item.data(Qt.ItemDataRole.UserRole))
        is_default = path == self._app_context.state.preferred_reference_video
        info = file_details(path)
        info += f"\n默认：{'是' if is_default else '否'}"
        self._reference_info.setPlainText(info)

    def _update_avatar_info(self, *_args: object) -> None:
        item = self._avatar_list.currentItem()
        if not item:
            self._avatar_info.setPlainText("未选择数字人模型。")
            return
        model_id = str(item.data(Qt.ItemDataRole.UserRole))
        root = self._app_context.settings.avatar.tuilionnx_root
        model_path = str((root / model_id).resolve()) if root else "未配置模型目录"
        is_default = model_id == current_avatar_model(self._app_context)
        self._avatar_info.setPlainText(f"模型：{model_id}\n目录：{model_path}\n默认：{'是' if is_default else '否'}")

    def _set_default_voice(self) -> None:
        item = self._voice_list.currentItem()
        if not item:
            return
        voice = str(item.data(Qt.ItemDataRole.UserRole))
        self._app_context.settings.tts.default_voice = voice
        self._app_context.state.preferred_voice = voice
        self._refresh_voice_list()
        self.preferences_changed.emit()
        self._app_context.task_controller.publish_state()

    def _refresh_test_voice_button_state(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._test_voice_button.setEnabled(False)
            self._test_voice_button.setText(self._test_voice_button_prepare_text)
            return
        if self._app_context.state.is_running:
            self._test_voice_button.setEnabled(False)
            self._test_voice_button.setText(self._test_voice_button_running_text)
            return
        self._test_voice_button.setEnabled(True)
        self._test_voice_button.setText(self._test_voice_button_default_text)

    def _finish_ultimate_clone_prepare(self, token: int) -> bool:
        if token != self._ultimate_clone_prepare_token:
            return False
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token += 1
        self._refresh_test_voice_button_state()
        return True

    def _begin_ultimate_clone_prepare(self, reference_audio_path: str) -> None:
        self._ultimate_clone_prepare_token += 1
        token = self._ultimate_clone_prepare_token
        self._ultimate_clone_prepare_in_progress = True
        self._refresh_test_voice_button_state()
        self._voice_info.setPlainText("正在识别参考音频文字，用于精准匹配试听...")
        prepare_ultimate_clone_prompt_text_async(
            self,
            self._app_context,
            str(reference_audio_path or ""),
            on_ready=lambda _prepared_text: self._handle_ultimate_clone_prompt_ready(token),
            on_failed=lambda message: self._handle_ultimate_clone_prompt_error(token, message),
        )

    def _handle_ultimate_clone_prompt_ready(self, token: int) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._test_voice()

    def _handle_ultimate_clone_prompt_error(self, token: int, message: str) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._voice_info.setPlainText(f"极致克隆准备失败：{message}")

    def _test_voice(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._voice_info.setPlainText("正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._voice_info.setPlainText("已有任务正在运行，请稍候或取消当前任务。")
            return

        item = self._voice_list.currentItem()
        if not item:
            return
        try:
            workspace = ensure_workspace(self._app_context, self._app_context.state.workspace)
        except RuntimeError as exc:
            self._voice_info.setPlainText(str(exc))
            return
        reference_audio_path = self._app_context.state.preferred_audio or None
        ultimate_clone, prompt_text = resolve_tts_clone_options(self._app_context, reference_audio_path)
        sample_text = self._voice_sample_input.text().strip() or "这是一个声音试听样例。"
        voice = str(item.data(Qt.ItemDataRole.UserRole))
        if ultimate_clone and not prompt_text:
            self._begin_ultimate_clone_prepare(str(reference_audio_path or ""))
            return

        self._submit_voice_preview_request(
            text=sample_text,
            voice=voice,
            reference_audio_path=reference_audio_path,
            ultimate_clone=ultimate_clone,
            prompt_text=prompt_text,
            workspace=workspace,
        )

    def _submit_voice_preview_request(
        self,
        *,
        text: str,
        voice: str,
        reference_audio_path: str | None,
        ultimate_clone: bool,
        prompt_text: str | None,
        workspace: str,
    ) -> str:
        self._refresh_test_voice_button_state()
        self._test_voice_button.setEnabled(False)
        self._test_voice_button.setText(self._test_voice_button_running_text)
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=voice,
                reference_audio_path=reference_audio_path,
                ultimate_clone=ultimate_clone,
                prompt_text=prompt_text,
                workspace=workspace,
                output_name="voice-preview",
            ),
        )
        if not task_id:
            self._voice_info.setPlainText(self._app_context.state.last_error or "试听任务提交失败，请稍后重试。")
            self._refresh_test_voice_button_state()
        return task_id

    def _set_default_reference(self) -> None:
        item = self._reference_list.currentItem()
        if not item:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole))
        self._app_context.state.preferred_reference_video = path
        self._refresh_reference_list()
        self.preferences_changed.emit()
        self._app_context.task_controller.publish_state()

    def _open_selected_reference_dir(self) -> None:
        item = self._reference_list.currentItem()
        if not item:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole))
        target = Path(path).parent
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _set_default_avatar(self) -> None:
        item = self._avatar_list.currentItem()
        if not item:
            return
        model_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._app_context.settings.avatar.default_model_id = model_id
        self._app_context.state.preferred_avatar_model_id = model_id
        self._refresh_avatar_list()
        self.preferences_changed.emit()
        self._app_context.task_controller.publish_state()

    def _sync_state(self, state: PipelineState) -> None:
        self._refresh_test_voice_button_state()


class SettingsDialog(QDialog):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._option_loader_thread: QThread | None = None
        self._option_loader: SettingsOptionLoader | None = None
        self.setWindowTitle("设置")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "常用")
        tabs.addTab(self._build_path_tab(), "路径")
        tabs.addTab(self._build_api_tab(), "接口")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply_and_accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self._start_loading_options()
        self._run_diagnostics()

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setSpacing(10)

        default_voice = current_voice(self._app_context)
        self._default_voice_input = QComboBox()
        fill_voice_combo(
            self._default_voice_input,
            self._app_context,
            default_voice,
            voices=[default_voice],
        )
        default_avatar = current_avatar_model(self._app_context)
        self._default_avatar_input = QComboBox()
        fill_avatar_combo(
            self._default_avatar_input,
            self._app_context,
            default_avatar,
            avatar_models=[default_avatar],
        )
        default_reference = self._app_context.state.preferred_reference_video
        self._default_reference_input = QComboBox()
        fill_reference_combo(
            self._default_reference_input,
            self._app_context,
            default_reference,
            reference_videos=[default_reference] if default_reference else [],
        )
        self._workspace_input = QLineEdit(self._app_context.state.workspace)
        self._option_status = QLabel("正在加载可用语音和素材...")
        self._option_status.setObjectName("mutedText")

        form.addRow("默认语音", self._default_voice_input)
        form.addRow("默认数字人", self._default_avatar_input)
        form.addRow("默认参考视频", self._default_reference_input)
        form.addRow("当前工作区", self._workspace_input)
        form.addRow("", self._option_status)
        return tab

    def _start_loading_options(self) -> None:
        if self._option_loader_thread is not None:
            return
        self._option_loader_thread = QThread()
        self._option_loader = SettingsOptionLoader(self._app_context)
        self._option_loader.moveToThread(self._option_loader_thread)
        self._option_loader_thread.started.connect(self._option_loader.run)
        self._option_loader.loaded.connect(self._apply_loaded_options)
        self._option_loader.failed.connect(self._handle_option_loading_failure)
        self._option_loader.finished.connect(self._option_loader_thread.quit)
        self._option_loader.finished.connect(self._option_loader.deleteLater)
        self._option_loader_thread.finished.connect(self._option_loader_thread.deleteLater)
        self._option_loader_thread.finished.connect(self._clear_option_loader)
        self._option_loader_thread.start()

    def _apply_loaded_options(self, payload: object) -> None:
        if not isinstance(payload, SettingsOptionSnapshot):
            self._handle_option_loading_failure("收到的设置数据格式不正确。")
            return
        fill_voice_combo(
            self._default_voice_input,
            self._app_context,
            combo_value(self._default_voice_input),
            voices=payload.voices,
        )
        fill_avatar_combo(
            self._default_avatar_input,
            self._app_context,
            combo_value(self._default_avatar_input),
            avatar_models=payload.avatar_models,
        )
        fill_reference_combo(
            self._default_reference_input,
            self._app_context,
            combo_value(self._default_reference_input),
            reference_videos=payload.reference_videos,
        )
        self._option_status.hide()

    def _handle_option_loading_failure(self, message: str) -> None:
        self._option_status.setText(f"可用语音和素材加载失败：{message}")

    def _clear_option_loader(self) -> None:
        self._option_loader_thread = None
        self._option_loader = None

    def _build_path_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        form_shell = QWidget()
        form = QFormLayout(form_shell)
        form.setSpacing(10)

        settings = self._app_context.settings
        self._ffmpeg_input = QLineEdit(str(settings.media.ffmpeg_path or ""))
        self._ffprobe_input = QLineEdit(str(settings.media.ffprobe_path or ""))
        self._model_root_input = QLineEdit(str(settings.models.root_dir or ""))
        self._tuilionnx_root_input = QLineEdit(str(settings.avatar.tuilionnx_root or ""))
        self._tuilionnx_python_input = QLineEdit(str(settings.avatar.tuilionnx_python or ""))

        form.addRow("FFmpeg 路径", self._ffmpeg_input)
        form.addRow("FFprobe 路径", self._ffprobe_input)
        form.addRow("模型根目录", self._model_root_input)
        form.addRow("数字人模型目录", self._tuilionnx_root_input)
        form.addRow("数字人 Python", self._tuilionnx_python_input)
        layout.addWidget(form_shell)

        diagnostics_shell = QFrame()
        diagnostics_shell.setObjectName("panelCard")
        diagnostics_layout = QVBoxLayout(diagnostics_shell)
        diagnostics_layout.setContentsMargins(16, 16, 16, 16)
        diagnostics_layout.setSpacing(10)
        diagnostics_head = QHBoxLayout()
        diagnostics_label = QLabel("运行环境自检")
        diagnostics_label.setObjectName("fieldLabel")
        diagnostics_hint = QLabel("仅检查路径和关键文件，不会加载 GPU 模型。")
        diagnostics_hint.setObjectName("mutedText")
        self._diagnostic_button = QPushButton("运行环境自检")
        self._diagnostic_button.setObjectName("subtleButton")
        self._diagnostic_button.clicked.connect(self._run_diagnostics)
        diagnostics_head.addWidget(diagnostics_label)
        diagnostics_head.addStretch(1)
        diagnostics_head.addWidget(self._diagnostic_button)
        diagnostics_layout.addLayout(diagnostics_head)
        diagnostics_layout.addWidget(diagnostics_hint)
        self._diagnostic_output = QPlainTextEdit()
        self._diagnostic_output.setObjectName("compactLog")
        self._diagnostic_output.setReadOnly(True)
        self._diagnostic_output.setMinimumHeight(220)
        diagnostics_layout.addWidget(self._diagnostic_output)
        layout.addWidget(diagnostics_shell)
        layout.addStretch(1)
        return tab

    def _build_api_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setSpacing(10)

        settings = self._app_context.settings
        self._llm_base_input = QLineEdit(settings.llm.api_base)
        self._llm_model_input = QLineEdit(settings.llm.model)
        self._llm_key_input = QLineEdit(settings.llm.api_key or "")
        self._llm_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._tts_base_input = QLineEdit(settings.tts.base_url)

        form.addRow("LLM API Base", self._llm_base_input)
        form.addRow("LLM 模型", self._llm_model_input)
        form.addRow("LLM API Key", self._llm_key_input)
        form.addRow("TTS 服务地址", self._tts_base_input)
        return tab

    def _apply_and_accept(self) -> None:
        settings = self._app_context.settings
        state = self._app_context.state

        settings.tts.default_voice = combo_value(self._default_voice_input) or settings.tts.default_voice
        settings.avatar.default_model_id = combo_value(self._default_avatar_input) or settings.avatar.default_model_id
        state.preferred_voice = settings.tts.default_voice
        state.preferred_avatar_model_id = settings.avatar.default_model_id
        state.preferred_reference_video = combo_value(self._default_reference_input)
        workspace_text = self._workspace_input.text().strip()
        if workspace_text:
            try:
                state.workspace = ensure_workspace(self._app_context, workspace_text)
            except RuntimeError as exc:
                QMessageBox.warning(self, "工作空间不可用", str(exc))
                return

        settings.media.ffmpeg_path = Path(self._ffmpeg_input.text().strip()) if self._ffmpeg_input.text().strip() else None
        settings.media.ffprobe_path = (
            Path(self._ffprobe_input.text().strip()) if self._ffprobe_input.text().strip() else None
        )
        settings.models.root_dir = Path(self._model_root_input.text().strip()) if self._model_root_input.text().strip() else None
        settings.avatar.tuilionnx_root = (
            Path(self._tuilionnx_root_input.text().strip()) if self._tuilionnx_root_input.text().strip() else None
        )
        settings.avatar.tuilionnx_python = (
            Path(self._tuilionnx_python_input.text().strip())
            if self._tuilionnx_python_input.text().strip()
            else None
        )
        settings.llm.api_base = self._llm_base_input.text().strip() or settings.llm.api_base
        settings.llm.model = self._llm_model_input.text().strip() or settings.llm.model
        settings.llm.api_key = self._llm_key_input.text().strip() or None
        settings.tts.base_url = self._tts_base_input.text().strip() or settings.tts.base_url

        self.accept()

    def _settings_for_diagnostics(self) -> AppSettings:
        settings = self._app_context.settings.model_copy(deep=True)
        model_root_text = self._model_root_input.text().strip()
        model_root = Path(model_root_text) if model_root_text else None

        settings.media.ffmpeg_path = Path(self._ffmpeg_input.text().strip()) if self._ffmpeg_input.text().strip() else None
        settings.media.ffprobe_path = Path(self._ffprobe_input.text().strip()) if self._ffprobe_input.text().strip() else None
        settings.models.root_dir = model_root
        if model_root is not None:
            settings.models.voxcpm_model_dir = model_root / "voxcpm" / "VoxCPM2"
            settings.models.whisper_model_dir = model_root / "whisper"
            settings.models.tuilionnx_model_dir = model_root / "tuilionnx"
        settings.avatar.tuilionnx_root = (
            Path(self._tuilionnx_root_input.text().strip())
            if self._tuilionnx_root_input.text().strip()
            else settings.models.tuilionnx_model_dir
        )
        settings.avatar.tuilionnx_python = (
            Path(self._tuilionnx_python_input.text().strip())
            if self._tuilionnx_python_input.text().strip()
            else None
        )
        return settings

    def _run_diagnostics(self) -> None:
        settings = self._settings_for_diagnostics()
        report = format_diagnostic_report(run_startup_diagnostics(settings))
        self._diagnostic_output.setPlainText(report)
