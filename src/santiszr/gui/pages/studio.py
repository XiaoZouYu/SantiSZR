from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from random import choice

from PySide6.QtCore import QSignalBlocker, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QLinearGradient, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from santiszr.app import AppContext
from santiszr.core.asset_library import AssetCategory
from santiszr.core.paths import ensure_module_dir, sanitize_filename
from santiszr.domain.schemas.audio import RewriteMode, RewriteRequest, TTSRequest, TTSResult
from santiszr.domain.schemas.avatar import AvatarEngine, AvatarRequest, AvatarResult
from santiszr.domain.schemas.content import ContentRequest, ContentResult, VideoSource
from santiszr.domain.schemas.publish import (
    GenerateVideoWorkflowRequest,
    PublishPlatform,
    PublishRequest,
)
from santiszr.domain.schemas.subtitle import SubtitleRequest, SubtitleResult, SubtitleStyle
from santiszr.gui.i18n import status_text, studio_flow_labels, voice_text
from santiszr.gui.state.session import AudioVariant, PipelineState
from santiszr.gui.ultimate_clone import cached_ultimate_clone_prompt_text, prepare_ultimate_clone_prompt_text_async
from santiszr.gui.workspace import ensure_workspace as ensure_selected_workspace
from santiszr.infra.llm.client import LLMClient
from santiszr.infra.media.ffmpeg import FFmpegAdapter
from santiszr.workers.protocol import WorkerEvent, WorkerEventType, WorkerTaskKind


VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
FONT_OPTIONS = ["Microsoft YaHei", "SimHei", "SimSun", "Arial", "KaiTi"]
PHASE_LABELS = ("1. 文案", "2. 音频", "3. 数字人", "4. 发布")
DESCRIPTION_SYSTEM_PROMPT = "你是短视频运营助手，只输出中文成品。"
DESCRIPTION_USER_PROMPT = (
    "请根据下列口播文案生成一段适合发布的短视频描述，并在下一行给出 4 到 6 个话题标签。"
    "输出格式必须严格是：第一段为描述；第二行是标签，标签之间用空格分隔。\n\n文案：\n{text}"
)
HOOK_SYSTEM_PROMPT = "你是短视频封面文案助手，只输出纯文本。"
HOOK_USER_PROMPT = (
    "根据下列文案生成一条适合中文短视频封面的短句，控制在 12 个字以内，"
    "再给出一个适合作为高亮词的 2 到 4 字词语。"
    "按两行输出：第一行短句，第二行高亮词。\n\n文案：\n{text}"
)

CLONE_REFERENCE_VOICE = "reference-clone"
UPLOADED_AVATAR_MODEL_ID = "uploaded-avatar"


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
    del app_context
    return UPLOADED_AVATAR_MODEL_ID


def list_voices(app_context: AppContext) -> list[str]:
    try:
        voices = list(app_context.services.tts.client.list_voices())
    except Exception:
        voices = []
    fallback = current_voice(app_context)
    if fallback and fallback not in voices:
        voices.append(fallback)
    return voices or [CLONE_REFERENCE_VOICE]


def list_avatar_models(app_context: AppContext) -> list[str]:
    del app_context
    return [UPLOADED_AVATAR_MODEL_ID]


def list_audio_candidates(app_context: AppContext) -> list[str]:
    return app_context.media_library.list_paths(AssetCategory.audio)


def list_reference_videos(app_context: AppContext) -> list[str]:
    return app_context.media_library.list_paths(AssetCategory.reference_video)


def list_bgm_candidates(app_context: AppContext) -> list[str]:
    return app_context.media_library.list_paths(AssetCategory.background_music)


def combo_value(combo: QComboBox) -> str:
    index = combo.currentIndex()
    text = combo.currentText().strip()
    if index >= 0 and text == combo.itemText(index).strip():
        data = combo.itemData(index)
        if data is not None:
            return str(data).strip()
    return text


def file_summary(path_text: str) -> str:
    if not path_text:
        return "暂无输出"
    path = Path(path_text)
    if not path.exists():
        return f"路径: {path_text}\n״̬: 文件不存在"
    stat = path.stat()
    return (
        f"路径: {path}\n"
        f"大小: {stat.st_size / (1024 * 1024):.2f} MB\n"
        f"修改时间: {stat.st_mtime:.0f}"
    )


def format_audio_time(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds // 1000))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def description_fallback(text: str, tags: list[str]) -> str:
    normalized = " ".join(line.strip() for line in text.splitlines() if line.strip())
    summary = normalized[:120] + ("..." if len(normalized) > 120 else "")
    tag_line = " ".join(tags[:5] or ["#短视频", "#数字人", "#内容生产"])
    return f"{summary}\n{tag_line}"


def title_from_description(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:24] or "短视频成品"


def heuristic_cover_text(text: str, tags: list[str]) -> tuple[str, str]:
    compact = "".join(ch for ch in text if not ch.isspace())
    headline = compact[:12] or "爆款拆解"
    highlight = (tags[0].lstrip("#") if tags else compact[:4]) or "重点"
    return headline[:12], highlight[:4]


def display_voice_label(voice: str) -> str:
    normalized = voice.strip()
    if not normalized:
        return ""
    if normalized == CLONE_REFERENCE_VOICE:
        return "克隆声音"
    return normalized


def describe_audio_variant(variant: AudioVariant) -> str:
    name = variant.label or Path(variant.path).stem
    meta: list[str] = []
    if variant.voice:
        meta.append(display_voice_label(variant.voice))
    if variant.speed is not None:
        meta.append(f"{variant.speed:.1f}x")
    if variant.duration_sec:
        meta.append(f"{variant.duration_sec:.1f}s")
    if variant.source:
        meta.append("音频库" if variant.source == "library" else "生成")
    summary = " / ".join(meta)
    return f"{name}\n{summary}" if summary else name


class ColorInput(QWidget):
    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._line_edit = QLineEdit(value)
        self._line_edit.textChanged.connect(self._refresh_preview)
        self._preview = QPushButton()
        self._preview.setFixedWidth(44)
        self._preview.clicked.connect(self._pick_color)
        layout.addWidget(self._line_edit, 1)
        layout.addWidget(self._preview)
        self._refresh_preview()

    def text(self) -> str:
        return self._line_edit.text().strip() or "#FFFFFF"

    def setText(self, value: str) -> None:
        self._line_edit.setText(value)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.text()), self, "选择颜色")
        if color.isValid():
            self._line_edit.setText(color.name().upper())

    def _refresh_preview(self) -> None:
        self._preview.setStyleSheet(
            f"border-radius: 10px; border: 1px solid #d2d8df; background: {self.text()};"
        )


class ClickOnlyComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: ANN001
        event.ignore()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if self.view().isVisible():
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()
        if key in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_F4,
        ) or (key == Qt.Key.Key_Down and modifiers & Qt.KeyboardModifier.AltModifier):
            self.showPopup()
            event.accept()
            return
        event.ignore()


QComboBox = ClickOnlyComboBox


class ToggleSwitch(QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        del event
        radius = self.height() / 2
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        handle_diameter = rect.height() - 4
        checked = self.isChecked()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#005A6E") if checked else QColor("#C8D0D4"))
        painter.setBrush(QColor("#005A6E") if checked else QColor("#D8DADB"))
        painter.drawRoundedRect(rect, radius, radius)

        handle_x = rect.right() - handle_diameter - 2 if checked else rect.left() + 2
        handle_rect = QRectF(handle_x, rect.top() + 2, handle_diameter, handle_diameter)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(handle_rect)
        painter.end()


class AspectRatioContainer(QWidget):
    def __init__(
        self,
        aspect_width: int,
        aspect_height: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._aspect_width = max(1, aspect_width)
        self._aspect_height = max(1, aspect_height)
        self._content: QWidget | None = None

    def set_content(self, widget: QWidget) -> None:
        self._content = widget
        widget.setParent(self)
        self._update_content_geometry()

    def minimumSizeHint(self) -> QSize:
        return QSize(320, 568)

    def sizeHint(self) -> QSize:
        return QSize(360, 640)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._update_content_geometry()

    def _update_content_geometry(self) -> None:
        if self._content is None:
            return
        bounds = self.contentsRect()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return

        target_width = bounds.width()
        target_height = round(target_width * self._aspect_height / self._aspect_width)
        if target_height > bounds.height():
            target_height = bounds.height()
            target_width = round(target_height * self._aspect_width / self._aspect_height)

        x = bounds.x() + max(0, (bounds.width() - target_width) // 2)
        y = bounds.y() + max(0, (bounds.height() - target_height) // 2)
        self._content.setGeometry(x, y, target_width, target_height)


class ClickableWidget(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class ClickableVideoWidget(QVideoWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class PipelineStudioPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ffmpeg = FFmpegAdapter(
            ffmpeg_path=app_context.settings.media.ffmpeg_path,
            ffprobe_path=app_context.settings.media.ffprobe_path,
        )
        self._subtitle_text_path = ""
        self._cover_output_path = ""
        self._cover_generated_for_publish = False
        self._latest_download_url = ""
        self._latest_elapsed_sec = 0.0
        self._api_keys: list[str] = []
        self._auto_pipeline_active = False
        self._auto_pipeline_log: list[str] = []
        self._audio_loaded_path = ""
        self._audio_loaded_mtime_ns = 0
        self._audio_slider_is_dragging = False
        self._avatar_video_output_device = None
        self._avatar_video_player = None
        self._avatar_video_loaded_path = ""
        self._avatar_video_loaded_mtime_ns = 0
        self._avatar_video_has_started = False
        self._build_ui()
        self._setup_audio_player()
        self._setup_avatar_video_player()
        self._load_api_keys()
        self.refresh_options()
        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._app_context.task_controller.task_event.connect(self._handle_task_event)
        self._sync_state(self._app_context.state)

    def refresh_options(self) -> None:
        self._refresh_voice_options()
        self._refresh_avatar_options()
        self._refresh_reference_options()
        self._refresh_bgm_options()
        self._load_api_keys()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root_layout.addWidget(scroll)

        canvas = QWidget()
        scroll.setWidget(canvas)
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(24, 24, 24, 28)
        layout.setSpacing(18)
        layout.addWidget(self._create_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_column())
        splitter.addWidget(self._build_center_column())
        splitter.addWidget(self._build_right_column())
        splitter.setSizes([430, 430, 520])
        layout.addWidget(splitter, 1)
        layout.addWidget(self._build_bottom_section())

    def _create_header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("heroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        eyebrow = QLabel("提取 / 改写 / 生成 / 发布")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("全流程工作台")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "?????????????????????????????????????????????????"
        )
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for label in studio_flow_labels():
            chip = QLabel(label)
            chip.setObjectName("flowChip")
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(chips)
        return card

    def _build_left_column(self) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_copy_extraction_card())
        layout.addWidget(self._build_api_key_card())
        layout.addWidget(self._build_rewrite_card())
        layout.addStretch(1)
        return column

    def _build_center_column(self) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_audio_card())
        layout.addWidget(self._build_subtitle_card())
        layout.addWidget(self._build_description_card())
        layout.addStretch(1)
        return column

    def _build_right_column(self) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_avatar_card())
        layout.addWidget(self._build_subtitle_style_card())
        layout.addWidget(self._build_bgm_card())
        layout.addWidget(self._build_cover_card())
        layout.addStretch(1)
        return column

    def _section_card(self, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        desc_label = QLabel(desc)
        desc_label.setObjectName("sectionCaption")
        desc_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        return card

    def _sub_card(self, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setObjectName("subCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        desc_label = QLabel(desc)
        desc_label.setObjectName("mutedText")
        desc_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        return card

    def _build_bottom_section(self) -> QWidget:
        card = self._section_card("发布与运维区", "发布按钮、流程控制、服务启动和兼容说明都集中在这里。")
        layout = card.layout()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        publish_box = self._sub_card("单平台发布", "当前版本只能生成发布素材，不能直接登录平台自动发布。")
        publish_layout = publish_box.layout()
        publish_row = QHBoxLayout()
        self._publish_douyin = QPushButton("生成抖音发布结果")
        self._publish_xhs = QPushButton("生成小红书发布结果")
        self._publish_wechat = QPushButton("生成视频号发布结果")
        self._publish_all = QPushButton("生成全部平台发布结果")
        self._publish_all.setObjectName("primaryButton")
        self._publish_douyin.clicked.connect(lambda: self._publish(PublishPlatform.douyin))
        self._publish_xhs.clicked.connect(lambda: self._publish(PublishPlatform.xiaohongshu))
        self._publish_wechat.clicked.connect(lambda: self._publish(PublishPlatform.wechat_channels))
        self._publish_all.clicked.connect(lambda: self._publish(None))
        for button in [
            self._publish_douyin,
            self._publish_xhs,
            self._publish_wechat,
            self._publish_all,
        ]:
            publish_row.addWidget(button)
        self._publish_status = QPlainTextEdit()
        self._publish_status.setReadOnly(True)
        self._publish_status.setMaximumHeight(160)
        publish_layout.addLayout(publish_row)
        publish_layout.addWidget(self._publish_status)

        control_box = self._sub_card("一键全流程", "总控按钮会串联提取、改写、音频、字幕、数字人、后处理和发布素材准备。")
        control_layout = control_box.layout()
        control_row = QHBoxLayout()
        self._all_in_one_button = QPushButton("一键生成并准备发布素材")
        self._all_in_one_button.setObjectName("primaryButton")
        self._all_in_one_button.clicked.connect(self._run_all_in_one)
        self._cancel_button = QPushButton("取消当前任务")
        self._cancel_button.clicked.connect(self._app_context.task_controller.cancel_active_task)
        control_row.addWidget(self._all_in_one_button)
        control_row.addWidget(self._cancel_button)
        self._all_in_one_status = QPlainTextEdit()
        self._all_in_one_status.setReadOnly(True)
        self._all_in_one_status.setMaximumHeight(160)
        control_layout.addLayout(control_row)
        control_layout.addWidget(self._all_in_one_status)

        ops_box = self._sub_card("服务启动与更新", "当前项目未内置完整运维脚本，按钮保留职责并反馈检查结果。")
        ops_layout = ops_box.layout()
        ops_row = QHBoxLayout()
        self._start_digit_human = QPushButton("启动 digit_human")
        self._start_cosyvoice = QPushButton("启动 cosyvoice")
        self._check_update = QPushButton("检查更新")
        self._start_digit_human.clicked.connect(lambda: self._service_action("digit_human"))
        self._start_cosyvoice.clicked.connect(lambda: self._service_action("cosyvoice"))
        self._check_update.clicked.connect(self._check_project_update)
        ops_row.addWidget(self._start_digit_human)
        ops_row.addWidget(self._start_cosyvoice)
        ops_row.addWidget(self._check_update)
        self._ops_status = QPlainTextEdit()
        self._ops_status.setReadOnly(True)
        self._ops_status.setMaximumHeight(160)
        ops_layout.addLayout(ops_row)
        ops_layout.addWidget(self._ops_status)

        compatibility_box = self._sub_card("????", "????????????????????????")
        compatibility_layout = compatibility_box.layout()
        compatibility_text = QPlainTextEdit()
        compatibility_text.setReadOnly(True)
        compatibility_text.setMaximumHeight(110)
        compatibility_text.setPlainText(
            "?????????????????????????\n"
            "特效字幕模板：旧项目废弃能力，当前不作为重点实现。\n"
            "????????????????????????"
        )
        compatibility_layout.addWidget(compatibility_text)

        grid.addWidget(publish_box, 0, 0)
        grid.addWidget(control_box, 0, 1)
        grid.addWidget(ops_box, 1, 0)
        grid.addWidget(compatibility_box, 1, 1)
        layout.addLayout(grid)
        return card

    def _build_copy_extraction_card(self) -> QWidget:
        card = self._section_card("?????", "???????????????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._workspace_input = QLineEdit(self._app_context.state.workspace)
        self._video_link_input = QLineEdit(self._app_context.state.source_input)
        self._video_link_input.setPlaceholderText("输入视频链接、本地视频路径或分享口令")
        self._source_type_hint = QLabel("????????????")
        self._source_type_hint.setObjectName("mutedText")
        self._copy_editor = QPlainTextEdit()
        self._copy_editor.setPlaceholderText("??????????????????????????????")
        self._copy_editor.setMinimumHeight(220)
        self._extract_button = QPushButton("提取视频文案")
        self._extract_button.setObjectName("primaryButton")
        self._extract_button.clicked.connect(self._submit_content_task)
        open_workspace = QPushButton("?????")
        open_workspace.clicked.connect(lambda: self._open_path(self._workspace_input.text().strip()))
        action_row = QHBoxLayout()
        action_row.addWidget(self._extract_button)
        action_row.addWidget(open_workspace)
        form.addRow("???", self._workspace_input)
        form.addRow("视频链接", self._video_link_input)
        layout.addLayout(form)
        layout.addWidget(self._source_type_hint)
        layout.addLayout(action_row)
        layout.addWidget(self._copy_editor)
        return card

    def _build_api_key_card(self) -> QWidget:
        card = self._section_card("API Key ???", "???????????? AI ??????? Key ???")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("手动输入 API Key")
        self._api_key_combo = QComboBox()
        self._api_key_combo.currentIndexChanged.connect(lambda _index: self._apply_selected_api_key())
        self._api_status = QLabel("??? API Key??? AI ??????????????")
        self._api_status.setObjectName("mutedText")
        button_row = QHBoxLayout()
        save_button = QPushButton("保存 Key")
        delete_button = QPushButton("删除 Key")
        refresh_button = QPushButton("刷新 Key")
        save_button.clicked.connect(self._save_api_key)
        delete_button.clicked.connect(self._delete_api_key)
        refresh_button.clicked.connect(self._load_api_keys)
        button_row.addWidget(save_button)
        button_row.addWidget(delete_button)
        button_row.addWidget(refresh_button)
        form.addRow("手动输入", self._api_key_input)
        form.addRow("当前 Key", self._api_key_combo)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(self._api_status)
        return card

    def _build_rewrite_card(self) -> QWidget:
        card = self._section_card("?????", "?????????????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._rewrite_mode = QComboBox()
        self._rewrite_mode.addItem("AI自动仿写", RewriteMode.imitate)
        self._rewrite_mode.addItem("根据指令仿写", RewriteMode.custom)
        self._rewrite_mode.addItem("润色纠错", RewriteMode.correct)
        self._rewrite_prompt = QLineEdit("??????????????????????")
        self._rewrite_prompt.setPlaceholderText("??????????")
        rewrite_button = QPushButton("执行仿写")
        rewrite_button.clicked.connect(self._submit_rewrite_task)
        self._rewrite_status = QLabel("??????????????????")
        self._rewrite_status.setObjectName("mutedText")
        form.addRow("仿写模式", self._rewrite_mode)
        form.addRow("Prompt 规则", self._rewrite_prompt)
        layout.addLayout(form)
        layout.addWidget(rewrite_button)
        layout.addWidget(self._rewrite_status)
        return card

    def _build_audio_card(self) -> QWidget:
        card = self._section_card("????????", "??????????????????????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._clone_voice_hint = QLabel("??????????????????????????????????????")
        self._voice_combo.setEditable(False)
        self._refresh_voice_button = QPushButton("刷新音色")
        self._refresh_voice_button.clicked.connect(self._refresh_voice_options)
        self._voice_speed = QDoubleSpinBox()
        self._voice_speed.setRange(0.5, 2.0)
        self._voice_speed.setSingleStep(0.1)
        self._voice_speed.setValue(1.0)
        form.addRow("音色选择", self._voice_combo)
        self._ultimate_clone_checkbox = QCheckBox("极致克隆 / 精准匹配")
        self._ultimate_clone_checkbox.setChecked(False)
        self._ultimate_clone_checkbox.setToolTip("弢启后自动识别参音频文字，提高音色和语气相似度；第丢次会慢一点?")
        form.addRow("", self._refresh_voice_button)
        form.addRow("语?, self._voice_speed")
        form.addRow("", self._ultimate_clone_checkbox)

        action_row = QHBoxLayout()
        self._generate_audio_button = QPushButton("生成音频")
        self._generate_audio_button.setObjectName("primaryButton")
        self._generate_audio_button.clicked.connect(self._submit_tts_task)
        action_row.addWidget(self._generate_audio_button)
        action_row.addStretch(1)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._audio_status_badge = QLabel()
        self._audio_status_badge.setObjectName("statusPill")
        self._audio_status_hint = QLabel()
        self._audio_status_hint.setObjectName("mutedText")
        self._audio_status_hint.setWordWrap(True)
        status_row.addWidget(self._audio_status_badge, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self._audio_status_hint, 1)

        self._audio_preview_card = QFrame()
        self._audio_preview_card.setObjectName("audioPreviewCard")
        preview_layout = QVBoxLayout(self._audio_preview_card)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(10)

        player_row = QHBoxLayout()
        player_row.setSpacing(12)
        self._play_audio_button = QPushButton("播放")
        self._play_audio_button.setObjectName("playButton")
        self._play_audio_button.clicked.connect(self._toggle_audio_playback)
        self._audio_progress = QSlider(Qt.Orientation.Horizontal)
        self._audio_progress.setRange(0, 0)
        self._audio_progress.setEnabled(False)
        self._audio_progress.sliderPressed.connect(self._on_audio_slider_pressed)
        self._audio_progress.sliderReleased.connect(self._on_audio_slider_released)
        self._audio_progress.sliderMoved.connect(self._seek_audio)
        self._audio_timeline = QLabel("00:00 / 00:00")
        self._audio_timeline.setObjectName("timeLabel")
        player_row.addWidget(self._play_audio_button)
        player_row.addWidget(self._audio_progress, 1)
        player_row.addWidget(self._audio_timeline)

        self._audio_output = QLabel()
        self._audio_output.setObjectName("mutedText")
        self._audio_output.setWordWrap(True)

        preview_layout.addLayout(player_row)
        preview_layout.addWidget(self._audio_output)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addLayout(status_row)
        layout.addWidget(self._audio_preview_card)
        return card

    def _setup_audio_player(self) -> None:
        self._audio_output_device = QAudioOutput(self)
        self._audio_output_device.setVolume(0.85)
        self._audio_player = QMediaPlayer(self)
        self._audio_player.setAudioOutput(self._audio_output_device)
        self._audio_player.positionChanged.connect(self._update_audio_position)
        self._audio_player.durationChanged.connect(self._update_audio_duration)
        self._audio_player.playbackStateChanged.connect(self._update_audio_play_button)
        self._audio_player.mediaStatusChanged.connect(self._handle_audio_media_status)
        self._audio_player.errorOccurred.connect(self._handle_audio_error)
        self._set_audio_feedback("???", "idle", "???????????????????????")
        audio_path = self._app_context.state.audio_path
        self._sync_audio_source(audio_path)
        if audio_path and Path(audio_path).exists():
            self._set_audio_feedback("就绪", "success", "已加载当前音频，可直接试听和切换版本。")

    def _set_audio_feedback(self, title: str, tone: str, detail: str) -> None:
        self._audio_status_badge.setText(title)
        self._audio_status_badge.setProperty("tone", tone)
        self._audio_status_badge.style().unpolish(self._audio_status_badge)
        self._audio_status_badge.style().polish(self._audio_status_badge)
        self._audio_status_hint.setText(detail)

    def _sync_audio_source(self, path_text: str) -> None:
        audio_path = (path_text or "").strip()
        self._audio_restore_path_after_preview = ""
        if not audio_path:
            self._audio_output.setText("尚未生成音频文件。")
            self._reset_audio_player(clear_source=True)
            return

        audio_file = Path(audio_path)
        if not audio_file.exists():
            self._audio_output.setText(f"音频文件不存在：\n{audio_path}")
            self._reset_audio_player(clear_source=True)
            return

        self._audio_output.setText(file_summary(audio_path))
        self._load_audio_source(audio_file)

    def _load_audio_source(self, audio_file: Path) -> None:
        resolved = str(audio_file.resolve())
        current_mtime_ns = audio_file.stat().st_mtime_ns
        if resolved == self._audio_loaded_path and current_mtime_ns == self._audio_loaded_mtime_ns:
            self._play_audio_button.setEnabled(True)
            return
        self._audio_player.stop()
        self._audio_player.setSource(QUrl())
        self._audio_player.setSource(QUrl.fromLocalFile(resolved))
        self._audio_loaded_path = resolved
        self._audio_loaded_mtime_ns = current_mtime_ns
        self._audio_progress.setRange(0, 0)
        self._audio_progress.setValue(0)
        self._audio_progress.setEnabled(False)
        self._play_audio_button.setEnabled(True)
        self._audio_timeline.setText("00:00 / 00:00")

    def _reset_audio_player(self, *, clear_source: bool) -> None:
        if hasattr(self, "_audio_player"):
            self._audio_player.stop()
            if clear_source:
                self._audio_player.setSource(QUrl())
        self._audio_loaded_path = ""
        self._audio_loaded_mtime_ns = 0
        self._audio_restore_path_after_preview = ""
        self._audio_slider_is_dragging = False
        self._audio_progress.setRange(0, 0)
        self._audio_progress.setValue(0)
        self._audio_progress.setEnabled(False)
        self._play_audio_button.setEnabled(False)
        self._play_audio_button.setText("▶")
        self._audio_timeline.setText("00:00 / 00:00")

    def _toggle_audio_playback(self) -> None:
        if self._audio_loaded_path:
            if self._audio_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
                self._audio_player.pause()
                return
            self._audio_player.play()
            return

        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._set_audio_feedback("待选择", "idle", "请先选择一条当前音频或参考音频。")
            return
        audio_file = Path(audio_path)
        if not audio_file.exists():
            self._set_audio_feedback("文件缺失", "error", f"音频文件不存在：{audio_path}")
            self._reset_audio_player(clear_source=True)
            return
        self._load_audio_source(audio_file)
        if self._audio_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self._audio_player.pause()
            return
        self._audio_player.play()

    def _on_audio_slider_pressed(self) -> None:
        self._audio_slider_is_dragging = True

    def _on_audio_slider_released(self) -> None:
        self._audio_slider_is_dragging = False
        self._seek_audio(self._audio_progress.value())

    def _seek_audio(self, position: int) -> None:
        if not self._audio_loaded_path:
            return
        self._audio_player.setPosition(position)
        self._audio_timeline.setText(
            f"{format_audio_time(position)} / {format_audio_time(self._audio_player.duration())}"
        )

    def _update_audio_position(self, position: int) -> None:
        if self._audio_slider_is_dragging:
            return
        self._audio_progress.setValue(position)
        self._audio_timeline.setText(
            f"{format_audio_time(position)} / {format_audio_time(self._audio_player.duration())}"
        )

    def _update_audio_duration(self, duration: int) -> None:
        self._audio_progress.setRange(0, max(duration, 0))
        self._audio_progress.setEnabled(duration > 0)
        self._audio_timeline.setText(
            f"{format_audio_time(self._audio_player.position())} / {format_audio_time(duration)}"
        )

    def _update_audio_play_button(self, playback_state: QMediaPlayer.PlaybackState) -> None:
        self._play_audio_button.setText(
            "⏸" if playback_state is QMediaPlayer.PlaybackState.PlayingState else "▶"
        )

    def _handle_audio_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status is QMediaPlayer.MediaStatus.EndOfMedia:
            self._audio_player.pause()
            self._audio_player.setPosition(0)
            if self._audio_restore_path_after_preview:
                restore_path = self._audio_restore_path_after_preview
                self._audio_restore_path_after_preview = ""
                self._sync_audio_source(restore_path)

    def _handle_audio_error(self, _error: QMediaPlayer.Error, error_text: str) -> None:
        if not error_text:
            return
        self._set_audio_feedback("播放失败", "error", error_text)

    def _setup_avatar_video_player(self) -> None:
        if not hasattr(self, "_avatar_video_widget"):
            return
        if self._avatar_video_player is not None:
            return
        self._avatar_video_output_device = QAudioOutput(self)
        self._avatar_video_output_device.setVolume(1.0)
        self._avatar_video_player = QMediaPlayer(self)
        self._avatar_video_player.setAudioOutput(self._avatar_video_output_device)
        self._avatar_video_player.setVideoOutput(self._avatar_video_widget)
        self._avatar_video_player.playbackStateChanged.connect(self._handle_avatar_video_playback_state)
        self._avatar_video_player.mediaStatusChanged.connect(self._handle_avatar_video_media_status)
        self._avatar_video_player.errorOccurred.connect(self._handle_avatar_video_error)
        self._avatar_video_widget.setToolTip("点击预览区域可播放或暂停视频。")

    def _current_avatar_preview_video_path(self) -> str:
        fallback = ""
        for path in [
            self._app_context.state.final_video_path,
            self._app_context.state.avatar_video_path,
        ]:
            normalized = (path or "").strip()
            if not normalized:
                continue
            if Path(normalized).exists():
                return normalized
            if not fallback:
                fallback = normalized
        return fallback

    def _load_avatar_video_source(self, video_file: Path) -> None:
        if self._avatar_video_player is None:
            self._setup_avatar_video_player()
        if self._avatar_video_player is None:
            return
        resolved = str(video_file.resolve())
        current_mtime_ns = video_file.stat().st_mtime_ns
        if resolved == self._avatar_video_loaded_path and current_mtime_ns == self._avatar_video_loaded_mtime_ns:
            return
        self._avatar_video_player.stop()
        self._avatar_video_player.setSource(QUrl())
        self._avatar_video_player.setSource(QUrl.fromLocalFile(resolved))
        self._avatar_video_loaded_path = resolved
        self._avatar_video_loaded_mtime_ns = current_mtime_ns
        self._avatar_video_has_started = False

    def _reset_avatar_video_player(self, *, clear_source: bool) -> None:
        if self._avatar_video_player is not None:
            self._avatar_video_player.stop()
            if clear_source:
                self._avatar_video_player.setSource(QUrl())
        self._avatar_video_loaded_path = ""
        self._avatar_video_loaded_mtime_ns = 0
        self._avatar_video_has_started = False

    def _toggle_avatar_video_playback(self) -> None:
        video_path = self._current_avatar_preview_video_path().strip()
        if not video_path:
            return
        if self._avatar_video_player is None:
            self._setup_avatar_video_player()
        if self._avatar_video_player is None:
            return
        video_file = Path(video_path)
        if not video_file.exists():
            self._reset_avatar_video_player(clear_source=True)
            if hasattr(self, "_avatar_output"):
                self._avatar_output.setPlainText(f"视频文件不存在：{video_path}")
            self._sync_avatar_preview(self._app_context.state)
            return
        self._load_avatar_video_source(video_file)
        if (
            hasattr(self, "_audio_player")
            and self._audio_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState
        ):
            self._audio_player.pause()
        if self._avatar_video_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self._avatar_video_player.pause()
            return
        self._avatar_video_has_started = True
        self._avatar_preview_stack.setCurrentWidget(self._avatar_video_widget)
        self._avatar_video_player.play()

    def _handle_avatar_video_playback_state(self, _state: QMediaPlayer.PlaybackState) -> None:
        self._sync_avatar_preview(self._app_context.state)

    def _handle_avatar_video_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._avatar_video_player is None:
            return
        if status is QMediaPlayer.MediaStatus.EndOfMedia:
            self._avatar_video_player.pause()
            self._avatar_video_player.setPosition(0)
            self._sync_avatar_preview(self._app_context.state)

    def _handle_avatar_video_error(self, _error: QMediaPlayer.Error, error_text: str) -> None:
        if not error_text:
            return
        self._reset_avatar_video_player(clear_source=True)
        if hasattr(self, "_avatar_output"):
            self._avatar_output.setPlainText(f"视频预览失败：{error_text}")
        self._sync_avatar_preview(self._app_context.state)

    def _build_subtitle_card(self) -> QWidget:
        card = self._section_card("????????", "????????????????????")
        layout = card.layout()
        action_row = QHBoxLayout()
        self._generate_subtitle_button = QPushButton("单独生成字幕")
        self._generate_subtitle_button.clicked.connect(self._submit_subtitle_task)
        self._save_subtitle_button = QPushButton("保存字幕文本")
        self._save_subtitle_button.clicked.connect(self._save_subtitle_text)
        open_subtitle = QPushButton("打开字幕文件")
        open_subtitle.clicked.connect(lambda: self._open_path(self._app_context.state.subtitle_path))
        action_row.addWidget(self._generate_subtitle_button)
        action_row.addWidget(self._save_subtitle_button)
        action_row.addWidget(open_subtitle)
        self._subtitle_output = QPlainTextEdit()
        self._subtitle_output.setPlaceholderText("??????????????")
        self._subtitle_output.setMinimumHeight(220)
        layout.addLayout(action_row)
        layout.addWidget(self._subtitle_output)
        return card

    def _build_description_card(self) -> QWidget:
        card = self._section_card("???? / ?????", "??????????????")
        layout = card.layout()
        self._description_button = QPushButton("AI ???????????")
        self._description_button.clicked.connect(self._generate_description)
        self._description_output = QPlainTextEdit()
        self._description_output.setPlaceholderText("???? + ??????")
        self._description_output.setMinimumHeight(180)
        layout.addWidget(self._description_button)
        layout.addWidget(self._description_output)
        return card

    def _build_avatar_card(self) -> QWidget:
        card = self._section_card("????????", "????????????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._avatar_model_combo = QComboBox()
        self._avatar_model_combo.setEditable(False)
        self._reference_video_combo = QComboBox()
        self._reference_video_combo.setEditable(False)
        self._refresh_reference_button = QPushButton("??????")
        self._refresh_reference_button.clicked.connect(self._refresh_reference_options)
        self._browse_reference_button = QPushButton("??????")
        self._browse_reference_button.clicked.connect(self._pick_reference_video)
        self._batch_size = QSpinBox()
        self._batch_size.setRange(1, 16)
        self._batch_size.setValue(4)
        self._av_offset = QDoubleSpinBox()
        self._av_offset.setRange(-2.0, 2.0)
        self._av_offset.setSingleStep(0.1)
        self._mask_height = QDoubleSpinBox()
        self._mask_height.setRange(0.1, 1.0)
        self._mask_height.setSingleStep(0.05)
        self._mask_height.setValue(0.8)
        self._mask_width = QDoubleSpinBox()
        self._mask_width.setRange(0.1, 1.0)
        self._mask_width.setSingleStep(0.05)
        self._mask_width.setValue(0.8)
        self._compress_checkbox = QCheckBox("压缩推理")
        self._beautify_checkbox = QCheckBox("美化牙齿")
        self._watermark_checkbox = QCheckBox("AI 水印")
        self._avatar_version = QComboBox()
        self._avatar_version.addItems(["标准版", "极速版"])
        self._subtitle_type = QComboBox()
        self._subtitle_type.addItems(["使用已生成字幕", "生成后再加工", "仅视频"])
        reference_widget = QWidget()
        reference_row = QHBoxLayout(reference_widget)
        reference_row.setContentsMargins(0, 0, 0, 0)
        reference_row.setSpacing(8)
        reference_row.addWidget(self._reference_video_combo, 1)
        reference_row.addWidget(self._browse_reference_button)
        reference_row.addWidget(self._refresh_reference_button)
        form.addRow("人物模型", self._avatar_model_combo)
        form.addRow("??????", reference_widget)
        form.addRow("批次大小", self._batch_size)
        form.addRow("音画同步偏移", self._av_offset)
        form.addRow("遮罩高度比例", self._mask_height)
        form.addRow("遮罩宽度比例", self._mask_width)
        form.addRow("?????", self._avatar_version)
        form.addRow("字幕生成类型", self._subtitle_type)
        form.addRow("", self._compress_checkbox)
        form.addRow("", self._beautify_checkbox)
        form.addRow("", self._watermark_checkbox)
        action_row = QHBoxLayout()
        self._generate_avatar_button = QPushButton("???????")
        self._generate_avatar_button.setObjectName("primaryButton")
        self._generate_avatar_button.clicked.connect(self._submit_avatar_task)
        open_video = QPushButton("打开视频")
        open_video.clicked.connect(lambda: self._open_path(self._current_video_path()))
        action_row.addWidget(self._generate_avatar_button)
        action_row.addWidget(open_video)
        self._avatar_output = QPlainTextEdit()
        self._avatar_output.setReadOnly(True)
        self._avatar_output.setMinimumHeight(220)
        self._avatar_compatibility_note = QLabel(
            "??????????????????????????????????????????????"
        )
        self._avatar_compatibility_note.setObjectName("mutedText")
        self._avatar_compatibility_note.setWordWrap(True)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addWidget(self._avatar_output)
        layout.addWidget(self._avatar_compatibility_note)
        return card

    def _build_subtitle_style_card(self) -> QWidget:
        card = self._section_card("???????", "???????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._subtitle_font = QComboBox()
        self._subtitle_font.addItems(FONT_OPTIONS)
        self._subtitle_font_size = QSpinBox()
        self._subtitle_font_size.setRange(18, 88)
        self._subtitle_font_size.setValue(32)
        self._subtitle_margin = QSpinBox()
        self._subtitle_margin.setRange(0, 200)
        self._subtitle_margin.setValue(72)
        self._subtitle_color = ColorInput("#FFFFFF")
        self._subtitle_outline = ColorInput("#000000")
        form.addRow("字体", self._subtitle_font)
        form.addRow("字体大小", self._subtitle_font_size)
        form.addRow("底部边距", self._subtitle_margin)
        form.addRow("字体颜色", self._subtitle_color)
        form.addRow("描边颜色", self._subtitle_outline)
        self._apply_subtitle_button = QPushButton("???????")
        self._apply_subtitle_button.clicked.connect(self._apply_subtitle_style)
        self._subtitle_style_status = QPlainTextEdit()
        self._subtitle_style_status.setReadOnly(True)
        self._subtitle_style_status.setMaximumHeight(120)
        layout.addLayout(form)
        layout.addWidget(self._apply_subtitle_button)
        layout.addWidget(self._subtitle_style_status)
        return card

    def _build_bgm_card(self) -> QWidget:
        card = self._section_card("BGM ???", "??????????????????")
        layout = card.layout()
        top_row = QHBoxLayout()
        self._random_bgm_button = QPushButton("随机选择背景音乐")
        self._random_bgm_button.clicked.connect(self._pick_random_bgm)
        self._skip_bgm_in_auto = QCheckBox("全自动时跳过 BGM")
        top_row.addWidget(self._random_bgm_button)
        top_row.addWidget(self._skip_bgm_in_auto)
        self._bgm_combo = QComboBox()
        self._bgm_combo.setEditable(False)
        self._bgm_upload = QLineEdit()
        self._bgm_upload.setPlaceholderText("??? / ????? BGM ??")
        bgm_upload_row = QHBoxLayout()
        bgm_upload_row.addWidget(self._bgm_upload, 1)
        pick_bgm = QPushButton("选择文件")
        pick_bgm.clicked.connect(self._pick_bgm_file)
        bgm_upload_row.addWidget(pick_bgm)
        self._bgm_volume = QSlider(Qt.Orientation.Horizontal)
        self._bgm_volume.setRange(0, 100)
        self._bgm_volume.setValue(24)
        self._bgm_volume_value = QLabel("24%")
        self._bgm_volume.valueChanged.connect(lambda value: self._bgm_volume_value.setText(f"{value}%"))
        volume_row = QHBoxLayout()
        volume_row.addWidget(self._bgm_volume, 1)
        volume_row.addWidget(self._bgm_volume_value)
        button_row = QHBoxLayout()
        refresh_bgm = QPushButton("刷新背景音乐")
        refresh_bgm.clicked.connect(self._refresh_bgm_options)
        apply_bgm = QPushButton("?????????")
        apply_bgm.clicked.connect(self._apply_bgm)
        button_row.addWidget(refresh_bgm)
        button_row.addWidget(apply_bgm)
        self._bgm_status = QPlainTextEdit()
        self._bgm_status.setReadOnly(True)
        self._bgm_status.setMaximumHeight(120)
        layout.addLayout(top_row)
        layout.addWidget(self._bgm_combo)
        layout.addLayout(bgm_upload_row)
        layout.addLayout(volume_row)
        layout.addLayout(button_row)
        layout.addWidget(self._bgm_status)
        return card

    def _build_cover_card(self) -> QWidget:
        card = self._section_card("??????", "???????????????????????")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)
        self._auto_cover_checkbox = QCheckBox("丢键流程时自动生成封面")
        self._auto_cover_checkbox.setChecked(True)
        self._ai_cover_copy_checkbox = QCheckBox("使用 AI 生成封面文案")
        self._cover_text = QLineEdit()
        self._cover_text.setPlaceholderText("封面文案")
        self._cover_highlight = QLineEdit()
        self._cover_highlight.setPlaceholderText("???")
        self._cover_font = QComboBox()
        self._cover_font.addItems(FONT_OPTIONS)
        self._cover_font_size = QSpinBox()
        self._cover_font_size.setRange(24, 120)
        self._cover_font_size.setValue(68)
        self._cover_font_color = ColorInput("#FFFFFF")
        self._cover_highlight_color = ColorInput("#F59E0B")
        self._cover_position = QComboBox()
        self._cover_position.addItems(["bottom", "center", "top"])
        self._cover_frame_time = QDoubleSpinBox()
        self._cover_frame_time.setRange(0.0, 60.0)
        self._cover_frame_time.setSingleStep(0.5)
        self._publish_with_cover = QCheckBox("?????????")
        self._publish_with_cover.setChecked(True)
        form.addRow("", self._auto_cover_checkbox)
        form.addRow("", self._ai_cover_copy_checkbox)
        form.addRow("封面文案", self._cover_text)
        form.addRow("???", self._cover_highlight)
        form.addRow("字体", self._cover_font)
        form.addRow("字号", self._cover_font_size)
        form.addRow("字体颜色", self._cover_font_color)
        form.addRow("高亮颜色", self._cover_highlight_color)
        form.addRow("位置", self._cover_position)
        form.addRow("?????", self._cover_frame_time)
        form.addRow("", self._publish_with_cover)
        button_row = QHBoxLayout()
        generate_cover = QPushButton("?????")
        generate_cover.clicked.connect(self._generate_cover)
        open_cover = QPushButton("打开封面")
        open_cover.clicked.connect(lambda: self._open_path(self._cover_output_path))
        button_row.addWidget(generate_cover)
        button_row.addWidget(open_cover)
        self._cover_status = QPlainTextEdit()
        self._cover_status.setReadOnly(True)
        self._cover_status.setMaximumHeight(120)
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(self._cover_status)
        return card

    def _submit_content_task(self) -> None:
        raw_input = self._video_link_input.text().strip()
        if not raw_input:
            self._copy_editor.setPlainText("?????????????????????")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        self._app_context.state.source_input = raw_input
        self._source_type_hint.setText(f"妫€娴嬪埌鐨勬潵婧愮被鍨嬶細{detect_source_type(raw_input)}")
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.content,
            ContentRequest(
                source=VideoSource(source_type=detect_source_type(raw_input), raw_input=raw_input),
                workspace=workspace,
            ),
        )

    def _submit_rewrite_task(self) -> None:
        text = self._copy_editor.toPlainText().strip()
        if not text:
            self._rewrite_status.setText("????????????")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        self._apply_env_settings()
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.rewrite_text,
            RewriteRequest(
                text=text,
                mode=self._rewrite_mode.currentData(),
                prompt=self._rewrite_prompt.text().strip() or None,
                model=self._app_context.settings.llm.model,
                workspace=workspace,
            ),
        )

    def _prepare_ultimate_clone_request(self, reference_audio_path: str) -> tuple[bool, str | None] | None:
        checkbox = getattr(self, "_ultimate_clone_checkbox", None)
        ultimate_clone = bool(checkbox.isChecked()) if checkbox is not None else False
        self._app_context.state.ultimate_clone_enabled = ultimate_clone
        if not ultimate_clone:
            return False, None

        if not reference_audio_path or not Path(reference_audio_path).exists():
            self._set_audio_feedback("极致克隆准备失败", "error", f"参考音频不存在：{reference_audio_path}")
            return None
        prompt_text = cached_ultimate_clone_prompt_text(self._app_context, reference_audio_path)
        if not prompt_text:
            self._set_audio_feedback("识别参考音频", "running", "正在识别参考音频文字，用于精准匹配...")
            return None
        return True, prompt_text

    def _submit_tts_task(self) -> None:
        text = self._copy_editor.toPlainText().strip()
        if not text:
            self._set_audio_feedback("待输入", "idle", "请先准备好当前文案。")
            self._audio_output.setText("尚未生成音频文件。")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        voice = combo_value(self._voice_combo) or current_voice(self._app_context)
        self._app_context.state.preferred_voice = voice
        self._audio_player.stop()
        self._set_audio_feedback("生成中", "running", "正在生成音频，请稍候。")
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=voice,
                speed=float(self._voice_speed.value()),
                workspace=workspace,
                output_name="studio-narration",
            ),
        )
        if not task_id:
            self._set_audio_feedback(
                "生成失败",
                "error",
                self._app_context.state.last_error or "音频任务提交失败，请稍后重试?",
            )
            return
        self._prime_loading_button(WorkerTaskKind.tts.value)

    def _submit_subtitle_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._subtitle_output.setPlainText("???????")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.subtitle,
            SubtitleRequest(
                audio_path=audio_path,
                reference_text=self._copy_editor.toPlainText().strip() or None,
                burn_in=False,
                workspace=workspace,
                output_name="studio-subtitle",
            ),
        )

    def _save_subtitle_text(self) -> None:
        try:
            target = self._subtitle_text_target()
        except RuntimeError as exc:
            self._subtitle_output.setPlainText(str(exc))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._subtitle_output.toPlainText(), encoding="utf-8")
        self._subtitle_text_path = str(target)
        self._app_context.state.subtitle_path = str(target)
        self._app_context.task_controller.publish_state()
        self._subtitle_style_status.setPlainText(f"字幕文本已保存到：\n{target}")

    def _generate_description(self) -> None:
        copy_text = self._copy_editor.toPlainText().strip()
        if not copy_text:
            self._description_output.setPlainText("???????")
            return
        self._apply_env_settings()
        client = self._llm_client()
        if client.is_configured():
            try:
                response = client.generate(
                    DESCRIPTION_USER_PROMPT.format(text=copy_text),
                    system_prompt=DESCRIPTION_SYSTEM_PROMPT,
                    model=self._app_context.settings.llm.model,
                    temperature=0.8,
                )
                self._description_output.setPlainText(response.text)
                return
            except Exception as exc:
                fallback = description_fallback(copy_text, self._app_context.state.tags)
                self._description_output.setPlainText(f"{fallback}\n\n[鍥為€€鍘熷洜] {exc}")
                return
        self._description_output.setPlainText(description_fallback(copy_text, self._app_context.state.tags))

    def _submit_avatar_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._avatar_output.setPlainText("???????")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        model_id = combo_value(self._avatar_model_combo) or current_avatar_model(self._app_context)
        self._app_context.state.preferred_avatar_model_id = model_id
        subtitle_path = ""
        if self._subtitle_type.currentText() != "???":
            subtitle_path = self._app_context.state.subtitle_path
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.avatar,
            AvatarRequest(
                audio_path=audio_path,
                model_id=model_id,
                engine=AvatarEngine.tuilionnx,
                workspace=workspace,
                subtitle_path=subtitle_path or None,
                background_video_path=combo_value(self._reference_video_combo) or None,
                overlay_text=self._app_context.state.rewritten_title or None,
            ),
        )

    def _apply_subtitle_style(self) -> bool:
        video_path = self._current_video_path()
        if not video_path:
            self._subtitle_style_status.setPlainText("??????????")
            return False
        subtitle_path = self._ensure_subtitle_file()
        if not subtitle_path:
            self._subtitle_style_status.setPlainText("????????????")
            return False
        style = SubtitleStyle(
            font_name=combo_value(self._subtitle_font),
            font_size=int(self._subtitle_font_size.value()),
            color=self._subtitle_color.text(),
            outline_color=self._subtitle_outline.text(),
            bottom_margin=int(self._subtitle_margin.value()),
        )
        workspace = self._require_workspace()
        if not workspace:
            return False
        output_dir = ensure_module_dir(workspace, "subtitle")
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_styled.mp4"
        rendered = self._ffmpeg.burn_subtitles(video_path, subtitle_path, output_path, style=style)
        self._app_context.state.final_video_path = str(rendered)
        self._app_context.task_controller.publish_state()
        self._subtitle_style_status.setPlainText(
            f"字幕已添加到视频。\n输出路径：{rendered}\n字幕文件：{subtitle_path}"
        )
        return True

    def _apply_bgm(self) -> bool:
        video_path = self._current_video_path()
        if not video_path:
            self._bgm_status.setPlainText("?????????")
            return False
        bgm_path = self._selected_bgm_path()
        if not bgm_path:
            self._bgm_status.setPlainText("????????????")
            return False
        output_dir = ensure_module_dir(
            self._workspace_input.text().strip() or self._app_context.state.workspace,
            "bgm",
        )
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_bgm.mp4"
        rendered = self._ffmpeg.mix_background_music(
            video_path,
            bgm_path,
            output_path,
            bgm_volume=float(self._bgm_volume.value()) / 100.0,
        )
        self._app_context.state.final_video_path = str(rendered)
        self._app_context.task_controller.publish_state()
        self._bgm_status.setPlainText(f"BGM 已应用到视频\n视频：{rendered}\n音频：{bgm_path}")
        return True

    def _generate_cover(self) -> bool:
        video_path = self._current_video_path() or self._app_context.state.source_video_path
        if not video_path:
            self._cover_status.setPlainText("??????????")
            return False
        copy_text = self._copy_editor.toPlainText().strip()
        if self._ai_cover_copy_checkbox.isChecked() and copy_text:
            headline, highlight = self._generate_cover_copy(copy_text)
            if not self._cover_text.text().strip():
                self._cover_text.setText(headline)
            if not self._cover_highlight.text().strip():
                self._cover_highlight.setText(highlight)
        title = self._cover_text.text().strip() or title_from_description(self._description_output.toPlainText())
        highlight = self._cover_highlight.text().strip()
        output_dir = ensure_module_dir(
            self._workspace_input.text().strip() or self._app_context.state.workspace,
            "cover",
        )
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_cover.png"
        rendered = self._ffmpeg.render_cover_image(
            video_path,
            output_path,
            timestamp_sec=float(self._cover_frame_time.value()),
            title=title,
            highlight_text=highlight,
            font_name=combo_value(self._cover_font),
            font_size=int(self._cover_font_size.value()),
            font_color=self._cover_font_color.text(),
            highlight_color=self._cover_highlight_color.text(),
            position=combo_value(self._cover_position),
        )
        self._cover_output_path = str(rendered)
        self._cover_generated_for_publish = True
        self._cover_status.setPlainText(f"封面已生成并保存\n{rendered}")
        return True

    def _publish(self, platform: PublishPlatform | None) -> None:
        video_path = self._current_video_path()
        if not video_path:
            self._publish_status.setPlainText("??????????")
            return
        description = self._description_output.toPlainText().strip()
        if not description:
            self._publish_status.setPlainText("????????????")
            return
        cover_path = (
            self._cover_output_path
            if self._publish_with_cover.isChecked() and self._cover_generated_for_publish
            else None
        )
        platforms = (
            [platform]
            if platform is not None
            else [
                PublishPlatform.douyin,
                PublishPlatform.xiaohongshu,
                PublishPlatform.wechat_channels,
            ]
        )
        tags = [tag for tag in description.split() if tag.startswith("#")]
        title = title_from_description(description)
        lines: list[str] = []
        for item in platforms:
            result = self._app_context.services.publish.publish(
                PublishRequest(
                    platform=item,
                    video_path=video_path,
                    title=title,
                    tags=tags,
                    cover_path=cover_path,
                )
            )
            line = f"{item.value}: {result.status or 'unknown'}"
            if result.error:
                line += f" / {result.error.message}"
            lines.append(line)
        self._publish_status.setPlainText("\n".join(lines))

    def _run_all_in_one(self) -> None:
        source_text = self._video_link_input.text().strip()
        if not source_text:
            self._all_in_one_status.setPlainText("??????????????")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        request = GenerateVideoWorkflowRequest(
            source=VideoSource(source_type=detect_source_type(source_text), raw_input=source_text),
            rewrite_mode=self._rewrite_mode.currentData(),
            rewrite_prompt=self._rewrite_prompt.text().strip() or None,
            rewrite_model=self._app_context.settings.llm.model,
            voice=combo_value(self._voice_combo) or current_voice(self._app_context),
            voice_speed=float(self._voice_speed.value()),
            avatar_model_id=combo_value(self._avatar_model_combo) or current_avatar_model(self._app_context),
            avatar_engine=AvatarEngine.tuilionnx,
            subtitle_burn_in=self._subtitle_type.currentText() == "???????",
            reference_video_path=combo_value(self._reference_video_combo) or None,
            workspace=workspace,
        )
        self._auto_pipeline_active = True
        self._auto_pipeline_log = ["???????????????"]
        self._all_in_one_status.setPlainText("\n".join(self._auto_pipeline_log))
        self._app_context.task_controller.submit_task(WorkerTaskKind.full_workflow, request)

    def _refresh_voice_options(self) -> None:
        self._app_context.state.preferred_voice = CLONE_REFERENCE_VOICE

    def _refresh_avatar_options(self) -> None:
        selected = combo_value(self._avatar_model_combo) or current_avatar_model(self._app_context)
        self._avatar_model_combo.clear()
        for model_id in list_avatar_models(self._app_context):
            self._avatar_model_combo.addItem(model_id, model_id)
        if selected:
            index = self._avatar_model_combo.findData(selected)
            if index >= 0:
                self._avatar_model_combo.setCurrentIndex(index)
            else:
                self._avatar_model_combo.setEditText(selected)

    def _refresh_reference_options(self) -> None:
        selected = combo_value(self._reference_video_combo)
        self._reference_video_combo.clear()
        self._reference_video_combo.addItem("自动使用导入视频", "")
        for path in list_reference_videos(self._app_context):
            self._reference_video_combo.addItem(Path(path).name, path)
        if selected:
            index = self._reference_video_combo.findData(selected)
            if index >= 0:
                self._reference_video_combo.setCurrentIndex(index)
            else:
                self._reference_video_combo.setEditText(selected)

    def _refresh_bgm_options(self) -> None:
        selected = combo_value(self._bgm_combo) or self._app_context.state.preferred_bgm
        self._bgm_combo.clear()
        self._bgm_combo.addItem("请选择背景音乐", "")
        for path in list_bgm_candidates(self._app_context):
            self._bgm_combo.addItem(Path(path).name, path)
        if selected:
            index = self._bgm_combo.findData(selected)
            if index >= 0:
                self._bgm_combo.setCurrentIndex(index)
            else:
                self._bgm_combo.setEditText(selected)

    def _pick_reference_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "??????",
            str(Path(self._workspace_input.text().strip() or Path.cwd())),
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm)",
        )
        if file_path:
            self._reference_video_combo.setEditText(file_path)

    def _pick_bgm_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景音乐",
            str(Path(self._workspace_input.text().strip() or Path.cwd())),
            "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac)",
        )
        if file_path:
            self._bgm_upload.setText(file_path)

    def _pick_random_bgm(self) -> None:
        candidates = list_bgm_candidates(self._app_context)
        if not candidates:
            self._bgm_status.setPlainText("?????? BGM ???")
            return
        path = choice(candidates)
        index = self._bgm_combo.findData(path)
        if index >= 0:
            self._bgm_combo.setCurrentIndex(index)
        else:
            self._bgm_combo.setEditText(path)
        self._bgm_status.setPlainText(f"宸查殢鏈洪€夋嫨锛{path}")

    def _service_action(self, service_name: str) -> None:
        if service_name == "cosyvoice":
            base_url = self._app_context.settings.tts.base_url
            self._ops_status.setPlainText(
                f"??????? cosyvoice ?????\n???????????????????\n?????{base_url}"
            )
            return
        avatar_root = self._app_context.settings.avatar.tuilionnx_root
        self._ops_status.setPlainText(
            "??????? digit_human ???????\n"
            f"?????????????{avatar_root or '???'}"
        )

    def _check_project_update(self) -> None:
        try:
            result = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=str(Path(__file__).resolve().parents[4]),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                self._ops_status.setPlainText(result.stdout.strip() or "??????????????")
                return
        except Exception as exc:
            self._ops_status.setPlainText(f"运行脚本失败：{exc}")
            return
        self._ops_status.setPlainText("???????? Git ?????")

    def _sync_state(self, state: PipelineState) -> None:
        source_text = self._video_link_input.text().strip()
        source_type = detect_source_type(source_text) if source_text else "???"
        self._source_type_hint.setText(f"妫€娴嬪埌鐨勬潵婧愮被鍨嬶細{source_type}")
        self._sync_audio_source(state.audio_path)
        if state.subtitle_path and not self._subtitle_output.toPlainText().strip():
            path = Path(state.subtitle_path)
            if path.exists():
                self._subtitle_output.setPlainText(path.read_text(encoding="utf-8"))
        self._generate_audio_button.setEnabled(not state.is_running)
        self._generate_subtitle_button.setEnabled(not state.is_running)
        self._extract_button.setEnabled(not state.is_running)
        self._generate_avatar_button.setEnabled(not state.is_running)
        self._all_in_one_button.setEnabled(not state.is_running)
        self._cancel_button.setEnabled(state.is_running and state.is_cancellable)
        self._avatar_output.setPlainText(self._avatar_status_text(state))

    def _handle_task_event(self, event: object) -> None:
        if not isinstance(event, WorkerEvent):
            return
        if event.task_kind is WorkerTaskKind.tts:
            if event.event in {WorkerEventType.started, WorkerEventType.progress}:
                self._set_audio_feedback("生成中", "running", event.message or "正在生成音频，请稍候。")
                return
            if event.event is WorkerEventType.failed:
                error_text = event.error.message if event.error else (event.message or "音频生成失败?")
                self._set_audio_feedback("生成失败", "error", error_text)
                return
            if event.event is WorkerEventType.cancelled:
                self._set_audio_feedback("宸插彇娑?", "idle", event.message or "音频生成已取消?")
                return
            if event.event is WorkerEventType.succeeded and "tts" in event.payload:
                result = TTSResult.model_validate(event.payload["tts"])
                self._set_audio_feedback("生成成功", "success", "可以直接播放，也可以拖动进度条择试听片段?")
                self._sync_audio_source(result.audio_path or self._app_context.state.audio_path)
                return
        if event.event is not WorkerEventType.succeeded:
            return
        if event.task_kind is WorkerTaskKind.content and "content" in event.payload:
            result = ContentResult.model_validate(event.payload["content"])
            if result.extracted_copy:
                self._copy_editor.setPlainText(result.extracted_copy.cleaned_text)
            return
        if event.task_kind in {WorkerTaskKind.rewrite_text, WorkerTaskKind.rewrite}:
            rewrite_payload = event.payload.get("rewrite", {})
            rewritten = rewrite_payload.get("rewritten_text", "")
            if rewritten:
                self._copy_editor.setPlainText(str(rewritten))
            return
        if event.task_kind is WorkerTaskKind.subtitle and "subtitle" in event.payload:
            result = SubtitleResult.model_validate(event.payload["subtitle"])
            if result.subtitle_text:
                self._subtitle_output.setPlainText(result.subtitle_text)
            self._subtitle_text_path = result.srt_path or self._subtitle_text_path
            return
        if event.task_kind is WorkerTaskKind.avatar and "avatar" in event.payload:
            result = AvatarResult.model_validate(event.payload["avatar"])
            self._latest_elapsed_sec = float(result.elapsed_sec or 0.0)
            self._latest_download_url = str(result.download_url or "")
            return
        if event.task_kind is WorkerTaskKind.full_workflow and "workflow" in event.payload:
            workflow = event.payload["workflow"]
            artifacts = workflow.get("artifacts", {})
            content = artifacts.get("content")
            rewrite = artifacts.get("rewrite")
            tts = artifacts.get("tts")
            subtitle = artifacts.get("subtitle")
            avatar = artifacts.get("avatar")
            if content and content.get("extracted_copy"):
                self._copy_editor.setPlainText(content["extracted_copy"].get("cleaned_text", ""))
            if rewrite and rewrite.get("rewritten_text"):
                self._copy_editor.setPlainText(str(rewrite["rewritten_text"]))
            if tts and tts.get("audio_path"):
                self._set_audio_feedback("生成成功", "success", "可以直接播放，也可以拖动进度条择试听片段?")
                self._sync_audio_source(str(tts["audio_path"]))
            if subtitle and subtitle.get("subtitle_text"):
                self._subtitle_output.setPlainText(str(subtitle["subtitle_text"]))
                self._subtitle_text_path = str(subtitle.get("srt_path") or self._subtitle_text_path)
            if avatar:
                avatar_result = AvatarResult.model_validate(avatar)
                self._latest_elapsed_sec = float(avatar_result.elapsed_sec or 0.0)
                self._latest_download_url = str(avatar_result.download_url or "")
            if self._auto_pipeline_active:
                self._continue_auto_pipeline()

    def _continue_auto_pipeline(self) -> None:
        self._auto_pipeline_log.append("??????????????????????")
        if not self._description_output.toPlainText().strip():
            self._generate_description()
            self._auto_pipeline_log.append("???????????")
        if self._subtitle_type.currentText() != "仅视?":
            try:
                if self._apply_subtitle_style():
                    self._auto_pipeline_log.append("????????")
            except Exception as exc:
                self._auto_pipeline_log.append(f"字幕后处理失败：{exc}")
        if not self._skip_bgm_in_auto.isChecked():
            try:
                if self._apply_bgm():
                    self._auto_pipeline_log.append("????????")
            except Exception as exc:
                self._auto_pipeline_log.append(f"BGM 鍔犲伐璺宠繃锛{exc}")
        if self._auto_cover_checkbox.isChecked():
            try:
                if self._generate_cover():
                    self._auto_pipeline_log.append("???????")
            except Exception as exc:
                self._auto_pipeline_log.append(f"封面生成失败：{exc}")
        self._publish(None)
        publish_text = self._publish_status.toPlainText().strip()
        if publish_text:
            self._auto_pipeline_log.append("?????")
            self._auto_pipeline_log.extend(publish_text.splitlines())
        self._all_in_one_status.setPlainText("\n".join(self._auto_pipeline_log))
        self._auto_pipeline_active = False

    def _avatar_status_text(self, state: PipelineState) -> str:
        current_path = self._current_video_path()
        lines = [
            f"鐘舵€侊細{status_text(state.status)}",
            f"当前视频{current_path or '暂无'}",
            f"数字人视频：{state.avatar_video_path or '暂无'}",
            f"朢终视频：{state.final_video_path or '暂无'}",
        ]
        if self._latest_elapsed_sec:
            lines.append(f"?????{self._latest_elapsed_sec:.2f} ?")
        if self._latest_download_url:
            lines.append(f"分享下载 URL锛{self._latest_download_url}")
        if current_path:
            lines.append("")
            lines.append(file_summary(current_path))
        return "\n".join(lines)

    def _selected_bgm_path(self) -> str:
        upload = self._bgm_upload.text().strip()
        if upload:
            return upload
        return combo_value(self._bgm_combo)

    def _current_video_path(self) -> str:
        for path in [
            self._app_context.state.final_video_path,
            self._app_context.state.avatar_video_path,
            self._app_context.state.source_video_path,
        ]:
            if path:
                return path
        return ""

    def _subtitle_text_target(self) -> Path:
        if self._subtitle_text_path:
            return Path(self._subtitle_text_path)
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        return ensure_module_dir(workspace, "subtitle") / "manual-subtitle.srt"

    def _ensure_subtitle_file(self) -> str:
        if self._subtitle_output.toPlainText().strip():
            self._save_subtitle_text()
        return self._app_context.state.subtitle_path or self._subtitle_text_path

    def _generate_cover_copy(self, copy_text: str) -> tuple[str, str]:
        self._apply_env_settings()
        client = self._llm_client()
        if client.is_configured():
            try:
                response = client.generate(
                    HOOK_USER_PROMPT.format(text=copy_text),
                    system_prompt=HOOK_SYSTEM_PROMPT,
                    model=self._app_context.settings.llm.model,
                    temperature=0.8,
                )
                lines = [line.strip() for line in response.text.splitlines() if line.strip()]
                if lines:
                    highlight = lines[1] if len(lines) > 1 else lines[0][:4]
                    return lines[0][:12], highlight[:4]
            except Exception:
                pass
        return heuristic_cover_text(copy_text, self._app_context.state.tags)

    def _load_api_keys(self) -> None:
        store_path = self._api_key_store_path()
        if store_path.exists():
            try:
                payload = json.loads(store_path.read_text(encoding="utf-8"))
                self._api_keys = [item for item in payload if item]
            except Exception:
                self._api_keys = []
        current_key = self._app_context.settings.llm.api_key or ""
        if current_key and current_key not in self._api_keys:
            self._api_keys.append(current_key)
        self._api_key_combo.blockSignals(True)
        self._api_key_combo.clear()
        self._api_key_combo.addItem("未择", "")
        for index, key in enumerate(self._api_keys, start=1):
            label = f"Key {index} / ...{key[-6:]}" if len(key) > 6 else f"Key {index}"
            self._api_key_combo.addItem(label, key)
        selected = self._app_context.settings.llm.api_key or ""
        combo_index = self._api_key_combo.findData(selected)
        self._api_key_combo.setCurrentIndex(combo_index if combo_index >= 0 else 0)
        self._api_key_combo.blockSignals(False)
        self._api_status.setText(
            "??? API Key ???" if self._api_keys else "??? API Key??? AI ??????????????"
        )

    def _save_api_key(self) -> None:
        key = self._api_key_input.text().strip()
        if not key:
            self._api_status.setText("??????? API Key?")
            return
        if key not in self._api_keys:
            self._api_keys.append(key)
        self._write_api_keys()
        self._app_context.settings.llm.api_key = key
        self._apply_env_settings()
        self._load_api_keys()
        self._api_status.setText("API Key ????????????? Key?")

    def _delete_api_key(self) -> None:
        key = self._api_key_combo.currentData()
        if not key:
            self._api_status.setText("???????? Key?")
            return
        self._api_keys = [item for item in self._api_keys if item != key]
        if self._app_context.settings.llm.api_key == key:
            self._app_context.settings.llm.api_key = None
        self._write_api_keys()
        self._apply_env_settings()
        self._load_api_keys()
        self._api_status.setText("?????? API Key?")

    def _apply_selected_api_key(self) -> None:
        key = self._api_key_combo.currentData()
        self._app_context.settings.llm.api_key = key or None
        self._apply_env_settings()
        self._api_status.setText("????? API Key?" if key else "????? API Key?")

    def _api_key_store_path(self) -> Path:
        root = (
            Path(self._app_context.settings.data_dir)
            if self._app_context.settings.data_dir
            else Path.cwd() / "data"
        )
        store_dir = root / "gui"
        store_dir.mkdir(parents=True, exist_ok=True)
        return store_dir / "api_keys.json"

    def _write_api_keys(self) -> None:
        self._api_key_store_path().write_text(
            json.dumps(self._api_keys, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _apply_env_settings(self) -> None:
        if self._app_context.settings.llm.api_key:
            os.environ["SANTISZR_LLM_API_KEY"] = self._app_context.settings.llm.api_key
        else:
            os.environ.pop("SANTISZR_LLM_API_KEY", None)
        os.environ["SANTISZR_LLM_API_BASE"] = self._app_context.settings.llm.api_base
        os.environ["SANTISZR_LLM_MODEL"] = self._app_context.settings.llm.model

    def _llm_client(self) -> LLMClient:
        return LLMClient(
            api_key=self._app_context.settings.llm.api_key,
            api_base=self._app_context.settings.llm.api_base,
            model=self._app_context.settings.llm.model,
            timeout_sec=self._app_context.settings.llm.timeout_sec,
        )

    def _open_path(self, path_text: str) -> None:
        if not path_text:
            return
        target = Path(path_text)
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))

    def refresh_options(self) -> None:
        self._refresh_audio_library_options()
        self._refresh_voice_options()
        self._refresh_avatar_options()
        self._refresh_reference_options()
        self._refresh_bgm_options()
        self._load_api_keys()

    def _build_audio_card(self) -> QWidget:
        card = self._section_card("音色与音频生成区", "既可以生成新音频，也可以直接使用音频管理页里上传的现有音频。")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)

        self._managed_audio_combo = QComboBox()
        self._managed_audio_combo.setEditable(False)
        self._refresh_audio_library_button = QPushButton("刷新音频")
        self._refresh_audio_library_button.clicked.connect(self._refresh_audio_library_options)
        self._use_managed_audio_button = QPushButton("使用所选音频")
        self._use_managed_audio_button.clicked.connect(self._use_selected_audio)
        managed_audio_row = QWidget()
        managed_audio_layout = QHBoxLayout(managed_audio_row)
        managed_audio_layout.setContentsMargins(0, 0, 0, 0)
        managed_audio_layout.setSpacing(8)
        managed_audio_layout.addWidget(self._managed_audio_combo, 1)
        managed_audio_layout.addWidget(self._use_managed_audio_button)
        managed_audio_layout.addWidget(self._refresh_audio_library_button)

        self._clone_voice_hint = QLabel("当前默认使用参考音频克隆。请先在音频管理页上传参考音频，再回到这里开始生成。")
        self._clone_voice_hint.setObjectName("mutedText")
        self._clone_voice_hint.setWordWrap(True)
        self._voice_speed = QDoubleSpinBox()
        self._voice_speed.setRange(0.5, 2.0)
        self._voice_speed.setSingleStep(0.1)
        self._voice_speed.setValue(1.0)
        self._ultimate_clone_checkbox = QCheckBox("极致克隆 / 精准匹配")
        self._ultimate_clone_checkbox.setChecked(False)
        self._ultimate_clone_checkbox.setToolTip("开启后自动识别参考音频文字，提高音色和语气相似度；第一次会慢一点。")
        self._voice_speed.setEnabled(False)
        self._voice_speed.setToolTip("当前引擎暂不支持直接调节语速，生成时会保持克隆声音的原始节奏。")

        form.addRow("已管理音频", managed_audio_row)
        form.addRow("声音来源", self._clone_voice_hint)
        form.addRow("语速", self._voice_speed)
        form.addRow("", self._ultimate_clone_checkbox)

        action_row = QHBoxLayout()
        self._generate_audio_button = QPushButton("生成音频")
        self._generate_audio_button.setObjectName("primaryButton")
        self._generate_audio_button.clicked.connect(self._submit_tts_task)
        action_row.addWidget(self._generate_audio_button)
        action_row.addStretch(1)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._audio_status_badge = QLabel()
        self._audio_status_badge.setObjectName("statusPill")
        self._audio_status_hint = QLabel()
        self._audio_status_hint.setObjectName("mutedText")
        self._audio_status_hint.setWordWrap(True)
        status_row.addWidget(self._audio_status_badge, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self._audio_status_hint, 1)

        self._audio_preview_card = QFrame()
        self._audio_preview_card.setObjectName("audioPreviewCard")
        preview_layout = QVBoxLayout(self._audio_preview_card)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(10)

        player_row = QHBoxLayout()
        player_row.setSpacing(12)
        self._play_audio_button = QPushButton("播放")
        self._play_audio_button.setObjectName("playButton")
        self._play_audio_button.clicked.connect(self._toggle_audio_playback)
        self._audio_progress = QSlider(Qt.Orientation.Horizontal)
        self._audio_progress.setRange(0, 0)
        self._audio_progress.setEnabled(False)
        self._audio_progress.sliderPressed.connect(self._on_audio_slider_pressed)
        self._audio_progress.sliderReleased.connect(self._on_audio_slider_released)
        self._audio_progress.sliderMoved.connect(self._seek_audio)
        self._audio_timeline = QLabel("00:00 / 00:00")
        self._audio_timeline.setObjectName("timeLabel")
        player_row.addWidget(self._play_audio_button)
        player_row.addWidget(self._audio_progress, 1)
        player_row.addWidget(self._audio_timeline)

        self._audio_output = QLabel()
        self._audio_output.setObjectName("mutedText")
        self._audio_output.setWordWrap(True)

        preview_layout.addLayout(player_row)
        preview_layout.addWidget(self._audio_output)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addLayout(status_row)
        layout.addWidget(self._audio_preview_card)
        return card

    def _build_avatar_card(self) -> QWidget:
        card = self._section_card("数字人视频生成区", "参视频只读取参视频管理页里上传的内容?")
        layout = card.layout()
        form = QFormLayout()
        form.setSpacing(10)

        self._avatar_model_combo = QComboBox()
        self._avatar_model_combo.setEditable(False)
        self._reference_video_combo = QComboBox()
        self._reference_video_combo.setEditable(False)
        self._refresh_reference_button = QPushButton("刷新参视?")
        self._refresh_reference_button.clicked.connect(self._refresh_reference_options)
        self._batch_size = QSpinBox()
        self._batch_size.setRange(1, 16)
        self._batch_size.setValue(4)
        self._av_offset = QDoubleSpinBox()
        self._av_offset.setRange(-2.0, 2.0)
        self._av_offset.setSingleStep(0.1)
        self._mask_height = QDoubleSpinBox()
        self._mask_height.setRange(0.1, 1.0)
        self._mask_height.setSingleStep(0.05)
        self._mask_height.setValue(0.8)
        self._mask_width = QDoubleSpinBox()
        self._mask_width.setRange(0.1, 1.0)
        self._mask_width.setSingleStep(0.05)
        self._mask_width.setValue(0.8)
        self._beautify_checkbox = QCheckBox("美化牙齿")
        self._subtitle_type = QComboBox()
        self._subtitle_type.addItems(["自动生成并烧录字幕", "生成后再加工", "仅视频"])

        reference_widget = QWidget()
        reference_row = QHBoxLayout(reference_widget)
        reference_row.setContentsMargins(0, 0, 0, 0)
        reference_row.setSpacing(8)
        reference_row.addWidget(self._reference_video_combo, 1)
        reference_row.addWidget(self._refresh_reference_button)

        form.addRow("人物模型", self._avatar_model_combo)
        form.addRow("参视?, reference_widget")
        form.addRow("批次大小", self._batch_size)
        form.addRow("音画同步偏移", self._av_offset)
        form.addRow("遮罩高度比例", self._mask_height)
        form.addRow("遮罩宽度比例", self._mask_width)
        form.addRow("鏁板瓧浜虹増鏈?, self._avatar_version")
        form.addRow("字幕生成类型", self._subtitle_type)
        form.addRow("", self._compress_checkbox)
        form.addRow("", self._beautify_checkbox)
        form.addRow("", self._watermark_checkbox)

        action_row = QHBoxLayout()
        self._generate_avatar_button = QPushButton("生成数字人视?")
        self._generate_avatar_button.setObjectName("primaryButton")
        self._generate_avatar_button.clicked.connect(self._submit_avatar_task)
        open_video = QPushButton("打开视频")
        open_video.clicked.connect(lambda: self._open_path(self._current_video_path()))
        action_row.addWidget(self._generate_avatar_button)
        action_row.addWidget(open_video)

        self._avatar_output = QPlainTextEdit()
        self._avatar_output.setReadOnly(True)
        self._avatar_output.setMinimumHeight(220)
        self._avatar_compatibility_note = QLabel(
            "参视频下拉只显示参视频管理页里的资源；如果为空，请先去管理页上传?"
        )
        self._avatar_compatibility_note.setObjectName("mutedText")
        self._avatar_compatibility_note.setWordWrap(True)

        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addWidget(self._avatar_output)
        layout.addWidget(self._avatar_compatibility_note)
        return card

    def _build_bgm_card(self) -> QWidget:
        card = self._section_card("BGM 娣诲姞鍖?", "背景音乐只读取背景音乐管理页里上传的内容?")
        layout = card.layout()
        top_row = QHBoxLayout()
        self._random_bgm_button = QPushButton("随机选择背景音乐")
        self._random_bgm_button.clicked.connect(self._pick_random_bgm)
        self._skip_bgm_in_auto = QCheckBox("全自动时跳过 BGM")
        top_row.addWidget(self._random_bgm_button)
        top_row.addWidget(self._skip_bgm_in_auto)

        self._bgm_combo = QComboBox()
        self._bgm_combo.setEditable(False)
        self._bgm_volume = QSlider(Qt.Orientation.Horizontal)
        self._bgm_volume.setRange(0, 100)
        self._bgm_volume.setValue(24)
        self._bgm_volume_value = QLabel("24%")
        self._bgm_volume.valueChanged.connect(lambda value: self._bgm_volume_value.setText(f"{value}%"))
        volume_row = QHBoxLayout()
        volume_row.addWidget(self._bgm_volume, 1)
        volume_row.addWidget(self._bgm_volume_value)

        button_row = QHBoxLayout()
        refresh_bgm = QPushButton("刷新背景音乐")
        refresh_bgm.clicked.connect(self._refresh_bgm_options)
        apply_bgm = QPushButton("添加背景音乐到视?")
        apply_bgm.clicked.connect(self._apply_bgm)
        button_row.addWidget(refresh_bgm)
        button_row.addWidget(apply_bgm)

        self._bgm_status = QPlainTextEdit()
        self._bgm_status.setReadOnly(True)
        self._bgm_status.setMaximumHeight(120)

        layout.addLayout(top_row)
        layout.addWidget(self._bgm_combo)
        layout.addLayout(volume_row)
        layout.addLayout(button_row)
        layout.addWidget(self._bgm_status)
        return card

    def _refresh_audio_library_options(self) -> None:
        available = list_audio_candidates(self._app_context)
        selected = combo_value(self._managed_audio_combo) or self._app_context.state.preferred_audio
        if not selected and self._app_context.state.audio_path in available:
            selected = self._app_context.state.audio_path

        self._managed_audio_combo.clear()
        self._managed_audio_combo.addItem("请选择已管理音频", "")
        for path in available:
            self._managed_audio_combo.addItem(Path(path).name, path)

        if selected:
            index = self._managed_audio_combo.findData(selected)
            if index >= 0:
                self._managed_audio_combo.setCurrentIndex(index)
                return
        self._managed_audio_combo.setCurrentIndex(0)

    def _use_selected_audio(self) -> None:
        audio_path = combo_value(self._managed_audio_combo)
        if not audio_path:
            self._set_audio_feedback("寰呴€夋嫨", "idle", "请先在音频管理页上传音频，并在这里择丢条资源?")
            return
        if not Path(audio_path).exists():
            self._set_audio_feedback("文件缺失", "error", f"音频文件不存在：{audio_path}")
            return

        self._app_context.state.preferred_audio = audio_path
        self._app_context.state.audio_path = audio_path
        self._sync_audio_source(audio_path)
        self._set_audio_feedback("已设为当前", "success", "已将所选音频加入当前流程，可直接生成或试听。")
        self._app_context.task_controller.publish_state()

    def _submit_tts_task(self) -> None:
        text = self._copy_editor.toPlainText().strip()
        if not text:
            self._set_audio_feedback("待输入", "idle", "请先准备好当前文案。")
            self._audio_output.setText("暂未生成音频文件。")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        voice = combo_value(self._voice_combo) or current_voice(self._app_context)
        self._app_context.state.preferred_voice = voice
        self._app_context.state.preferred_audio = ""
        self._refresh_audio_library_options()
        self._audio_player.stop()
        self._set_audio_feedback("生成中", "running", "正在生成音频，请稍候。")
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=voice,
                speed=float(self._voice_speed.value()),
                workspace=workspace,
                output_name="studio-narration",
            ),
        )
        if not task_id:
            self._set_audio_feedback(
                "生成失败",
                "error",
                self._app_context.state.last_error or "音频任务提交失败，请稍后重试?",
            )

    def _submit_subtitle_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._subtitle_output.setPlainText("请先生成音频，或从音频管理中选择丢条音频?")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.subtitle,
            SubtitleRequest(
                audio_path=audio_path,
                reference_text=self._copy_editor.toPlainText().strip() or None,
                burn_in=False,
                workspace=workspace,
                output_name="studio-subtitle",
            ),
        )

    def _submit_avatar_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._avatar_output.setPlainText("请先生成音频，或从音频管理中选择丢条音频?")
            return
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        model_id = combo_value(self._avatar_model_combo) or current_avatar_model(self._app_context)
        self._app_context.state.preferred_avatar_model_id = model_id
        subtitle_path = ""
        if self._subtitle_type.currentText() != "仅视?":
            subtitle_path = self._app_context.state.subtitle_path
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.avatar,
            AvatarRequest(
                audio_path=audio_path,
                model_id=model_id,
                engine=AvatarEngine.tuilionnx,
                workspace=workspace,
                subtitle_path=subtitle_path or None,
                background_video_path=combo_value(self._reference_video_combo) or None,
                overlay_text=self._app_context.state.rewritten_title or None,
            ),
        )

    def _apply_bgm(self) -> bool:
        video_path = self._current_video_path()
        if not video_path:
            self._bgm_status.setPlainText("请先准备视频输出?")
            return False
        bgm_path = self._selected_bgm_path()
        if not bgm_path:
            self._bgm_status.setPlainText("请先在背景音乐管理页上传音乐，并在这里择丢条?")
            return False
        output_dir = ensure_module_dir(
            self._workspace_input.text().strip() or self._app_context.state.workspace,
            "bgm",
        )
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_bgm.mp4"
        rendered = self._ffmpeg.mix_background_music(
            video_path,
            bgm_path,
            output_path,
            bgm_volume=float(self._bgm_volume.value()) / 100.0,
        )
        self._app_context.state.final_video_path = str(rendered)
        self._app_context.task_controller.publish_state()
        self._bgm_status.setPlainText(f"BGM 已应用到视频\n视频：{rendered}\n音频：{bgm_path}")
        return True

    def _refresh_reference_options(self) -> None:
        selected = combo_value(self._reference_video_combo) or self._app_context.state.preferred_reference_video
        self._reference_video_combo.clear()
        self._reference_video_combo.addItem("自动使用导入视频", "")
        for path in list_reference_videos(self._app_context):
            self._reference_video_combo.addItem(Path(path).name, path)
        if selected:
            index = self._reference_video_combo.findData(selected)
            if index >= 0:
                self._reference_video_combo.setCurrentIndex(index)
                return
        self._reference_video_combo.setCurrentIndex(0)

    def _refresh_bgm_options(self) -> None:
        selected = combo_value(self._bgm_combo) or self._app_context.state.preferred_bgm
        self._bgm_combo.clear()
        self._bgm_combo.addItem("请选择背景音乐", "")
        for path in list_bgm_candidates(self._app_context):
            self._bgm_combo.addItem(Path(path).name, path)
        if selected:
            index = self._bgm_combo.findData(selected)
            if index >= 0:
                self._bgm_combo.setCurrentIndex(index)
                return
        self._bgm_combo.setCurrentIndex(0)

    def _selected_bgm_path(self) -> str:
        return combo_value(self._bgm_combo)

    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ffmpeg = FFmpegAdapter(
            ffmpeg_path=app_context.settings.media.ffmpeg_path,
            ffprobe_path=app_context.settings.media.ffprobe_path,
        )
        self._subtitle_text_path = ""
        self._cover_output_path = ""
        self._cover_generated_for_publish = False
        self._cover_preview_frame_path = ""
        self._cover_preview_frame_key = ""
        self._latest_download_url = ""
        self._latest_elapsed_sec = 0.0
        self._auto_pipeline_active = False
        self._auto_pipeline_log: list[str] = []
        self._audio_loaded_path = ""
        self._audio_loaded_mtime_ns = 0
        self._audio_restore_path_after_preview = ""
        self._audio_slider_is_dragging = False
        self._avatar_video_output_device = None
        self._avatar_video_player = None
        self._avatar_video_loaded_path = ""
        self._avatar_video_loaded_mtime_ns = 0
        self._avatar_video_has_started = False
        self._pending_avatar_after_subtitle = False
        self._syncing_audio_list = False
        self._copy_versions: list[tuple[str, str]] = []
        self._pending_tts_voice = ""
        self._pending_tts_speed = None
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token = 0
        self._ultimate_clone_prepare_reference = ""
        self._ultimate_clone_prepare_task_kind = ""
        self._loading_frames = (".", "..", "...", "....")
        self._loading_frame_index = 0
        self._loading_button_specs: dict[str, tuple[QPushButton, str]] = {}
        self._primed_loading_task_kind = ""
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(140)
        self._loading_timer.timeout.connect(self._advance_loading_buttons)
        self._build_ui()
        self._register_loading_button(WorkerTaskKind.content.value, self._extract_button, "提取文案")
        self._register_loading_button(WorkerTaskKind.tts.value, self._generate_audio_button, "生成音频")
        self._register_loading_button(WorkerTaskKind.avatar.value, self._generate_avatar_button, "生成视频")
        self._register_loading_button(
            WorkerTaskKind.full_workflow.value,
            self._all_in_one_button,
            "一键完成流程",
        )
        self._configure_numeric_inputs()
        self._setup_audio_player()
        self._setup_avatar_video_player()
        self.refresh_options()
        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._app_context.task_controller.task_event.connect(self._handle_task_event)
        self._sync_state(self._app_context.state)

    def refresh_options(self) -> None:
        self._refresh_audio_library_options()
        self._refresh_voice_options()
        self._refresh_avatar_options()
        self._refresh_reference_options()
        self._refresh_bgm_options()
        self._restore_workspace_audio_variants()
        self._sync_audio_variant_list(self._app_context.state)

    def _restore_workspace_audio_variants(self) -> None:
        state = self._app_context.state
        workspace_raw = (self._workspace_input.text().strip() if hasattr(self, "_workspace_input") else "") or state.workspace
        workspace_path = Path(workspace_raw).expanduser() if workspace_raw else None
        if workspace_path is None:
            return

        try:
            workspace_dir = workspace_path.resolve()
        except OSError:
            return
        if not workspace_dir.exists() or not workspace_dir.is_dir():
            return

        tts_dir = workspace_dir / "tts"
        workspace_audio_paths: list[Path] = []
        if tts_dir.exists() and tts_dir.is_dir():
            for path in tts_dir.iterdir():
                if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                try:
                    workspace_audio_paths.append(path.resolve())
                except OSError:
                    continue
            workspace_audio_paths.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)

        valid_selected_path = self._resolve_existing_audio_path(state.selected_audio_variant_path) or self._resolve_existing_audio_path(
            state.audio_path
        )
        workspace_audio_set = {str(path) for path in workspace_audio_paths}

        restored_variants = []
        for variant in state.audio_variants:
            normalized_path = self._resolve_existing_audio_path(variant.path)
            if not normalized_path:
                continue
            if variant.source == "generated" and normalized_path not in workspace_audio_set and normalized_path != valid_selected_path:
                continue
            variant.path = normalized_path
            restored_variants.append(variant)
        state.audio_variants = restored_variants

        for path in reversed(workspace_audio_paths):
            state.upsert_audio_variant(
                path=str(path),
                label=path.stem,
                source="generated",
                make_selected=False,
            )

        if valid_selected_path:
            if not any(item.path == valid_selected_path for item in state.audio_variants):
                state.upsert_audio_variant(
                    path=valid_selected_path,
                    label=Path(valid_selected_path).stem,
                    source="library" if state.preferred_audio == valid_selected_path else "generated",
                    make_selected=False,
                )
            state.select_audio_variant(valid_selected_path, preferred_audio=state.preferred_audio == valid_selected_path)
        elif workspace_audio_paths:
            state.select_audio_variant(str(workspace_audio_paths[0]), preferred_audio=False)
        else:
            if state.selected_audio_variant_path and not self._resolve_existing_audio_path(state.selected_audio_variant_path):
                state.selected_audio_variant_path = ""
            if state.audio_path and not self._resolve_existing_audio_path(state.audio_path):
                state.audio_path = ""

    def _resolve_existing_audio_path(self, raw_path: str) -> str:
        normalized = raw_path.strip()
        if not normalized:
            return ""
        try:
            resolved = Path(normalized).expanduser().resolve()
        except OSError:
            return ""
        return str(resolved) if resolved.exists() and resolved.is_file() else ""

    def _refresh_primary_action_buttons(self, state: PipelineState | None = None) -> None:
        state = state or self._app_context.state
        busy = state.is_running or self._ultimate_clone_prepare_in_progress
        self._generate_audio_button.setEnabled(not busy)
        self._all_in_one_button.setEnabled(not busy)

    def _register_loading_button(self, task_kind: str, button: QPushButton, label: str) -> None:
        self._loading_button_specs[task_kind] = (button, label)
        button.setText(label)

    def _prime_loading_button(self, task_kind: str) -> None:
        if task_kind not in self._loading_button_specs:
            return
        self._primed_loading_task_kind = task_kind
        self._refresh_loading_buttons()

    def _current_loading_task_kind(self, state: PipelineState | None = None) -> str:
        state = state or self._app_context.state
        if state.is_running:
            active_task_kind = state.active_task_kind.strip()
            self._primed_loading_task_kind = ""
            if active_task_kind in self._loading_button_specs:
                return active_task_kind
            return ""
        primed_task_kind = self._primed_loading_task_kind.strip()
        if primed_task_kind in self._loading_button_specs:
            return primed_task_kind
        return ""

    def _refresh_loading_buttons(self, state: PipelineState | None = None) -> None:
        active_task_kind = self._current_loading_task_kind(state)
        for task_kind, (button, label) in self._loading_button_specs.items():
            if task_kind == active_task_kind:
                frame = self._loading_frames[self._loading_frame_index % len(self._loading_frames)]
                button.setText(f"{frame} {label}")
            else:
                button.setText(label)
        if active_task_kind:
            if not self._loading_timer.isActive():
                self._loading_timer.start()
            return
        self._loading_timer.stop()
        self._loading_frame_index = 0

    def _finish_ultimate_clone_prepare(self, token: int) -> bool:
        if token != self._ultimate_clone_prepare_token:
            return False
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_reference = ""
        self._ultimate_clone_prepare_task_kind = ""
        self._ultimate_clone_prepare_token += 1
        self._refresh_primary_action_buttons()
        if not self._app_context.state.is_running:
            self._primed_loading_task_kind = ""
            self._refresh_loading_buttons()
        return True

    def _begin_ultimate_clone_prepare(
        self,
        reference_audio_path: str,
        *,
        task_kind: str,
        retry_callback,
        failure_callback,
    ) -> None:
        reference_path = str(Path(reference_audio_path).expanduser().resolve())
        self._ultimate_clone_prepare_token += 1
        token = self._ultimate_clone_prepare_token
        self._ultimate_clone_prepare_in_progress = True
        self._ultimate_clone_prepare_reference = reference_path
        self._ultimate_clone_prepare_task_kind = task_kind
        self._refresh_primary_action_buttons()
        self._prime_loading_button(task_kind)
        prepare_ultimate_clone_prompt_text_async(
            self,
            self._app_context,
            reference_path,
            on_ready=lambda _prompt_text: self._handle_ultimate_clone_prepare_ready(token, retry_callback),
            on_failed=lambda message: self._handle_ultimate_clone_prepare_failed(token, message, failure_callback),
        )

    def _handle_ultimate_clone_prepare_ready(self, token: int, retry_callback) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        retry_callback()

    def _handle_ultimate_clone_prepare_failed(self, token: int, message: str, failure_callback) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        failure_callback(message)

    def _advance_loading_buttons(self) -> None:
        if not self._current_loading_task_kind():
            self._refresh_loading_buttons()
            return
        self._loading_frame_index = (self._loading_frame_index + 1) % len(self._loading_frames)
        self._refresh_loading_buttons()

    def _configure_numeric_inputs(self) -> None:
        for spinbox in self.findChildren(QAbstractSpinBox):
            spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root_layout.addWidget(scroll)

        canvas = QWidget()
        scroll.setWidget(canvas)
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(32, 32, 32, 48)
        canvas_layout.setSpacing(0)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)

        shell = QWidget()
        shell.setMaximumWidth(16777215)
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(32)
        shell_layout.addWidget(self._build_copy_section())
        shell_layout.addWidget(self._build_audio_section())
        shell_layout.addWidget(self._build_avatar_section())
        shell_layout.addWidget(self._build_publish_section())
        shell_layout.addWidget(self._build_global_action_bar())
        shell_layout.addStretch(1)

        center_row.addWidget(shell, 1)
        canvas_layout.addLayout(center_row)
        canvas_layout.addStretch(1)

    def _create_header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("heroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        eyebrow = QLabel("文案 / 音频 / 数字人 / 发布")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("视频工作台")
        title.setObjectName("pageTitle")
        desc = QLabel("按四个业务阶段组织工作流。文案统一编辑，多版本音频可筛选，数字人始终依赖当前选中的音频。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        for label_text in PHASE_LABELS:
            chip = QLabel(label_text)
            chip.setObjectName("flowPill")
            chip_row.addWidget(chip)
        chip_row.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self._open_workspace_button = QPushButton("打开工作区")
        self._open_workspace_button.setObjectName("subtleButton")
        self._open_workspace_button.clicked.connect(lambda: self._open_path(self._workspace_input.text().strip()))
        self._save_draft_button = QPushButton("保存草稿")
        self._save_draft_button.setObjectName("subtleButton")
        self._save_draft_button.clicked.connect(self._save_copy_draft)
        self._all_in_one_button = QPushButton("一键全流程")
        self._all_in_one_button.setObjectName("primaryButton")
        self._all_in_one_button.clicked.connect(self._run_all_in_one)
        self._cancel_button = QPushButton("取消任务")
        self._cancel_button.setObjectName("subtleButton")
        self._cancel_button.clicked.connect(self._app_context.task_controller.cancel_active_task)
        action_row.addWidget(self._open_workspace_button)
        action_row.addWidget(self._save_draft_button)
        action_row.addStretch(1)
        action_row.addWidget(self._cancel_button)
        action_row.addWidget(self._all_in_one_button)

        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(chip_row)
        layout.addLayout(action_row)
        return card

    def _section_card(self, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        raw_number, _, raw_label = title.partition(".")
        number = raw_number.strip() or "01"
        label = raw_label.strip() or title.strip()
        if not label.endswith("模块"):
            label = f"{label}模块"

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        badge = QLabel(number.zfill(2))
        badge.setObjectName("moduleBadge")
        title_label = QLabel(label)
        title_label.setObjectName("moduleTitle")
        header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        header_row.addWidget(title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        action_host = QWidget(card)
        action_host.setObjectName("sectionHeaderActionHost")
        action_layout = QHBoxLayout(action_host)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        header_row.addWidget(action_host, 0, Qt.AlignmentFlag.AlignVCenter)

        divider = QFrame()
        divider.setObjectName("moduleDivider")

        layout.addLayout(header_row)
        layout.addWidget(divider)
        return card

    def _attach_section_action(self, card: QFrame, widget: QWidget) -> None:
        host = card.findChild(QWidget, "sectionHeaderActionHost")
        if host is None or host.layout() is None:
            return
        host.layout().addWidget(widget)

    def _build_global_action_bar(self) -> QWidget:
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)

        self._cancel_button = QPushButton("取消任务", shell)
        self._cancel_button.setObjectName("subtleButton")
        self._cancel_button.clicked.connect(self._app_context.task_controller.cancel_active_task)
        self._cancel_button.hide()

        self._all_in_one_button = QPushButton("一键完成流程", shell)
        self._all_in_one_button.setObjectName("bottomCtaButton")
        self._all_in_one_button.clicked.connect(self._run_all_in_one)

        self._all_in_one_status = QPlainTextEdit(shell)
        self._all_in_one_status.setObjectName("compactLog")
        self._all_in_one_status.setReadOnly(True)
        self._all_in_one_status.hide()

        row.addWidget(self._cancel_button)
        row.addWidget(self._all_in_one_button)
        layout.addLayout(row)
        layout.addWidget(self._all_in_one_status)
        return shell

    def _build_copy_section(self) -> QWidget:
        card = self._section_card("1. 文案", "支持手动输入、视频链接提取和文案改写。当前编辑区文本会直接作为后续音频生成输入。")
        layout = card.layout()

        self._workspace_input = QLineEdit(self._app_context.state.workspace)
        self._workspace_input.hide()
        self._copy_version_combo = QComboBox(card)
        self._copy_version_combo.hide()

        self._open_workspace_button = QPushButton("打开工作区", card)
        self._open_workspace_button.setObjectName("subtleButton")
        self._open_workspace_button.clicked.connect(lambda: self._open_path(self._workspace_input.text().strip()))
        self._open_workspace_button.hide()

        self._save_draft_button = QPushButton("保存草稿", card)
        self._save_draft_button.setObjectName("subtleButton")
        self._save_draft_button.clicked.connect(self._save_copy_draft)
        self._save_draft_button.hide()

        content = QWidget()
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setHorizontalSpacing(24)
        content_layout.setVerticalSpacing(12)
        content_layout.setColumnStretch(0, 3)
        content_layout.setColumnStretch(1, 7)
        content_layout.setColumnStretch(2, 2)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_label = QLabel("来源链接")
        left_label.setObjectName("fieldLabel")
        self._video_link_input = QLineEdit(self._app_context.state.source_input)
        self._video_link_input.setPlaceholderText("粘贴网页或视频链接")
        self._source_type_hint = QLabel("来源类型：未检测", card)
        self._source_type_hint.setObjectName("mutedText")
        self._source_type_hint.hide()

        extract_row = QVBoxLayout()
        extract_row.setContentsMargins(0, 0, 0, 0)
        extract_row.setSpacing(0)
        self._extract_button = QPushButton("提取文案")
        self._extract_button.setObjectName("subtleButton")
        self._extract_button.setMinimumHeight(36)
        self._extract_button.clicked.connect(self._submit_content_task)
        extract_row.addWidget(self._extract_button)
        extract_row.addStretch(1)

        left_layout.addWidget(left_label)
        left_layout.addWidget(self._video_link_input)
        left_layout.addLayout(extract_row)

        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        self._copy_editor = QPlainTextEdit()
        self._copy_editor.setPlaceholderText("请输入或粘贴您的口播文案...")
        self._copy_editor.setMinimumHeight(128)
        self._copy_editor.setMaximumHeight(156)
        self._copy_editor.textChanged.connect(self._update_copy_counter)
        counter_row = QHBoxLayout()
        counter_row.setContentsMargins(0, 0, 0, 0)
        counter_row.addStretch(1)
        self._copy_counter_label = QLabel("字数 0 / 2000")
        self._copy_counter_label.setObjectName("copyCounter")
        counter_row.addWidget(self._copy_counter_label)

        editor_layout.addWidget(self._copy_editor)
        editor_layout.addLayout(counter_row)

        self._rewrite_mode = QComboBox(card)
        self._rewrite_mode.addItem("自动改写", RewriteMode.imitate)
        self._rewrite_mode.addItem("按提示改写", RewriteMode.custom)
        self._rewrite_mode.addItem("纠错润色", RewriteMode.correct)
        self._rewrite_mode.hide()
        self._rewrite_prompt = QLineEdit("保留事实信息，增强开头钩子、节奏和转化动作。", card)
        self._rewrite_prompt.setPlaceholderText("按提示改写时填写要求")
        self._rewrite_prompt.hide()
        self._rewrite_status = QLabel("改写结果会直接回写到主文案。", card)
        self._rewrite_status.setObjectName("mutedText")
        self._rewrite_status.setWordWrap(True)
        self._rewrite_status.hide()
        rewrite_button = QPushButton("智能改写")
        rewrite_button.setObjectName("accentButton")
        rewrite_button.setMinimumHeight(132)
        rewrite_button.clicked.connect(self._submit_rewrite_task)

        content_layout.addWidget(left_panel, 0, 0)
        content_layout.addWidget(editor_panel, 0, 1)
        content_layout.addWidget(rewrite_button, 0, 2)
        layout.addWidget(content)
        self._update_copy_counter()
        return card

    def _build_audio_section(self) -> QWidget:
        card = self._section_card("2. 音频", "每次生成都会新增一个音频版本。你可以从音频库导入，也可以在版本列表中反复试听并切换当前音频。")
        layout = card.layout()

        toolbar_row = QHBoxLayout()
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(12)
        toolbar_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        source_panel = QFrame()
        source_panel.setObjectName("toolbarChip")
        source_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_panel.setMinimumHeight(44)
        source_layout = QHBoxLayout(source_panel)
        source_layout.setContentsMargins(14, 8, 14, 8)
        source_layout.setSpacing(10)
        source_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        source_label = QLabel("参考音频")
        source_label.setObjectName("fieldLabel")
        self._managed_audio_combo = QComboBox()
        self._managed_audio_combo.setEditable(False)
        self._managed_audio_combo.setMinimumHeight(36)
        self._managed_audio_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._managed_audio_combo.currentIndexChanged.connect(self._update_clone_audio_summary)
        self._clone_audio_summary = QLabel("当前未选择克隆声音", card)
        self._clone_audio_summary.setObjectName("mutedText")
        self._clone_audio_summary.setWordWrap(True)
        self._clone_audio_summary.hide()

        self._library_preview_button = QPushButton("▶")
        self._library_preview_button.setObjectName("miniIconButton")
        self._library_preview_button.setFixedSize(28, 28)
        self._library_preview_button.clicked.connect(self._preview_selected_library_audio)

        self._library_preview_slider = QSlider(Qt.Orientation.Horizontal)
        self._library_preview_slider.setRange(0, 100)
        self._library_preview_slider.setValue(50)
        self._library_preview_slider.setEnabled(False)
        self._library_preview_slider.setFixedWidth(96)

        self._use_managed_audio_button = QPushButton("设为当前", card)
        self._use_managed_audio_button.setObjectName("subtleButton")
        self._use_managed_audio_button.clicked.connect(self._use_selected_audio)
        self._use_managed_audio_button.hide()

        self._refresh_audio_library_button = QPushButton("刷新", card)
        self._refresh_audio_library_button.setObjectName("subtleButton")
        self._refresh_audio_library_button.clicked.connect(self._refresh_audio_library_options)
        self._refresh_audio_library_button.hide()

        source_layout.addWidget(source_label, 0, Qt.AlignmentFlag.AlignVCenter)
        source_layout.addWidget(self._managed_audio_combo, 1, Qt.AlignmentFlag.AlignVCenter)
        source_layout.addWidget(self._library_preview_button, 0, Qt.AlignmentFlag.AlignVCenter)
        source_layout.addWidget(self._library_preview_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        toolbar_row.addWidget(source_panel, 1, Qt.AlignmentFlag.AlignVCenter)

        self._ultimate_clone_checkbox = QCheckBox("极致克隆 / 精准匹配")
        self._ultimate_clone_checkbox.setChecked(False)
        self._ultimate_clone_checkbox.setToolTip("开启后自动识别参考音频文字，提高音色和语气相似度；第一次会慢一点。")
        self._ultimate_clone_checkbox.setMinimumHeight(36)
        self._ultimate_clone_checkbox.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        toolbar_row.addWidget(self._ultimate_clone_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self._generate_audio_button = QPushButton("生成新音频")
        self._generate_audio_button.setObjectName("primaryButton")
        self._generate_audio_button.setMinimumHeight(36)
        self._generate_audio_button.setMinimumWidth(124)
        self._generate_audio_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._generate_audio_button.clicked.connect(self._submit_tts_task)
        toolbar_row.addWidget(self._generate_audio_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(toolbar_row)

        self._audio_status_badge = QLabel(card)
        self._audio_status_badge.setObjectName("statusPill")
        self._audio_status_badge.hide()
        self._audio_status_hint = QLabel(card)
        self._audio_status_hint.setObjectName("mutedText")
        self._audio_status_hint.setWordWrap(True)
        self._audio_status_hint.hide()

        variant_header = QHBoxLayout()
        variant_title = QLabel("生成的音频列表")
        variant_title.setObjectName("fieldLabel")
        variant_header.addWidget(variant_title)
        variant_header.addStretch(1)
        self._audio_variant_summary = QLabel("当前未选择音频", card)
        self._audio_variant_summary.setObjectName("mutedText")
        self._audio_variant_summary.hide()
        layout.addLayout(variant_header)

        self._audio_variant_list = QListWidget()
        self._audio_variant_list.setObjectName("audioVariantList")
        self._audio_variant_list.setMinimumHeight(150)
        self._audio_variant_list.setSpacing(10)
        self._audio_variant_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._audio_variant_list.itemSelectionChanged.connect(self._on_audio_variant_selection_changed)
        layout.addWidget(self._audio_variant_list)

        self._play_audio_button = QPushButton("播放")
        self._play_audio_button.setObjectName("playButton")
        self._play_audio_button.clicked.connect(self._toggle_audio_playback)
        self._audio_progress = QSlider(Qt.Orientation.Horizontal)
        self._audio_progress.setRange(0, 0)
        self._audio_progress.setEnabled(False)
        self._audio_progress.sliderPressed.connect(self._on_audio_slider_pressed)
        self._audio_progress.sliderReleased.connect(self._on_audio_slider_released)
        self._audio_progress.sliderMoved.connect(self._seek_audio)
        self._audio_timeline = QLabel("00:00 / 00:00")
        self._audio_timeline.setObjectName("timeLabel")

        self._audio_volume_label = QLabel("音量")
        self._audio_volume_label.setObjectName("fieldLabel")
        self._audio_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._audio_volume_slider.setRange(0, 100)
        self._audio_volume_slider.setValue(85)
        self._audio_volume_slider.setFixedWidth(120)
        self._audio_volume_slider.valueChanged.connect(
            lambda value: hasattr(self, "_audio_output_device")
            and self._audio_output_device.setVolume(value / 100.0)
        )

        self._audio_output = QLabel(card)
        self._audio_output.setObjectName("mutedText")
        self._audio_output.setWordWrap(True)
        self._audio_output.hide()
        self._play_audio_button.setText("▶")
        self._play_audio_button.setToolTip("播放当前选中的音频版本")
        self._play_audio_button.setFixedSize(34, 34)
        return card

    def _build_avatar_section(self) -> QWidget:
        card = self._section_card("3. 数字人", "选择参考视频、字幕和 BGM，并在这里完成数字人渲染与后处理。")
        layout = card.layout()

        self._generate_avatar_button = QPushButton("生成视频")
        self._generate_avatar_button.setObjectName("primaryButton")
        self._generate_avatar_button.clicked.connect(self._submit_avatar_task)
        self._attach_section_action(card, self._generate_avatar_button)

        main_row = QHBoxLayout()
        main_row.setSpacing(24)
        main_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        preview_panel = QFrame()
        preview_panel.setObjectName("subCard")
        preview_panel.setMinimumWidth(360)
        preview_panel.setMaximumWidth(460)
        preview_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(10)
        preview_label = QLabel("视频渲染预览")
        preview_label.setObjectName("fieldLabel")
        self._avatar_preview_shell = AspectRatioContainer(9, 16, preview_panel)
        self._avatar_preview_shell.setMinimumHeight(720)
        self._avatar_preview_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._avatar_preview_frame = QFrame()
        self._avatar_preview_frame.setObjectName("previewStage")
        self._avatar_preview_shell.set_content(self._avatar_preview_frame)
        preview_stack = QStackedLayout(self._avatar_preview_frame)
        preview_stack.setContentsMargins(0, 0, 0, 0)
        self._avatar_preview_stack = preview_stack

        self._avatar_video_widget = ClickableVideoWidget(self._avatar_preview_frame)
        self._avatar_video_widget.setObjectName("avatarVideoWidget")
        self._avatar_video_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._avatar_video_widget.clicked.connect(self._toggle_avatar_video_playback)
        preview_stack.addWidget(self._avatar_video_widget)

        self._avatar_preview_placeholder = ClickableWidget(self._avatar_preview_frame)
        placeholder_layout = QVBoxLayout(self._avatar_preview_placeholder)
        placeholder_layout.setContentsMargins(28, 28, 28, 28)
        placeholder_layout.setSpacing(10)
        placeholder_layout.addStretch(1)
        preview_ring = QFrame()
        preview_ring.setObjectName("previewRing")
        preview_ring.setFixedSize(82, 82)
        preview_ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        placeholder_layout.addWidget(preview_ring, 0, Qt.AlignmentFlag.AlignHCenter)
        preview_line = QFrame()
        preview_line.setObjectName("previewAccentLine")
        preview_line.setFixedSize(120, 2)
        preview_line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        placeholder_layout.addWidget(preview_line, 0, Qt.AlignmentFlag.AlignHCenter)
        self._avatar_preview_badge = QLabel("待机")
        self._avatar_preview_badge.setObjectName("previewBadge")
        self._avatar_preview_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_preview_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._avatar_preview_title = QLabel("等待渲染视频")
        self._avatar_preview_title.setObjectName("previewTitle")
        self._avatar_preview_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_preview_title.setWordWrap(True)
        self._avatar_preview_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._avatar_preview_note = QLabel("选择参考视频后即可预览，生成结果也会在这里回放。")
        self._avatar_preview_note.setObjectName("previewNote")
        self._avatar_preview_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_preview_note.setWordWrap(True)
        self._avatar_preview_note.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        placeholder_layout.addWidget(self._avatar_preview_badge)
        placeholder_layout.addWidget(self._avatar_preview_title)
        placeholder_layout.addWidget(self._avatar_preview_note)
        placeholder_layout.addStretch(1)
        self._avatar_preview_placeholder.clicked.connect(self._toggle_avatar_video_playback)
        preview_stack.addWidget(self._avatar_preview_placeholder)
        preview_stack.setCurrentWidget(self._avatar_preview_placeholder)
        self._avatar_preview_name = QLabel("数字人素材：暂无")
        self._avatar_preview_name.setObjectName("mutedText")
        self._avatar_preview_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_preview_name.setWordWrap(True)
        self._reference_video_combo = QComboBox()
        self._reference_video_combo.setEditable(False)
        self._reference_video_combo.currentIndexChanged.connect(
            lambda _index: self._sync_avatar_preview(self._app_context.state)
        )
        self._refresh_reference_button = QPushButton("刷新", card)
        self._refresh_reference_button.setObjectName("subtleButton")
        self._refresh_reference_button.clicked.connect(self._refresh_reference_options)
        self._refresh_reference_button.hide()
        preview_layout.addWidget(preview_label)
        preview_layout.addWidget(self._avatar_preview_shell, 1)
        preview_layout.addWidget(self._avatar_preview_name)
        main_row.addWidget(preview_panel)

        right_shell = QWidget(card)
        right_column = QVBoxLayout(right_shell)
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(16)
        right_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._selected_audio_summary = QLabel("当前未选中音频版本", card)
        self._selected_audio_summary.hide()

        source_panel = QFrame()
        source_panel.setObjectName("toolbarChip")
        source_layout = QHBoxLayout(source_panel)
        source_layout.setContentsMargins(14, 8, 14, 8)
        source_layout.setSpacing(10)
        source_label = QLabel("参考视频")
        source_label.setObjectName("fieldLabel")
        source_layout.addWidget(source_label)
        source_layout.addWidget(self._reference_video_combo, 1)
        right_column.addWidget(source_panel)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        self._include_subtitle_checkbox = ToggleSwitch()
        self._include_subtitle_checkbox.setChecked(True)
        self._enable_bgm_checkbox = ToggleSwitch()
        toggle_row.addWidget(
            self._build_toggle_card(
                "字幕生成",
                "自动生成字幕，可在下方调整样式后再烧录到视频中。",
                self._include_subtitle_checkbox,
            ),
            1,
        )
        toggle_row.addWidget(
            self._build_toggle_card(
                "背景音乐 (BGM)",
                "添加环境垫乐",
                self._enable_bgm_checkbox,
            ),
            1,
        )
        right_column.addLayout(toggle_row)

        self._batch_size = QSpinBox()
        self._batch_size.setRange(1, 16)
        self._batch_size.setValue(4)
        self._av_offset = QDoubleSpinBox()
        self._av_offset.setRange(-2.0, 2.0)
        self._av_offset.setSingleStep(0.1)
        self._mask_height = QDoubleSpinBox()
        self._mask_height.setRange(0.1, 1.0)
        self._mask_height.setSingleStep(0.05)
        self._mask_height.setValue(0.8)
        self._mask_width = QDoubleSpinBox()
        self._mask_width.setRange(0.1, 1.0)
        self._mask_width.setSingleStep(0.05)
        self._mask_width.setValue(0.8)
        self._beautify_checkbox = QCheckBox("美化牙齿 (Beautify Teeth)")
        self._beautify_checkbox.setChecked(True)

        self._burn_subtitle_checkbox = QCheckBox(card)
        self._burn_subtitle_checkbox.setChecked(True)
        self._burn_subtitle_checkbox.hide()
        self._include_subtitle_checkbox.toggled.connect(self._burn_subtitle_checkbox.setChecked)

        advanced_shell = QFrame()
        advanced_shell.setObjectName("moreParamsShell")
        advanced_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._avatar_advanced_shell = advanced_shell
        advanced_shell_layout = QVBoxLayout(advanced_shell)
        advanced_shell_layout.setContentsMargins(0, 0, 0, 0)
        advanced_shell_layout.setSpacing(0)
        advanced_head = QWidget()
        advanced_head.setObjectName("moreParamsHeader")
        advanced_head_layout = QHBoxLayout(advanced_head)
        advanced_head_layout.setContentsMargins(16, 12, 16, 12)
        advanced_head_layout.setSpacing(10)
        advanced_title = QLabel("更多参数设置")
        advanced_title.setObjectName("fieldLabel")
        advanced_head_layout.addWidget(advanced_title)
        advanced_head_layout.addStretch(1)
        self._avatar_advanced_toggle = QPushButton("收起")
        self._avatar_advanced_toggle.setObjectName("disclosureButton")
        self._avatar_advanced_toggle.setCheckable(True)
        self._avatar_advanced_toggle.setChecked(True)
        self._avatar_advanced_toggle.toggled.connect(self._toggle_avatar_advanced_settings)
        advanced_head_layout.addWidget(self._avatar_advanced_toggle)
        advanced_shell_layout.addWidget(advanced_head)

        self._avatar_advanced_panel = QFrame()
        self._avatar_advanced_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        advanced_shell_layout.addWidget(self._avatar_advanced_panel)
        advanced_panel_layout = QVBoxLayout(self._avatar_advanced_panel)
        advanced_panel_layout.setContentsMargins(16, 16, 16, 16)
        advanced_panel_layout.setSpacing(20)

        render_group = QFrame()
        render_group.setObjectName("groupBlock")
        render_layout = QVBoxLayout(render_group)
        render_layout.setContentsMargins(0, 0, 0, 0)
        render_layout.setSpacing(14)
        render_title = QLabel("渲染参数")
        render_title.setObjectName("groupTitle")
        render_layout.addWidget(render_title)
        render_form = QGridLayout()
        render_form.setHorizontalSpacing(12)
        render_form.setVerticalSpacing(10)
        render_form.addWidget(QLabel("批次"), 0, 0)
        render_form.addWidget(self._batch_size, 0, 1)
        render_form.addWidget(QLabel("音画偏移"), 0, 2)
        render_form.addWidget(self._av_offset, 0, 3)
        render_form.addWidget(QLabel("嘴型高度"), 1, 0)
        render_form.addWidget(self._mask_height, 1, 1)
        render_form.addWidget(QLabel("嘴型宽度"), 1, 2)
        render_form.addWidget(self._mask_width, 1, 3)
        render_layout.addLayout(render_form)
        render_layout.addWidget(self._beautify_checkbox)
        advanced_panel_layout.addWidget(render_group)

        subtitle_group = QFrame()
        subtitle_group.setObjectName("groupBlock")
        subtitle_layout = QVBoxLayout(subtitle_group)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        subtitle_layout.setSpacing(14)
        subtitle_title = QLabel("字幕样式")
        subtitle_title.setObjectName("groupTitle")
        subtitle_layout.addWidget(subtitle_title)
        self._generate_subtitle_button = QPushButton("生成字幕", card)
        self._generate_subtitle_button.setObjectName("subtleButton")
        self._generate_subtitle_button.clicked.connect(self._submit_subtitle_task)
        self._generate_subtitle_button.hide()
        self._save_subtitle_button = QPushButton("保存", card)
        self._save_subtitle_button.setObjectName("subtleButton")
        self._save_subtitle_button.clicked.connect(self._save_subtitle_text)
        self._save_subtitle_button.hide()
        self._apply_subtitle_button = QPushButton("应用样式", card)
        self._apply_subtitle_button.setObjectName("subtleButton")
        self._apply_subtitle_button.clicked.connect(self._apply_subtitle_style)
        self._apply_subtitle_button.hide()
        self._subtitle_font = QComboBox()
        self._subtitle_font.addItems(FONT_OPTIONS)
        self._subtitle_font_size = QSpinBox()
        self._subtitle_font_size.setRange(18, 88)
        self._subtitle_font_size.setValue(32)
        self._subtitle_margin = QSpinBox()
        self._subtitle_margin.setRange(0, 200)
        self._subtitle_margin.setValue(48)
        self._subtitle_margin.setToolTip("数值越大，字幕离底部越远，会更靠上显示。")
        self._subtitle_color = ColorInput("#FFFFFF")
        self._subtitle_outline = ColorInput("#000000")
        subtitle_form = QGridLayout()
        subtitle_form.setHorizontalSpacing(12)
        subtitle_form.setVerticalSpacing(10)
        subtitle_form.addWidget(QLabel("字体"), 0, 0)
        subtitle_form.addWidget(self._subtitle_font, 0, 1)
        subtitle_form.addWidget(QLabel("字号 (px)"), 0, 2)
        subtitle_form.addWidget(self._subtitle_font_size, 0, 3)
        subtitle_form.addWidget(QLabel("底部边距"), 0, 4)
        subtitle_form.addWidget(self._subtitle_margin, 0, 5)
        subtitle_form.addWidget(self._build_color_field("文字颜色", self._subtitle_color), 1, 0, 1, 3)
        subtitle_form.addWidget(self._build_color_field("描边颜色", self._subtitle_outline), 1, 3, 1, 3)
        subtitle_layout.addLayout(subtitle_form)
        advanced_panel_layout.addWidget(subtitle_group)

        bgm_group = QFrame()
        bgm_group.setObjectName("groupBlock")
        bgm_layout = QVBoxLayout(bgm_group)
        bgm_layout.setContentsMargins(0, 0, 0, 0)
        bgm_layout.setSpacing(14)
        bgm_title = QLabel("背景 BGM")
        bgm_title.setObjectName("groupTitle")
        bgm_layout.addWidget(bgm_title)
        self._random_bgm_button = QPushButton("随机", card)
        self._random_bgm_button.setObjectName("subtleButton")
        self._random_bgm_button.clicked.connect(self._pick_random_bgm)
        self._random_bgm_button.hide()
        self._refresh_bgm_button = QPushButton("刷新", card)
        self._refresh_bgm_button.setObjectName("subtleButton")
        self._refresh_bgm_button.clicked.connect(self._refresh_bgm_options)
        self._refresh_bgm_button.hide()
        self._apply_bgm_button = QPushButton("应用 BGM", card)
        self._apply_bgm_button.setObjectName("subtleButton")
        self._apply_bgm_button.clicked.connect(self._apply_bgm)
        self._apply_bgm_button.hide()
        self._bgm_combo = QComboBox()
        self._bgm_combo.setEditable(False)
        bgm_layout.addWidget(self._bgm_combo)
        volume_row = QHBoxLayout()
        volume_row.setSpacing(10)
        self._bgm_volume = QSlider(Qt.Orientation.Horizontal)
        self._bgm_volume.setRange(0, 100)
        self._bgm_volume.setValue(15)
        self._bgm_volume_value = QLabel("15%")
        self._bgm_volume_value.setObjectName("mutedText")
        self._bgm_volume.valueChanged.connect(lambda value: self._bgm_volume_value.setText(f"{value}%"))
        volume_row.addWidget(QLabel("背景音量"))
        volume_row.addWidget(self._bgm_volume, 1)
        volume_row.addWidget(self._bgm_volume_value)
        bgm_layout.addLayout(volume_row)
        advanced_panel_layout.addWidget(bgm_group)

        right_column.addWidget(advanced_shell)
        right_column.addStretch(1)

        self._subtitle_output = QPlainTextEdit(card)
        self._subtitle_output.setPlaceholderText("这里会显示字幕文本，也可以手动修改后保存。")
        self._subtitle_output.hide()
        self._bgm_status = QPlainTextEdit(card)
        self._bgm_status.setObjectName("compactLog")
        self._bgm_status.setReadOnly(True)
        self._bgm_status.hide()
        self._avatar_output = QPlainTextEdit(card)
        self._avatar_output.setObjectName("compactLog")
        self._avatar_output.setReadOnly(True)
        self._avatar_output.hide()

        main_row.addWidget(right_shell, 1)
        layout.addLayout(main_row)
        self._toggle_avatar_advanced_settings(True)
        return card

    def _toggle_avatar_advanced_settings(self, checked: bool) -> None:
        self._avatar_advanced_panel.setVisible(checked)
        self._avatar_advanced_toggle.setText("收起" if checked else "展开")
        self._avatar_advanced_panel.setMaximumHeight(16777215 if checked else 0)
        if hasattr(self, "_avatar_advanced_shell"):
            self._avatar_advanced_shell.adjustSize()
            self._avatar_advanced_shell.updateGeometry()

    def _build_publish_section(self) -> QWidget:
        card = self._section_card("4. 发布", "生成封面、发布文案和平台素材。当前版本不支持自动登录发布。")
        layout = card.layout()

        main_row = QHBoxLayout()
        main_row.setSpacing(16)

        preview_panel = QFrame()
        preview_panel.setObjectName("subCard")
        preview_panel.setFixedWidth(206)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(8)
        preview_title = QLabel("封面预览")
        preview_title.setObjectName("fieldLabel")
        self._cover_preview_image = QLabel("封面展示文字")
        self._cover_preview_image.setObjectName("coverPreviewImage")
        self._cover_preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_preview_image.setFixedSize(182, 324)
        self._cover_preview_image.setWordWrap(False)
        self._cover_preview_meta = QLabel("尚未生成封面")
        self._cover_preview_meta.setObjectName("mutedText")
        self._cover_preview_meta.setWordWrap(True)
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self._cover_preview_image, 0, Qt.AlignmentFlag.AlignHCenter)
        preview_layout.addWidget(self._cover_preview_meta)
        preview_layout.addStretch(1)
        main_row.addWidget(preview_panel)

        right_shell = QWidget()
        right_shell_layout = QVBoxLayout(right_shell)
        right_shell_layout.setContentsMargins(0, 0, 0, 0)
        right_shell_layout.setSpacing(16)

        right_layout = QGridLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setHorizontalSpacing(16)
        right_layout.setVerticalSpacing(16)
        right_layout.setColumnStretch(0, 7)
        right_layout.setColumnStretch(1, 5)

        cover_card = QFrame()
        cover_card.setObjectName("subCard")
        cover_layout = QVBoxLayout(cover_card)
        cover_layout.setContentsMargins(16, 16, 16, 16)
        cover_layout.setSpacing(14)
        cover_head = QHBoxLayout()
        cover_head.setSpacing(10)
        cover_title = QLabel("封面设置")
        cover_title.setObjectName("groupTitle")
        cover_head.addWidget(cover_title)
        cover_head.addStretch(1)

        self._auto_cover_checkbox = QCheckBox(card)
        self._auto_cover_checkbox.setChecked(True)
        self._auto_cover_checkbox.hide()
        self._ai_cover_copy_checkbox = QCheckBox(card)
        self._ai_cover_copy_checkbox.setChecked(True)
        self._ai_cover_copy_checkbox.hide()
        self._publish_with_cover = QCheckBox(card)
        self._publish_with_cover.setChecked(True)
        self._publish_with_cover.hide()

        generate_cover = QPushButton("AI 智能生成")
        generate_cover.setObjectName("helperChipButton")
        generate_cover.clicked.connect(self._generate_cover)
        cover_head.addWidget(generate_cover)
        cover_layout.addLayout(cover_head)

        self._cover_text = QLineEdit()
        self._cover_text.setPlaceholderText("输入标题文字")
        self._cover_text.textChanged.connect(self._mark_cover_preview_dirty)
        self._cover_highlight = QLineEdit()
        self._cover_highlight.setPlaceholderText("高亮短句")
        self._cover_highlight.textChanged.connect(self._mark_cover_preview_dirty)
        self._cover_font = QComboBox()
        self._cover_font.addItems(FONT_OPTIONS)
        self._cover_font.hide()
        self._cover_font.currentIndexChanged.connect(lambda _index: self._mark_cover_preview_dirty())
        self._cover_font_size = QSpinBox()
        self._cover_font_size.setRange(24, 120)
        self._cover_font_size.setValue(68)
        self._cover_font_size.hide()
        self._cover_font_size.valueChanged.connect(lambda _value: self._mark_cover_preview_dirty())
        self._cover_font_color = ColorInput("#FFFFFF")
        self._cover_highlight_color = ColorInput("#005A6E")
        self._cover_position = QComboBox()
        self._cover_position.addItems(["top", "center", "bottom"])
        self._cover_position.currentIndexChanged.connect(lambda _index: self._sync_cover_position_buttons())
        self._cover_position.currentIndexChanged.connect(lambda _index: self._mark_cover_preview_dirty())
        self._cover_frame_time = QDoubleSpinBox()
        self._cover_frame_time.setRange(0.0, 600.0)
        self._cover_frame_time.setSingleStep(0.5)
        self._cover_frame_time.editingFinished.connect(self._mark_cover_preview_dirty)
        self._cover_font_color._line_edit.textChanged.connect(self._mark_cover_preview_dirty)
        self._cover_highlight_color._line_edit.textChanged.connect(self._mark_cover_preview_dirty)

        cover_form = QGridLayout()
        cover_form.setHorizontalSpacing(12)
        cover_form.setVerticalSpacing(12)
        cover_form.addWidget(QLabel("封面标题"), 0, 0)
        cover_form.addWidget(self._cover_text, 0, 1)
        cover_form.addWidget(QLabel("高亮文案"), 0, 2)
        cover_form.addWidget(self._cover_highlight, 0, 3)
        cover_form.addWidget(self._build_color_field("字体颜色", self._cover_font_color), 1, 0, 1, 2)
        cover_form.addWidget(self._build_color_field("高亮底色", self._cover_highlight_color), 1, 2, 1, 2)
        cover_form.addWidget(QLabel("截帧时间"), 2, 0)
        cover_form.addWidget(self._cover_frame_time, 2, 1)
        cover_form.addWidget(QLabel("排版位置"), 3, 0, 1, 4)
        cover_layout.addLayout(cover_form)

        position_row = QHBoxLayout()
        position_row.setSpacing(8)
        self._cover_position_buttons: dict[str, QPushButton] = {}
        for label_text, value in (("顶部", "top"), ("中间", "center"), ("底部", "bottom")):
            button = QPushButton(label_text)
            button.setObjectName("positionChoiceButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, current=value: self._set_cover_position(current))
            self._cover_position_buttons[value] = button
            position_row.addWidget(button, 1)
        cover_layout.addLayout(position_row)
        self._set_cover_position("top")

        self._cover_status = QPlainTextEdit(card)
        self._cover_status.setObjectName("compactLog")
        self._cover_status.setReadOnly(True)
        self._cover_status.hide()
        cover_layout.addWidget(self._cover_status)
        right_layout.addWidget(cover_card, 0, 0)

        side_shell = QWidget()
        side_layout = QVBoxLayout(side_shell)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(16)

        platform_card = QFrame()
        platform_card.setObjectName("subCard")
        platform_layout = QVBoxLayout(platform_card)
        platform_layout.setContentsMargins(16, 16, 16, 16)
        platform_layout.setSpacing(12)
        platform_label = QLabel("同步发布平台")
        platform_label.setObjectName("fieldLabel")
        platform_layout.addWidget(platform_label)
        self._publish_douyin_check = QCheckBox("抖音 (Douyin)")
        self._publish_wechat_check = QCheckBox("视频号 (Channels)")
        self._publish_xhs_check = QCheckBox(card)
        self._publish_xhs_check.hide()
        self._publish_douyin_check.setChecked(True)
        self._publish_wechat_check.setChecked(False)
        platform_layout.addWidget(self._publish_douyin_check)
        platform_layout.addWidget(self._publish_wechat_check)
        publish_hint = QLabel("当前版本暂未内置抖音 / 小红书 / 视频号自动发布，只能生成发布素材。")
        publish_hint.setObjectName("mutedText")
        publish_hint.setWordWrap(True)
        platform_layout.addWidget(publish_hint)

        self._publish_selected_button = QPushButton("发布选中平台", card)
        self._publish_selected_button.setObjectName("primaryButton")
        self._publish_selected_button.clicked.connect(self._publish_selected_platforms)
        self._publish_selected_button.hide()
        self._publish_status = QPlainTextEdit(card)
        self._publish_status.setObjectName("compactLog")
        self._publish_status.setReadOnly(True)
        self._publish_status.hide()
        platform_layout.addWidget(self._publish_selected_button)
        platform_layout.addWidget(self._publish_status)
        side_layout.addWidget(platform_card)

        description_card = QFrame()
        description_card.setObjectName("subCard")
        description_layout = QVBoxLayout(description_card)
        description_layout.setContentsMargins(16, 16, 16, 16)
        description_layout.setSpacing(12)
        description_label = QLabel("发布描述")
        description_label.setObjectName("fieldLabel")
        description_layout.addWidget(description_label)
        self._description_button = QPushButton("AI 生成文案", card)
        self._description_button.setObjectName("subtleButton")
        self._description_button.clicked.connect(self._generate_description)
        self._description_button.hide()
        self._description_output = QPlainTextEdit()
        self._description_output.setPlaceholderText("生成发布描述、#话题 和行动引导...")
        self._description_output.setMinimumHeight(104)
        description_layout.addWidget(self._description_output)
        side_layout.addWidget(description_card)
        right_layout.addWidget(side_shell, 0, 1)

        right_shell_layout.addLayout(right_layout)
        helper_button = QPushButton("AI 优化发布方案")
        helper_button.setObjectName("helperWideButton")
        helper_button.clicked.connect(self._optimize_publish_plan)
        right_shell_layout.addWidget(helper_button)

        main_row.addWidget(right_shell, 1)
        layout.addLayout(main_row)
        self._sync_cover_preview()
        return card

    def _build_toggle_card(self, title: str, desc: str, checkbox: QCheckBox) -> QWidget:
        card = QFrame()
        card.setObjectName("toggleCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("toggleTitle")
        desc_label = QLabel(desc)
        desc_label.setObjectName("mutedText")
        desc_label.setWordWrap(True)
        text_box.addWidget(title_label)
        text_box.addWidget(desc_label)

        layout.addLayout(text_box, 1)
        layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _build_color_field(self, title: str, color_input: ColorInput) -> QWidget:
        if hasattr(color_input, "_line_edit"):
            color_input._line_edit.hide()
        if hasattr(color_input, "_preview"):
            color_input._preview.setFixedSize(24, 24)

        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(color_input, 0, Qt.AlignmentFlag.AlignLeft)
        hex_label = QLabel(color_input.text().upper())
        hex_label.setObjectName("hexColorLabel")
        row.addWidget(hex_label)
        row.addStretch(1)
        if hasattr(color_input, "_line_edit"):
            color_input._line_edit.textChanged.connect(
                lambda text, output=hex_label: output.setText((text or "#FFFFFF").upper())
            )
        layout.addLayout(row)
        return field

    def _mark_cover_preview_dirty(self) -> None:
        if self._cover_output_path:
            self._cover_generated_for_publish = False
        self._sync_cover_preview()

    def _cover_preview_source_video_path(self) -> str:
        return self._current_video_path() or self._app_context.state.source_video_path or ""

    def _ensure_cover_preview_frame(self, video_path: str, timestamp_sec: float) -> str:
        video_file = Path(video_path)
        output_dir = ensure_module_dir(
            self._workspace_input.text().strip() or self._app_context.state.workspace,
            "cover",
        )
        frame_path = output_dir / f"{sanitize_filename(video_file.stem)}_preview-frame.png"
        cache_key = f"{video_file.resolve()}|{video_file.stat().st_mtime_ns}|{timestamp_sec:.3f}"
        if cache_key != self._cover_preview_frame_key or not frame_path.exists():
            self._ffmpeg.render_cover_image(
                video_path,
                frame_path,
                timestamp_sec=timestamp_sec,
                title="",
                highlight_text="",
            )
            self._cover_preview_frame_key = cache_key
            self._cover_preview_frame_path = str(frame_path)
        return str(frame_path)

    def _preview_text_color_for_background(self, color: QColor) -> QColor:
        luminance = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
        return QColor("#191C1D") if luminance >= 160 else QColor("#FFFFFF")

    def _compose_cover_preview(self, target_size: QSize) -> tuple[QPixmap, bool]:
        width = max(target_size.width(), 182)
        height = max(target_size.height(), 324)
        background = QPixmap(width, height)
        background.fill(QColor("#000000"))

        painter = QPainter(background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        has_background_frame = False

        source_video_path = self._cover_preview_source_video_path().strip()
        source_pixmap = QPixmap()
        if source_video_path and Path(source_video_path).exists():
            try:
                frame_path = self._ensure_cover_preview_frame(
                    source_video_path,
                    float(self._cover_frame_time.value()) if hasattr(self, "_cover_frame_time") else 0.0,
                )
                source_pixmap = QPixmap(frame_path)
            except Exception:
                source_pixmap = QPixmap()
        if source_pixmap.isNull() and self._cover_output_path:
            source_pixmap = QPixmap(self._cover_output_path)

        if not source_pixmap.isNull():
            scaled = source_pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            draw_x = (width - scaled.width()) // 2
            draw_y = (height - scaled.height()) // 2
            painter.drawPixmap(draw_x, draw_y, scaled)
            has_background_frame = True
        else:
            gradient = QLinearGradient(0, 0, 0, height)
            gradient.setColorAt(0.0, QColor("#9A6730"))
            gradient.setColorAt(0.45, QColor("#734723"))
            gradient.setColorAt(1.0, QColor("#3B2314"))
            painter.fillRect(0, 0, width, height, gradient)

        title = self._cover_text.text().strip() if hasattr(self, "_cover_text") else ""
        highlight = self._cover_highlight.text().strip() if hasattr(self, "_cover_highlight") else ""
        title_text = title or "封面展示文字"
        position = combo_value(self._cover_position) if hasattr(self, "_cover_position") else "center"

        title_color = QColor(self._cover_font_color.text() if hasattr(self, "_cover_font_color") else "#FFFFFF")
        if not title_color.isValid():
            title_color = QColor("#FFFFFF")
        highlight_color = QColor(
            self._cover_highlight_color.text() if hasattr(self, "_cover_highlight_color") else "#005A6E"
        )
        if not highlight_color.isValid():
            highlight_color = QColor("#005A6E")
        highlight_text_color = self._preview_text_color_for_background(highlight_color)

        content_width = width - 28
        title_font = QFont(combo_value(self._cover_font) if hasattr(self, "_cover_font") else "Microsoft YaHei")
        title_font.setBold(True)
        title_font.setPixelSize(max(16, min(24, int(int(self._cover_font_size.value()) * width / 720))))
        title_metrics = QFontMetrics(title_font)
        text_flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap)
        title_bounds = title_metrics.boundingRect(QRect(0, 0, content_width, 1000), text_flags, title_text)

        highlight_height = 0
        highlight_width = 0
        highlight_font = QFont(title_font)
        highlight_font.setPixelSize(max(12, int(title_font.pixelSize() * 0.72)))
        highlight_font.setBold(True)
        highlight_metrics = QFontMetrics(highlight_font)
        if highlight:
            highlight_width = min(content_width, highlight_metrics.horizontalAdvance(highlight) + 22)
            highlight_height = highlight_metrics.height() + 10
        gap = 10 if highlight else 0
        block_height = title_bounds.height() + gap + highlight_height
        top_y = max(22, int(height * 0.068))
        center_y = max(18, (height - block_height) // 2)
        bottom_y = max(18, height - block_height - max(24, int(height * 0.075)))
        start_y = {"top": top_y, "center": center_y, "bottom": bottom_y}.get(position, top_y)

        title_rect = QRectF(14, start_y, content_width, title_bounds.height() + 4)
        painter.setFont(title_font)
        painter.setPen(QColor(0, 0, 0, 170))
        painter.drawText(title_rect.translated(1.2, 1.2), text_flags, title_text)
        painter.setPen(title_color)
        painter.drawText(title_rect, text_flags, title_text)

        if highlight:
            highlight_rect = QRectF(
                (width - highlight_width) / 2,
                title_rect.y() + title_bounds.height() + gap,
                highlight_width,
                highlight_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight_color)
            painter.drawRoundedRect(highlight_rect, 10, 10)
            painter.setFont(highlight_font)
            painter.setPen(highlight_text_color)
            painter.drawText(highlight_rect, int(Qt.AlignmentFlag.AlignCenter), highlight)

        painter.end()
        return background, has_background_frame

    def _set_cover_position(self, value: str) -> None:
        index = self._cover_position.findText(value)
        if index >= 0:
            self._cover_position.setCurrentIndex(index)
        self._sync_cover_position_buttons()

    def _sync_cover_position_buttons(self) -> None:
        current = combo_value(self._cover_position)
        for value, button in getattr(self, "_cover_position_buttons", {}).items():
            selected = value == current
            button.setChecked(selected)
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)

    def _optimize_publish_plan(self) -> None:
        self._generate_description()
        copy_text = self._copy_editor.toPlainText().strip()
        if copy_text and (not self._cover_text.text().strip() or not self._cover_highlight.text().strip()):
            title, highlight = self._generate_cover_copy(copy_text)
            if not self._cover_text.text().strip():
                self._cover_text.setText(title)
            if not self._cover_highlight.text().strip():
                self._cover_highlight.setText(highlight)
        self._sync_cover_preview()

    def _preview_selected_library_audio(self) -> None:
        audio_path = combo_value(self._managed_audio_combo)
        if not audio_path:
            self._set_audio_feedback("未选择", "idle", "请先从音频库选择一条参考音频。")
            return
        audio_file = Path(audio_path)
        if not audio_file.exists():
            self._set_audio_feedback("文件缺失", "error", f"音频文件不存在：{audio_path}")
            return

        resolved = str(audio_file.resolve())
        if (
            resolved == self._audio_loaded_path
            and self._audio_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState
        ):
            self._audio_player.pause()
            if self._audio_restore_path_after_preview:
                restore_path = self._audio_restore_path_after_preview
                self._audio_restore_path_after_preview = ""
                self._sync_audio_source(restore_path)
            return

        current_audio_path = (self._app_context.state.audio_path or "").strip()
        current_audio_file = Path(current_audio_path).resolve() if current_audio_path else None
        if current_audio_file is not None and current_audio_file.exists() and str(current_audio_file) != resolved:
            self._audio_restore_path_after_preview = str(current_audio_file)
        else:
            self._audio_restore_path_after_preview = ""

        self._load_audio_source(audio_file)
        self._library_preview_slider.setEnabled(True)
        self._audio_player.play()
        self._set_audio_feedback("预览中", "success", "已开始播放音频库里的参考音频。")

    def _detach_shared_audio_controls(self) -> None:
        for widget in (
            getattr(self, "_play_audio_button", None),
            getattr(self, "_audio_progress", None),
            getattr(self, "_audio_timeline", None),
            getattr(self, "_audio_volume_label", None),
            getattr(self, "_audio_volume_slider", None),
        ):
            if widget is not None and widget.parentWidget() is not None:
                widget.setParent(None)

    def _build_waveform_strip(self, color: str, *, faded_tail: bool = False) -> QWidget:
        shell = QWidget()
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        heights = [6, 12, 18, 9, 15, 21, 12, 18, 6, 15, 12, 18, 9, 15, 6, 12]
        tail_start = len(heights) - 2
        for index, height in enumerate(heights):
            bar = QFrame()
            bar.setFixedSize(2, height)
            bar_color = color
            if faded_tail and index >= tail_start:
                bar_color = "#C1CBD0"
            bar.setStyleSheet(f"background: {bar_color}; border-radius: 1px;")
            layout.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)
        return shell

    def _build_audio_variant_widget(self, variant: AudioVariant, *, selected: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("audioVariantCard")
        card.setProperty("selected", selected)
        card.setMinimumHeight(64)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        info_panel = QWidget(card)
        info_panel.setMinimumWidth(176)
        info_panel.setMaximumWidth(220)
        info_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        info_box = QVBoxLayout(info_panel)
        info_box.setContentsMargins(0, 0, 0, 0)
        info_box.setSpacing(2)
        name_label = QLabel(variant.label or Path(variant.path).stem)
        name_label.setObjectName("audioVariantName")
        name_label.setWordWrap(False)
        name_label.setMinimumHeight(20)
        name_label.setToolTip(variant.label or Path(variant.path).stem)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        stat_path = Path(variant.path)
        timestamp = datetime.fromtimestamp(stat_path.stat().st_mtime).strftime("%m-%d %H:%M") if stat_path.exists() else "刚刚"
        meta_label = QLabel(timestamp)
        meta_label.setObjectName("audioVariantMeta")
        meta_label.setWordWrap(False)
        meta_label.setMinimumHeight(18)
        meta_label.setToolTip(timestamp)
        meta_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        info_box.addWidget(name_label)
        info_box.addWidget(meta_label)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(8)
        center_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        if selected:
            center_row.addWidget(self._play_audio_button, 0, Qt.AlignmentFlag.AlignVCenter)
            center_row.addWidget(self._audio_progress, 1, Qt.AlignmentFlag.AlignVCenter)
            center_row.addWidget(self._audio_timeline, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            ghost_button = QPushButton("▶")
            ghost_button.setObjectName("ghostPlayButton")
            ghost_button.setEnabled(False)
            ghost_button.setFixedSize(34, 34)
            center_row.addWidget(ghost_button, 0, Qt.AlignmentFlag.AlignVCenter)
            center_row.addWidget(self._build_waveform_strip("#7E878B"), 1, Qt.AlignmentFlag.AlignVCenter)
            duration_text = (
                format_audio_time(int(float(variant.duration_sec) * 1000))
                if variant.duration_sec
                else "--:--"
            )
            ghost_time = QLabel(duration_text)
            ghost_time.setObjectName("audioVariantMeta")
            ghost_time.setWordWrap(False)
            center_row.addWidget(ghost_time, 0, Qt.AlignmentFlag.AlignVCenter)

        volume_row = QHBoxLayout()
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(6)
        volume_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        if selected:
            volume_row.addWidget(self._audio_volume_label, 0, Qt.AlignmentFlag.AlignVCenter)
            volume_row.addWidget(self._audio_volume_slider, 1, Qt.AlignmentFlag.AlignVCenter)
        else:
            muted_label = QLabel("音量")
            muted_label.setObjectName("fieldLabel")
            muted_slider = QSlider(Qt.Orientation.Horizontal)
            muted_slider.setRange(0, 100)
            muted_slider.setValue(100)
            muted_slider.setEnabled(False)
            muted_slider.setFixedWidth(120)
            volume_row.addWidget(muted_label, 0, Qt.AlignmentFlag.AlignVCenter)
            volume_row.addWidget(muted_slider, 1, Qt.AlignmentFlag.AlignVCenter)

        select_box = QVBoxLayout()
        select_box.setSpacing(0)
        select_box.addStretch(1)
        select_toggle = QCheckBox(card)
        select_toggle.setObjectName("audioVariantSelector")
        select_toggle.setChecked(selected)
        select_toggle.clicked.connect(lambda checked=False, path=variant.path: self._select_audio_variant_path(path))
        select_box.addWidget(select_toggle, 0, Qt.AlignmentFlag.AlignCenter)
        select_box.addStretch(1)

        layout.addWidget(info_panel, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(center_row, 4)
        layout.addLayout(volume_row, 2)
        layout.addLayout(select_box)
        return card

    def _update_copy_counter(self) -> None:
        if not hasattr(self, "_copy_counter_label"):
            return
        text = self._copy_editor.toPlainText() if hasattr(self, "_copy_editor") else ""
        count = len(text.strip())
        self._copy_counter_label.setText(f"字数 {count} / 2000")

    def _update_clone_audio_summary(self) -> None:
        if not hasattr(self, "_clone_audio_summary"):
            return
        selected = combo_value(self._managed_audio_combo) if hasattr(self, "_managed_audio_combo") else ""
        active = self._app_context.state.preferred_audio or selected
        if not active:
            self._clone_audio_summary.setText("当前未择克隆声音")
            if hasattr(self, "_library_preview_button"):
                self._library_preview_button.setEnabled(False)
            if hasattr(self, "_library_preview_slider"):
                self._library_preview_slider.setEnabled(False)
            return
        label = Path(active).name
        self._clone_audio_summary.setText(f"当前克隆源：{label}")
        self._app_context.state.preferred_audio = active
        if hasattr(self, "_library_preview_button"):
            self._library_preview_button.setEnabled(True)
        if hasattr(self, "_library_preview_slider"):
            self._library_preview_slider.setEnabled(True)

    def _sync_avatar_preview(self, state: PipelineState) -> None:
        if not hasattr(self, "_avatar_preview_title"):
            return
        reference_path = combo_value(self._reference_video_combo) if hasattr(self, "_reference_video_combo") else ""
        current_path = self._current_avatar_preview_video_path()
        is_rendering = state.is_running and state.active_task_kind in {"avatar", "full-workflow"}
        has_generated_video = bool(current_path and Path(current_path).exists())
        is_playing = (
            hasattr(self, "_avatar_video_player")
            and self._avatar_video_player.playbackState() is QMediaPlayer.PlaybackState.PlayingState
        )

        if is_rendering:
            self._avatar_preview_stack.setCurrentWidget(self._avatar_preview_placeholder)
            if hasattr(self, "_avatar_video_player"):
                self._avatar_video_player.pause()
            self._avatar_preview_badge.setText(f"{int(state.progress * 100)}%")
            self._avatar_preview_title.setText("正在渲染")
            self._avatar_preview_note.setText(state.last_message or "正在生成数字人视频...")
        elif has_generated_video:
            self._load_avatar_video_source(Path(current_path))
            self._avatar_preview_stack.setCurrentWidget(
                self._avatar_video_widget if (self._avatar_video_has_started or is_playing) else self._avatar_preview_placeholder
            )
            self._avatar_preview_badge.setText("播放")
            self._avatar_preview_title.setText("点击预览播放")
            self._avatar_preview_note.setText(Path(current_path).name)
        elif reference_path:
            self._avatar_preview_stack.setCurrentWidget(self._avatar_preview_placeholder)
            self._reset_avatar_video_player(clear_source=True)
            self._avatar_preview_badge.setText("已选")
            self._avatar_preview_title.setText("参视频已选择")
            self._avatar_preview_note.setText(Path(reference_path).name)
        else:
            self._avatar_preview_stack.setCurrentWidget(self._avatar_preview_placeholder)
            self._reset_avatar_video_player(clear_source=True)
            self._avatar_preview_badge.setText("待机")
            self._avatar_preview_title.setText("等待渲染视频")
            self._avatar_preview_note.setText("选择参考视频后即可开始数字人渲染。")
        self._avatar_preview_placeholder.setCursor(
            Qt.CursorShape.PointingHandCursor if has_generated_video else Qt.CursorShape.ArrowCursor
        )
        self._avatar_video_widget.setCursor(
            Qt.CursorShape.PointingHandCursor if has_generated_video else Qt.CursorShape.ArrowCursor
        )
        if has_generated_video:
            action_text = "点击预览暂停" if is_playing else "点击预览播放"
            self._avatar_preview_name.setText(f"生成视频{Path(current_path).name} | {action_text}")
        else:
            self._avatar_preview_name.setText(
                f"数字人素材：{Path(reference_path).name}" if reference_path else "数字人素材：暂无"
            )

    def _sync_cover_preview(self) -> None:
        if not hasattr(self, "_cover_preview_image"):
            return
        target_size = self._cover_preview_image.size()
        if target_size.width() < 32 or target_size.height() < 32:
            target_size = QSize(182, 324)
        pixmap, has_background_frame = self._compose_cover_preview(target_size)
        self._cover_preview_image.setText("")
        self._cover_preview_image.setPixmap(pixmap)

        cover_path = Path(self._cover_output_path) if self._cover_output_path else None
        if cover_path and cover_path.exists():
            if self._cover_generated_for_publish:
                self._cover_preview_meta.setText(f"已生成：\n{cover_path.name}")
            else:
                self._cover_preview_meta.setText(f"预览已更新，重新生成后导出：\n{cover_path.name}")
        elif has_background_frame:
            self._cover_preview_meta.setText("预览已按当前设置更新")
        else:
            self._cover_preview_meta.setText("尚未生成封面")

    def _show_workspace_required(self, message: str) -> None:
        self._rewrite_status.setText(message)
        self._subtitle_output.setPlainText(message)
        self._avatar_output.setPlainText(message)
        self._all_in_one_status.setPlainText(message)
        self._set_audio_feedback("未选择工作空间", "error", message)

    def _require_workspace(self) -> str | None:
        try:
            return ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._show_workspace_required(str(exc))
            return None

    def _submit_content_task(self) -> None:
        raw_input = self._video_link_input.text().strip()
        if not raw_input:
            self._copy_editor.setPlainText("请先输入链接、分享文案或原始文本。")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        self._app_context.state.source_input = raw_input
        self._source_type_hint.setText(f"来源类型：{detect_source_type(raw_input)}")
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.content,
            ContentRequest(
                source=VideoSource(source_type=detect_source_type(raw_input), raw_input=raw_input),
                workspace=workspace,
            ),
        )
        if task_id:
            self._prime_loading_button(WorkerTaskKind.content.value)

    def _submit_rewrite_task(self) -> None:
        text = self._copy_editor.toPlainText().strip()
        if not text:
            self._rewrite_status.setText("请先准备需要改写的文案。")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        self._apply_env_settings()
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.rewrite_text,
            RewriteRequest(
                text=text,
                mode=self._rewrite_mode.currentData(),
                prompt=self._rewrite_prompt.text().strip() or None,
                model=self._app_context.settings.llm.model,
                workspace=workspace,
            ),
        )

    def _submit_tts_task(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._set_audio_feedback("识别参考音频", "running", "正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._set_audio_feedback("已有任务", "running", "已有任务正在运行，请稍候或取消当前任务。")
            return

        text = self._copy_editor.toPlainText().strip()
        if not text:
            self._set_audio_feedback("待输入", "idle", "请先准备好口播文案。")
            self._audio_output.setText("尚未生成音频文件。")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        reference_audio_path = (
            self._app_context.state.preferred_audio or combo_value(self._managed_audio_combo)
        ).strip()
        if not reference_audio_path:
            self._set_audio_feedback("缺少参考音频", "error", "请先在音频库选择一条参考音频。")
            return
        if not Path(reference_audio_path).exists():
            self._set_audio_feedback("缺少参考音频", "error", f"参考音频不存在：{reference_audio_path}")
            return
        ultimate_clone_request = self._prepare_ultimate_clone_request(reference_audio_path)
        if ultimate_clone_request is None:
            self._begin_ultimate_clone_prepare(
                reference_audio_path,
                task_kind=WorkerTaskKind.tts.value,
                retry_callback=self._submit_tts_task,
                failure_callback=lambda message: self._set_audio_feedback("极致克隆准备失败", "error", message),
            )
            return
        ultimate_clone, prompt_text = ultimate_clone_request
        voice = CLONE_REFERENCE_VOICE
        self._pending_tts_voice = voice
        self._pending_tts_speed = None
        self._app_context.state.preferred_voice = voice
        self._app_context.state.preferred_audio = reference_audio_path
        self._audio_player.stop()
        self._set_audio_feedback("生成中", "running", "正在生成音频，请稍候。")
        self._refresh_primary_action_buttons()
        self._generate_audio_button.setEnabled(False)
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=voice,
                reference_audio_path=reference_audio_path,
                ultimate_clone=ultimate_clone,
                prompt_text=prompt_text,
                speed=1.0,
                workspace=workspace,
                output_name="studio-narration",
            ),
        )
        if not task_id:
            self._set_audio_feedback(
                "生成失败",
                "error",
                self._app_context.state.last_error or "音频任务提交失败，请稍后重试。",
            )
            self._refresh_primary_action_buttons()
            return
        self._prime_loading_button(WorkerTaskKind.tts.value)

    def _submit_subtitle_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._subtitle_output.setPlainText("请先生成音频，或从音频库选择一条音频。")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        self._app_context.task_controller.submit_task(
            WorkerTaskKind.subtitle,
            SubtitleRequest(
                audio_path=audio_path,
                reference_text=self._copy_editor.toPlainText().strip() or None,
                burn_in=False,
                workspace=workspace,
                output_name="studio-subtitle",
            ),
        )

    def _queue_subtitle_then_avatar(self) -> bool:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._pending_avatar_after_subtitle = False
            self._avatar_output.setPlainText("请先生成音频后再创建数字人视频。")
            return False
        workspace = self._require_workspace()
        if not workspace:
            self._pending_avatar_after_subtitle = False
            return False
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.subtitle,
            SubtitleRequest(
                audio_path=audio_path,
                reference_text=self._copy_editor.toPlainText().strip() or None,
                burn_in=False,
                workspace=workspace,
                output_name="studio-subtitle",
            ),
        )
        if not task_id:
            self._pending_avatar_after_subtitle = False
            self._avatar_output.setPlainText("字幕任务提交失败，无法继续生成数字人视频。")
            return False
        self._pending_avatar_after_subtitle = True
        self._avatar_output.setPlainText("已提交字幕任务，完成后会自动继续生成视频。")
        return True

    def _submit_avatar_request(
        self,
        *,
        audio_path: str,
        reference_video_path: str,
        workspace: str,
        subtitle_path: str,
    ) -> str | None:
        self._cover_generated_for_publish = False
        self._reset_avatar_video_player(clear_source=True)
        self._avatar_output.setPlainText("正在生成数字人视频，请稍候。")
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.avatar,
            AvatarRequest(
                audio_path=audio_path,
                model_id=UPLOADED_AVATAR_MODEL_ID,
                engine=AvatarEngine.tuilionnx,
                workspace=workspace,
                subtitle_path=subtitle_path or None,
                subtitle_style=SubtitleStyle(
                    font_name=combo_value(self._subtitle_font),
                    font_size=int(self._subtitle_font_size.value()),
                    color=self._subtitle_color.text(),
                    outline_color=self._subtitle_outline.text(),
                    bottom_margin=int(self._subtitle_margin.value()),
                ),
                reference_video_path=reference_video_path,
                overlay_text=self._app_context.state.rewritten_title or None,
                batch_size=int(self._batch_size.value()),
                sync_offset=float(self._av_offset.value()),
                scale_h=float(self._mask_height.value()),
                scale_w=float(self._mask_width.value()),
                beautify_teeth=self._beautify_checkbox.isChecked(),
            ),
        )
        if task_id:
            self._prime_loading_button(WorkerTaskKind.avatar.value)
        return task_id

    def _submit_avatar_task(self) -> None:
        audio_path = self._app_context.state.audio_path
        if not audio_path:
            self._avatar_output.setPlainText("请先生成音频。")
            return
        reference_video_path = combo_value(self._reference_video_combo).strip()
        if not reference_video_path:
            self._avatar_output.setPlainText("请先选择参考视频。")
            return
        if not Path(reference_video_path).exists():
            self._avatar_output.setPlainText(f"参考视频不存在：{reference_video_path}")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        subtitle_path = ""
        if self._include_subtitle_checkbox.isChecked():
            subtitle_path = self._ensure_subtitle_file()
            if not subtitle_path:
                self._queue_subtitle_then_avatar()
                return
        self._submit_avatar_request(
            audio_path=audio_path,
            reference_video_path=reference_video_path,
            workspace=workspace,
            subtitle_path=subtitle_path,
        )

    def _save_subtitle_text(self) -> None:
        target = self._subtitle_text_target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._subtitle_output.toPlainText(), encoding="utf-8")
        self._subtitle_text_path = str(target)
        self._app_context.state.subtitle_path = str(target)
        self._app_context.task_controller.publish_state()

    def _apply_subtitle_style(self) -> bool:
        video_path = self._current_video_path()
        if not video_path:
            self._avatar_output.setPlainText("请先生成或选择一个视频，再应用字幕样式。")
            return False
        subtitle_path = self._ensure_subtitle_file()
        if not subtitle_path:
            self._avatar_output.setPlainText("请先准备字幕文件。")
            return False
        style = SubtitleStyle(
            font_name=combo_value(self._subtitle_font),
            font_size=int(self._subtitle_font_size.value()),
            color=self._subtitle_color.text(),
            outline_color=self._subtitle_outline.text(),
            bottom_margin=int(self._subtitle_margin.value()),
        )
        output_dir = ensure_module_dir(
            self._workspace_input.text().strip() or self._app_context.state.workspace,
            "subtitle",
        )
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_styled.mp4"
        rendered = self._ffmpeg.burn_subtitles(video_path, subtitle_path, output_path, style=style)
        self._app_context.state.final_video_path = str(rendered)
        self._cover_generated_for_publish = False
        self._app_context.task_controller.publish_state()
        self._avatar_output.setPlainText(self._avatar_status_text(self._app_context.state))
        return True

    def _apply_bgm(self) -> bool:
        video_path = self._current_video_path()
        if not video_path:
            self._bgm_status.setPlainText("请先生成或选择视频，再叠加 BGM。")
            return False
        bgm_path = self._selected_bgm_path()
        if not bgm_path:
            self._bgm_status.setPlainText("请先选择一条 BGM。")
            return False
        workspace = self._require_workspace()
        if not workspace:
            self._bgm_status.setPlainText("请先选择工作空间。")
            return False
        output_dir = ensure_module_dir(workspace, "bgm")
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_bgm.mp4"
        rendered = self._ffmpeg.mix_background_music(
            video_path,
            bgm_path,
            output_path,
            bgm_volume=float(self._bgm_volume.value()) / 100.0,
        )
        self._app_context.state.final_video_path = str(rendered)
        self._cover_generated_for_publish = False
        self._app_context.task_controller.publish_state()
        self._bgm_status.setPlainText(f"BGM 已应用\n输出：{rendered}\n音频：{bgm_path}")
        return True

    def _generate_description(self) -> None:
        copy_text = self._copy_editor.toPlainText().strip()
        if not copy_text:
            self._description_output.setPlainText("请先准备口播文案，再生成发布描述。")
            return
        self._apply_env_settings()
        client = self._llm_client()
        if client.is_configured():
            try:
                response = client.generate(
                    DESCRIPTION_USER_PROMPT.format(text=copy_text),
                    system_prompt=DESCRIPTION_SYSTEM_PROMPT,
                    model=self._app_context.settings.llm.model,
                    temperature=0.8,
                )
                self._description_output.setPlainText(response.text)
                return
            except Exception as exc:
                fallback = description_fallback(copy_text, self._app_context.state.tags)
                self._description_output.setPlainText(f"{fallback}\n\n[AI 生成失败，已使用兜底文案] {exc}")
                return
        self._description_output.setPlainText(description_fallback(copy_text, self._app_context.state.tags))

    def _generate_cover(self) -> bool:
        video_path = self._current_video_path() or self._app_context.state.source_video_path
        if not video_path:
            self._cover_status.setPlainText("请先生成或选择视频，再制作封面。")
            return False
        copy_text = self._copy_editor.toPlainText().strip()
        if self._ai_cover_copy_checkbox.isChecked() and copy_text:
            headline, highlight = self._generate_cover_copy(copy_text)
            if not self._cover_text.text().strip():
                self._cover_text.setText(headline)
            if not self._cover_highlight.text().strip():
                self._cover_highlight.setText(highlight)
        title = self._cover_text.text().strip() or title_from_description(self._description_output.toPlainText())
        highlight = self._cover_highlight.text().strip()
        workspace = self._require_workspace()
        if not workspace:
            self._cover_status.setPlainText("请先选择工作空间。")
            return False
        output_dir = ensure_module_dir(workspace, "cover")
        output_path = output_dir / f"{sanitize_filename(Path(video_path).stem)}_cover.png"
        rendered = self._ffmpeg.render_cover_image(
            video_path,
            output_path,
            timestamp_sec=float(self._cover_frame_time.value()),
            title=title,
            highlight_text=highlight,
            font_name=combo_value(self._cover_font),
            font_size=int(self._cover_font_size.value()),
            font_color=self._cover_font_color.text(),
            highlight_color=self._cover_highlight_color.text(),
            position=combo_value(self._cover_position),
        )
        self._cover_output_path = str(rendered)
        self._cover_generated_for_publish = True
        self._cover_status.setPlainText(f"封面已生成\n{rendered}")
        self._sync_cover_preview()
        return True

    def _publish_selected_platforms(self) -> None:
        self._publish(self._current_publish_platforms())

    def _publish(self, platforms: list[PublishPlatform] | None) -> None:
        video_path = self._current_video_path()
        if not video_path:
            self._publish_status.setPlainText("请先生成可发布的视频。")
            return
        description = self._description_output.toPlainText().strip()
        if not description:
            self._publish_status.setPlainText("请先生成发布描述。")
            return
        selected_platforms = platforms or self._current_publish_platforms()
        if not selected_platforms:
            self._publish_status.setPlainText("请至少勾选一个发布平台。")
            return
        cover_path = (
            self._cover_output_path
            if self._publish_with_cover.isChecked() and self._cover_generated_for_publish
            else None
        )
        tags = [tag for tag in description.split() if tag.startswith("#")]
        title = title_from_description(description)
        lines: list[str] = []
        results = []
        for item in selected_platforms:
            result = self._app_context.services.publish.publish(
                PublishRequest(
                    platform=item,
                    video_path=video_path,
                    title=title,
                    tags=tags,
                    cover_path=cover_path,
                    description=description,
                )
            )
            results.append(result)
            line = f"{item.value}: {result.status or 'unknown'}"
            if result.error:
                line += f" / {result.error.message}"
            lines.append(line)
        if results and all(result.error and result.error.code == "publish_not_configured" for result in results):
            lines.insert(0, "当前版本暂未内置自动发布，仅生成平台素材。")
        self._publish_status.setPlainText("\n".join(lines))

    def _run_all_in_one(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._all_in_one_status.setPlainText("正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._all_in_one_status.setPlainText("已有任务正在运行，请稍候或取消当前任务。")
            return

        source_text = self._video_link_input.text().strip()
        if not source_text:
            self._all_in_one_status.setPlainText("请先输入链接、分享文案或原始文本，再运行完整流程。")
            return
        workspace = self._require_workspace()
        if not workspace:
            return
        reference_audio_path = (
            self._app_context.state.preferred_audio or combo_value(self._managed_audio_combo)
        ).strip()
        if not reference_audio_path:
            self._all_in_one_status.setPlainText("请先选择参考音频，再运行完整流程。")
            return
        if not Path(reference_audio_path).exists():
            self._all_in_one_status.setPlainText(f"参考音频不存在：{reference_audio_path}")
            return
        self._app_context.state.preferred_audio = reference_audio_path
        reference_video_path = combo_value(self._reference_video_combo).strip()
        if not reference_video_path:
            self._all_in_one_status.setPlainText("请先选择参考视频，再运行完整流程。")
            return
        if not Path(reference_video_path).exists():
            self._all_in_one_status.setPlainText(f"参考视频不存在：{reference_video_path}")
            return
        ultimate_clone_request = self._prepare_ultimate_clone_request(reference_audio_path)
        if ultimate_clone_request is None:
            self._all_in_one_status.setPlainText("正在识别参考音频文字，用于精准匹配...")
            self._begin_ultimate_clone_prepare(
                reference_audio_path,
                task_kind=WorkerTaskKind.full_workflow.value,
                retry_callback=self._run_all_in_one,
                failure_callback=lambda message: self._all_in_one_status.setPlainText(f"极致克隆准备失败：{message}"),
            )
            return
        ultimate_clone, prompt_text = ultimate_clone_request
        request = GenerateVideoWorkflowRequest(
            source=VideoSource(source_type=detect_source_type(source_text), raw_input=source_text),
            rewrite_mode=self._rewrite_mode.currentData(),
            rewrite_prompt=self._rewrite_prompt.text().strip() or None,
            rewrite_model=self._app_context.settings.llm.model,
            voice=CLONE_REFERENCE_VOICE,
            reference_audio_path=reference_audio_path,
            ultimate_clone=ultimate_clone,
            prompt_text=prompt_text,
            voice_speed=1.0,
            avatar_model_id=UPLOADED_AVATAR_MODEL_ID,
            avatar_engine=AvatarEngine.tuilionnx,
            subtitle_burn_in=self._burn_subtitle_checkbox.isChecked(),
            subtitle_style=SubtitleStyle(
                font_name=combo_value(self._subtitle_font),
                font_size=int(self._subtitle_font_size.value()),
                color=self._subtitle_color.text(),
                outline_color=self._subtitle_outline.text(),
                bottom_margin=int(self._subtitle_margin.value()),
            ),
            reference_video_path=reference_video_path,
            workspace=workspace,
        )
        self._auto_pipeline_active = True
        self._auto_pipeline_log = ["已提交完整流程任务。"]
        self._all_in_one_status.setPlainText("\n".join(self._auto_pipeline_log))
        self._pending_tts_voice = request.voice
        self._pending_tts_speed = None
        self._refresh_primary_action_buttons()
        self._all_in_one_button.setEnabled(False)
        task_id = self._app_context.task_controller.submit_task(WorkerTaskKind.full_workflow, request)
        if not task_id:
            self._auto_pipeline_active = False
            self._all_in_one_status.setPlainText(self._app_context.state.last_error or "完整流程任务提交失败，请稍后重试。")
            self._refresh_primary_action_buttons()
            return
        self._prime_loading_button(WorkerTaskKind.full_workflow.value)

    def _continue_auto_pipeline(self) -> None:
        self._auto_pipeline_log.append("主流程已完成，开始补齐发布素材。")
        if not self._description_output.toPlainText().strip():
            self._generate_description()
            self._auto_pipeline_log.append("已生成发布描述。")
        if self._burn_subtitle_checkbox.isChecked():
            try:
                if self._apply_subtitle_style():
                    self._auto_pipeline_log.append("已烧录字幕。")
            except Exception as exc:
                self._auto_pipeline_log.append(f"字幕后处理失败：{exc}")
        if self._enable_bgm_checkbox.isChecked():
            try:
                if self._apply_bgm():
                    self._auto_pipeline_log.append("已叠加 BGM。")
            except Exception as exc:
                self._auto_pipeline_log.append(f"BGM 处理失败：{exc}")
        if self._auto_cover_checkbox.isChecked():
            try:
                if self._generate_cover():
                    self._auto_pipeline_log.append("已生成封面。")
            except Exception as exc:
                self._auto_pipeline_log.append(f"封面生成失败：{exc}")
        self._publish(self._current_publish_platforms())
        publish_text = self._publish_status.toPlainText().strip()
        if publish_text:
            self._auto_pipeline_log.append("发布结果")
            self._auto_pipeline_log.extend(publish_text.splitlines())
        self._all_in_one_status.setPlainText("\n".join(self._auto_pipeline_log))
        self._auto_pipeline_active = False

    def _refresh_voice_options(self) -> None:
        self._app_context.state.preferred_voice = CLONE_REFERENCE_VOICE

    def _refresh_avatar_options(self) -> None:
        return

    def _refresh_reference_options(self) -> None:
        selected = combo_value(self._reference_video_combo) or self._app_context.state.preferred_reference_video
        self._reference_video_combo.clear()
        self._reference_video_combo.addItem("请选择参考视频", "")
        for path in list_reference_videos(self._app_context):
            self._reference_video_combo.addItem(Path(path).name, path)
        if selected:
            index = self._reference_video_combo.findData(selected)
            if index >= 0:
                self._reference_video_combo.setCurrentIndex(index)
            else:
                self._reference_video_combo.setCurrentIndex(0)
        else:
            self._reference_video_combo.setCurrentIndex(0)
        self._sync_avatar_preview(self._app_context.state)

    def _refresh_bgm_options(self) -> None:
        selected = combo_value(self._bgm_combo) or self._app_context.state.preferred_bgm
        self._bgm_combo.clear()
        self._bgm_combo.addItem("请选择 BGM", "")
        for path in list_bgm_candidates(self._app_context):
            self._bgm_combo.addItem(Path(path).name, path)
        if selected:
            index = self._bgm_combo.findData(selected)
            if index >= 0:
                self._bgm_combo.setCurrentIndex(index)
                return
        self._bgm_combo.setCurrentIndex(0)

    def _refresh_audio_library_options(self) -> None:
        available = list_audio_candidates(self._app_context)
        selected = combo_value(self._managed_audio_combo) or self._app_context.state.preferred_audio
        if not selected and self._app_context.state.audio_path in available:
            selected = self._app_context.state.audio_path
        self._managed_audio_combo.clear()
        self._managed_audio_combo.addItem("请选择参考音频", "")
        for path in available:
            self._managed_audio_combo.addItem(Path(path).name, path)
        if selected:
            index = self._managed_audio_combo.findData(selected)
            if index >= 0:
                self._managed_audio_combo.setCurrentIndex(index)
            else:
                self._managed_audio_combo.setCurrentIndex(0)
        else:
            self._managed_audio_combo.setCurrentIndex(0)
        self._update_clone_audio_summary()

    def _use_selected_audio(self) -> None:
        audio_path = combo_value(self._managed_audio_combo)
        if not audio_path:
            self._set_audio_feedback("未选择", "idle", "请先从音频库选择一条音频。")
            return
        if not Path(audio_path).exists():
            self._set_audio_feedback("文件缺失", "error", f"音频文件不存在：{audio_path}")
            return
        self._app_context.state.upsert_audio_variant(
            path=audio_path,
            label=Path(audio_path).stem,
            source="library",
            make_selected=True,
        )
        self._sync_audio_variant_list(self._app_context.state)
        self._sync_audio_source(audio_path)
        self._set_audio_feedback("已设为当前", "success", "已将所选音频加入当前流程，可直接生成或试听。")
        self._app_context.task_controller.publish_state()

    def _sync_audio_variant_list(self, state: PipelineState) -> None:
        current_path = state.selected_audio_variant_path or state.audio_path
        if current_path and not any(item.path == current_path for item in state.audio_variants):
            state.upsert_audio_variant(
                path=current_path,
                label=Path(current_path).stem,
                source="library" if state.preferred_audio == current_path else "generated",
                make_selected=True,
            )

        self._detach_shared_audio_controls()
        self._syncing_audio_list = True
        blocker = QSignalBlocker(self._audio_variant_list)
        try:
            self._audio_variant_list.clear()
            selected_item: QListWidgetItem | None = None
            if not state.audio_variants:
                empty_item = QListWidgetItem("当前还没有生成任何音频版本")
                empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                self._audio_variant_list.addItem(empty_item)
            else:
                for variant in state.audio_variants:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, variant.path)
                    widget = self._build_audio_variant_widget(variant, selected=variant.path == current_path)
                    size_hint = widget.sizeHint()
                    size_hint.setHeight(max(size_hint.height(), 64))
                    item.setSizeHint(size_hint)
                    if variant.path == current_path:
                        selected_item = item
                    self._audio_variant_list.addItem(item)
                    self._audio_variant_list.setItemWidget(item, widget)

            if selected_item is not None:
                self._audio_variant_list.setCurrentItem(selected_item)
            else:
                self._audio_variant_list.clearSelection()
        finally:
            del blocker
            self._syncing_audio_list = False

        variant = state.current_audio_variant()
        self._audio_variant_summary.setText(
            f"当前版本：{variant.label or Path(variant.path).stem}" if variant else "当前未选择音频"
        )

    def _on_audio_variant_selection_changed(self) -> None:
        if self._syncing_audio_list:
            return
        item = self._audio_variant_list.currentItem()
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return
        self._select_audio_variant_path(path)

    def _select_audio_variant_path(self, path: str) -> None:
        normalized = path.strip()
        if not normalized:
            return
        variant = next((entry for entry in self._app_context.state.audio_variants if entry.path == normalized), None)
        source = variant.source if variant else "generated"
        if (
            normalized == self._app_context.state.selected_audio_variant_path
            and normalized == self._app_context.state.audio_path
        ):
            self._sync_audio_variant_list(self._app_context.state)
            return
        self._app_context.state.select_audio_variant(normalized, preferred_audio=source == "library")
        self._sync_audio_variant_list(self._app_context.state)
        self._sync_audio_source(normalized)
        self._sync_selected_audio_summary(self._app_context.state)
        self._set_audio_feedback("已切换", "success", "当前音频版本已切换，可继续试听或生成视频。")
        self._app_context.task_controller.publish_state()

    def _remove_selected_audio_variant(self) -> None:
        item = self._audio_variant_list.currentItem()
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return
        self._app_context.state.remove_audio_variant(path)
        self._sync_audio_variant_list(self._app_context.state)
        self._sync_audio_source(self._app_context.state.audio_path)
        self._sync_selected_audio_summary(self._app_context.state)
        self._app_context.task_controller.publish_state()

    def _save_copy_version(self) -> None:
        text = self._copy_editor.toPlainText().strip()
        if text:
            self._remember_copy_version("手动保存", text)

    def _remember_copy_version(self, prefix: str, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        if self._copy_versions and self._copy_versions[0][1] == normalized:
            return
        label = f"{prefix} {datetime.now().strftime('%H:%M:%S')}"
        self._copy_versions.insert(0, (label, normalized))
        self._copy_versions = self._copy_versions[:12]
        self._copy_version_combo.clear()
        for item_label, _ in self._copy_versions:
            self._copy_version_combo.addItem(item_label)
        self._copy_version_combo.setCurrentIndex(0)

    def _load_selected_copy_version(self) -> None:
        index = self._copy_version_combo.currentIndex()
        if 0 <= index < len(self._copy_versions):
            self._copy_editor.setPlainText(self._copy_versions[index][1])

    def _current_publish_platforms(self) -> list[PublishPlatform]:
        platforms: list[PublishPlatform] = []
        if self._publish_douyin_check.isChecked():
            platforms.append(PublishPlatform.douyin)
        if self._publish_xhs_check.isChecked():
            platforms.append(PublishPlatform.xiaohongshu)
        if self._publish_wechat_check.isChecked():
            platforms.append(PublishPlatform.wechat_channels)
        return platforms

    def _save_copy_draft(self) -> None:
        workspace = self._require_workspace()
        if not workspace:
            return
        draft_dir = ensure_module_dir(workspace, "drafts")
        copy_path = draft_dir / "studio-copy.txt"
        description_path = draft_dir / "studio-description.txt"
        copy_path.write_text(self._copy_editor.toPlainText(), encoding="utf-8")
        description_path.write_text(self._description_output.toPlainText(), encoding="utf-8")
        self._all_in_one_status.setPlainText(f"草稿已保存\n文案：{copy_path}\n描述：{description_path}")

    def _sync_selected_audio_summary(self, state: PipelineState) -> None:
        variant = state.current_audio_variant()
        if not variant:
            self._selected_audio_summary.setText("当前未选中音频版本")
            return
        lines = [f"当前选中：{variant.label or Path(variant.path).stem}"]
        detail: list[str] = []
        if variant.voice:
            detail.append(f"声音 {display_voice_label(variant.voice)}")
        if variant.speed is not None:
            detail.append(f"语速 {variant.speed:.1f}x")
        if variant.duration_sec:
            detail.append(f"时长 {variant.duration_sec:.1f}s")
        if detail:
            lines.append(" / ".join(detail))
        lines.append(Path(variant.path).name)
        self._selected_audio_summary.setText("\n".join(lines))

    def _current_video_path(self) -> str:
        for path in [
            self._app_context.state.final_video_path,
            self._app_context.state.avatar_video_path,
            self._app_context.state.source_video_path,
        ]:
            if path:
                return path
        return ""

    def _subtitle_text_target(self) -> Path:
        if self._subtitle_text_path:
            return Path(self._subtitle_text_path)
        workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        return ensure_module_dir(workspace, "subtitle") / "manual-subtitle.srt"

    def _ensure_subtitle_file(self) -> str:
        if self._subtitle_output.toPlainText().strip():
            self._save_subtitle_text()
        return self._app_context.state.subtitle_path or self._subtitle_text_path

    def _generate_cover_copy(self, copy_text: str) -> tuple[str, str]:
        self._apply_env_settings()
        client = self._llm_client()
        if client.is_configured():
            try:
                response = client.generate(
                    HOOK_USER_PROMPT.format(text=copy_text),
                    system_prompt=HOOK_SYSTEM_PROMPT,
                    model=self._app_context.settings.llm.model,
                    temperature=0.8,
                )
                lines = [line.strip() for line in response.text.splitlines() if line.strip()]
                if lines:
                    highlight = lines[1] if len(lines) > 1 else lines[0][:4]
                    return lines[0][:12], highlight[:4]
            except Exception:
                pass
        return heuristic_cover_text(copy_text, self._app_context.state.tags)

    def _auto_postprocess_avatar_if_needed(self) -> None:
        if self._burn_subtitle_checkbox.isChecked():
            try:
                self._apply_subtitle_style()
            except Exception as exc:
                self._avatar_output.appendPlainText(f"\n字幕后处理失败：{exc}")
        if self._enable_bgm_checkbox.isChecked():
            try:
                self._apply_bgm()
            except Exception as exc:
                self._avatar_output.appendPlainText(f"\nBGM 处理失败：{exc}")

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        source_text = self._video_link_input.text().strip()
        source_type = detect_source_type(source_text) if source_text else "未检测"
        self._source_type_hint.setText(f"来源类型：{source_type}")
        self._sync_audio_variant_list(state)
        self._sync_audio_source(state.audio_path)
        self._sync_selected_audio_summary(state)
        self._update_clone_audio_summary()
        self._update_copy_counter()
        self._sync_avatar_preview(state)
        self._sync_cover_preview()

        if state.subtitle_path and not self._subtitle_output.toPlainText().strip():
            path = Path(state.subtitle_path)
            if path.exists():
                self._subtitle_output.setPlainText(path.read_text(encoding="utf-8"))

        self._refresh_primary_action_buttons(state)
        self._generate_subtitle_button.setEnabled(not state.is_running)
        self._extract_button.setEnabled(not state.is_running)
        self._generate_avatar_button.setEnabled(not state.is_running)
        self._cancel_button.setEnabled(state.is_running and state.is_cancellable)
        self._publish_selected_button.setEnabled(not state.is_running)
        if not state.is_running and not self._ultimate_clone_prepare_in_progress:
            self._primed_loading_task_kind = ""
        self._refresh_loading_buttons(state)
        self._avatar_output.setPlainText(self._avatar_status_text(state))

    def _handle_task_event(self, event: object) -> None:
        if not isinstance(event, WorkerEvent):
            return

        if event.task_kind is WorkerTaskKind.tts:
            if event.event in {WorkerEventType.started, WorkerEventType.progress}:
                self._set_audio_feedback("生成中", "running", event.message or "音频生成中...")
                return
            if event.event is WorkerEventType.failed:
                error_text = event.error.message if event.error else (event.message or "音频生成失败。")
                self._set_audio_feedback("生成失败", "error", error_text)
                return
            if event.event is WorkerEventType.cancelled:
                self._set_audio_feedback("已取消", "idle", event.message or "音频生成已取消。")
                return
            if event.event is WorkerEventType.succeeded and "tts" in event.payload:
                result = TTSResult.model_validate(event.payload["tts"])
                duration = result.meta.duration_sec if result.meta else None
                audio_path = result.audio_path or self._app_context.state.audio_path
                if audio_path:
                    self._app_context.state.upsert_audio_variant(
                        path=audio_path,
                        label=Path(audio_path).stem,
                        voice=result.voice or self._pending_tts_voice,
                        speed=self._pending_tts_speed,
                        source="generated",
                        duration_sec=duration,
                        make_selected=True,
                    )
                self._set_audio_feedback("生成完成", "success", "已生成新音频版本，可直接试听或切换。")
                return

        if event.task_kind is WorkerTaskKind.subtitle:
            if event.event in {WorkerEventType.started, WorkerEventType.progress}:
                if self._pending_avatar_after_subtitle:
                    self._avatar_output.setPlainText(event.message or "正在生成字幕，完成后会自动创建数字人视频。")
                return
            if event.event is WorkerEventType.failed:
                if self._pending_avatar_after_subtitle:
                    self._pending_avatar_after_subtitle = False
                    error_text = event.error.message if event.error else (event.message or "字幕生成失败。")
                    self._avatar_output.setPlainText(f"自动字幕生成失败，已取消视频生成。\n{error_text}")
                return
            if event.event is WorkerEventType.cancelled:
                if self._pending_avatar_after_subtitle:
                    self._pending_avatar_after_subtitle = False
                    self._avatar_output.setPlainText(event.message or "字幕生成已取消，数字人视频未继续生成。")
                return

        if event.event is not WorkerEventType.succeeded:
            return

        if event.task_kind is WorkerTaskKind.content and "content" in event.payload:
            result = ContentResult.model_validate(event.payload["content"])
            if result.extracted_copy:
                self._copy_editor.setPlainText(result.extracted_copy.cleaned_text)
                self._remember_copy_version("提取", result.extracted_copy.cleaned_text)
            return

        if event.task_kind in {WorkerTaskKind.rewrite_text, WorkerTaskKind.rewrite}:
            rewrite_payload = event.payload.get("rewrite", {})
            rewritten = str(rewrite_payload.get("rewritten_text", "")).strip()
            if rewritten:
                self._copy_editor.setPlainText(rewritten)
                self._remember_copy_version("改写", rewritten)
            return

        if event.task_kind is WorkerTaskKind.subtitle and "subtitle" in event.payload:
            result = SubtitleResult.model_validate(event.payload["subtitle"])
            if result.subtitle_text:
                self._subtitle_output.setPlainText(result.subtitle_text)
            self._subtitle_text_path = result.srt_path or self._subtitle_text_path
            if result.srt_path:
                self._app_context.state.subtitle_path = result.srt_path
                self._app_context.task_controller.publish_state()
            if self._pending_avatar_after_subtitle:
                self._pending_avatar_after_subtitle = False
                subtitle_path = result.srt_path or self._ensure_subtitle_file()
                if not subtitle_path:
                    self._avatar_output.setPlainText("字幕生成完成，但未拿到字幕文件，无法继续生成视频。")
                    return
                audio_path = self._app_context.state.audio_path
                reference_video_path = combo_value(self._reference_video_combo).strip()
                if not audio_path:
                    self._avatar_output.setPlainText("字幕生成完成，但未找到音频文件，无法继续生成视频。")
                    return
                if not reference_video_path:
                    self._avatar_output.setPlainText("请先选择参考视频后再继续生成数字人视频。")
                    return
                if not Path(reference_video_path).exists():
                    self._avatar_output.setPlainText(f"参考视频不存在：{reference_video_path}")
                    return
                workspace = self._require_workspace()
                if not workspace:
                    return
                self._avatar_output.setPlainText("字幕已生成，正在继续创建数字人视频。")
                QTimer.singleShot(
                    0,
                    lambda audio_path=audio_path,
                    reference_video_path=reference_video_path,
                    workspace=workspace,
                    subtitle_path=subtitle_path: self._submit_avatar_request(
                        audio_path=audio_path,
                        reference_video_path=reference_video_path,
                        workspace=workspace,
                        subtitle_path=subtitle_path,
                    ),
                )
            return

        if event.task_kind is WorkerTaskKind.avatar and "avatar" in event.payload:
            result = AvatarResult.model_validate(event.payload["avatar"])
            self._latest_elapsed_sec = float(result.elapsed_sec or 0.0)
            self._latest_download_url = str(result.download_url or "")
            self._avatar_output.setPlainText(self._avatar_status_text(self._app_context.state))
            self._auto_postprocess_avatar_if_needed()
            return

        if event.task_kind is WorkerTaskKind.full_workflow and "workflow" in event.payload:
            workflow = event.payload["workflow"]
            artifacts = workflow.get("artifacts", {})
            content = artifacts.get("content")
            rewrite = artifacts.get("rewrite")
            tts = artifacts.get("tts")
            subtitle = artifacts.get("subtitle")
            avatar = artifacts.get("avatar")
            if content and content.get("extracted_copy"):
                text = content["extracted_copy"].get("cleaned_text", "")
                self._copy_editor.setPlainText(text)
                self._remember_copy_version("提取", text)
            if rewrite and rewrite.get("rewritten_text"):
                rewritten = str(rewrite["rewritten_text"])
                self._copy_editor.setPlainText(rewritten)
                self._remember_copy_version("改写", rewritten)
            if tts and tts.get("audio_path"):
                audio_path = str(tts["audio_path"])
                duration = None
                meta = tts.get("meta") or {}
                if isinstance(meta, dict):
                    duration = meta.get("duration_sec")
                self._app_context.state.upsert_audio_variant(
                    path=audio_path,
                    label=Path(audio_path).stem,
                    voice=str(tts.get("voice") or self._pending_tts_voice),
                    speed=self._pending_tts_speed,
                        source="generated",
                        duration_sec=float(duration) if duration is not None else None,
                        make_selected=True,
                    )
                self._set_audio_feedback("生成完成", "success", "已生成新音频版本，可直接试听或切换。")
            if subtitle and subtitle.get("subtitle_text"):
                self._subtitle_output.setPlainText(str(subtitle["subtitle_text"]))
                self._subtitle_text_path = str(subtitle.get("srt_path") or self._subtitle_text_path)
            if avatar:
                avatar_result = AvatarResult.model_validate(avatar)
                self._latest_elapsed_sec = float(avatar_result.elapsed_sec or 0.0)
                self._latest_download_url = str(avatar_result.download_url or "")
            if self._auto_pipeline_active:
                self._continue_auto_pipeline()

    def _avatar_status_text(self, state: PipelineState) -> str:
        current_path = self._current_video_path()
        lines = [
            f"状态：{status_text(state.status)}",
            f"当前视频：{current_path or '暂无'}",
            f"数字人视频：{state.avatar_video_path or '暂无'}",
            f"最终视频：{state.final_video_path or '暂无'}",
        ]
        if self._latest_elapsed_sec:
            lines.append(f"耗时：{self._latest_elapsed_sec:.2f} 秒")
        if self._latest_download_url:
            lines.append(f"下载地址：{self._latest_download_url}")
        if current_path:
            lines.append("")
            lines.append(file_summary(current_path))
        return "\n".join(lines)

    def _pick_random_bgm(self) -> None:
        candidates = list_bgm_candidates(self._app_context)
        if not candidates:
            self._bgm_status.setPlainText("背景音乐库为空，请先上传 BGM。")
            return
        path = choice(candidates)
        index = self._bgm_combo.findData(path)
        if index >= 0:
            self._bgm_combo.setCurrentIndex(index)
        self._bgm_status.setPlainText(f"已随机选择 BGM：{path}")

    def _pick_random_bgm(self) -> None:
        candidates = list_bgm_candidates(self._app_context)
        if not candidates:
            self._bgm_status.setPlainText("背景音乐库为空，请先上传 BGM。")
            return
        path = choice(candidates)
        index = self._bgm_combo.findData(path)
        if index >= 0:
            self._bgm_combo.setCurrentIndex(index)
        self._bgm_status.setPlainText(f"已随机选择 BGM：{path}")

    def _pick_random_bgm(self) -> None:
        candidates = list_bgm_candidates(self._app_context)
        if not candidates:
            self._bgm_status.setPlainText("背景音乐库为空，请先上传 BGM。")
            return
        path = choice(candidates)
        index = self._bgm_combo.findData(path)
        if index >= 0:
            self._bgm_combo.setCurrentIndex(index)
        self._bgm_status.setPlainText(f"已随机选择 BGM：{path}")
