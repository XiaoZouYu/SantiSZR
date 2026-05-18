from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from santiszr.app import AppContext
from santiszr.core.app_state import load_app_state
from santiszr.gui.i18n import display_text, stage_text, status_text, task_kind_text
from santiszr.gui.pages.library import (
    AudioManagementPage,
    BackgroundMusicManagementPage,
    ReferenceVideoManagementPage,
)
from santiszr.gui.pages.product_ui import SettingsDialog
from santiszr.gui.pages.studio import PipelineStudioPage
from santiszr.gui.state.session import PipelineState
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerEvent, WorkerEventType


class FailureLogDialog(QDialog):
    def __init__(self, title: str, log_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        log_view = QPlainTextEdit(self)
        log_view.setReadOnly(True)
        log_view.setPlainText(log_text)
        layout.addWidget(log_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._status_bar = QStatusBar(self)
        self._page_title = QLabel()
        self._workspace_badge = QLabel()
        self._status_overview = QLabel()
        self._switch_workspace_button = QToolButton()
        self._open_workspace_button = QToolButton()
        self._settings_button = QToolButton()
        self._nav_buttons: list[QAbstractButton] = []
        self._flow_labels: list[QLabel] = []
        self._page_titles = (
            "视频工作台",
            "音频管理",
            "参考视频管理",
            "背景音乐管理",
        )

        self._studio_page = PipelineStudioPage(self._app_context)
        self._audio_page = AudioManagementPage(self._app_context)
        self._reference_page = ReferenceVideoManagementPage(self._app_context)
        self._bgm_page = BackgroundMusicManagementPage(self._app_context)
        self._pages: list[QWidget] = [
            self._studio_page,
            self._audio_page,
            self._reference_page,
            self._bgm_page,
        ]

        self.setWindowTitle(app_context.settings.main_window.title)
        self.resize(
            app_context.settings.main_window.width,
            app_context.settings.main_window.height,
        )
        self.setMinimumSize(
            app_context.settings.main_window.min_width,
            app_context.settings.main_window.min_height,
        )

        self._build_ui()
        self._apply_style()
        self._wire_events()
        self._sync_state(self._app_context.state)
        self._schedule_workspace_prompt()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._app_context.task_controller.shutdown()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_content(), 1)
        self.setStatusBar(self._status_bar)
        self._status_bar.hide()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("sidebarRail")
        sidebar.setFixedWidth(80)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 24, 8, 24)
        layout.setSpacing(8)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(2)
        brand_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        brand_mark = QLabel("⤴")
        brand_mark.setObjectName("brandMark")
        brand_name = QLabel("SantiSZR")
        brand_name.setObjectName("brandName")
        brand_box.addWidget(brand_mark, 0, Qt.AlignmentFlag.AlignHCenter)
        brand_box.addWidget(brand_name, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(brand_box)
        layout.addSpacing(6)

        nav_specs = (
            ("视频工作台", QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("音频管理", QStyle.StandardPixmap.SP_MediaVolume),
            ("参考视频管理", QStyle.StandardPixmap.SP_FileDialogInfoView),
            ("背景音乐管理", QStyle.StandardPixmap.SP_MediaPlay),
        )
        for index, (text, icon_kind) in enumerate(nav_specs):
            button = QToolButton(self)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIcon(self.style().standardIcon(icon_kind))
            button.setText(text)
            button.clicked.connect(lambda checked=False, idx=index: self._switch_page(idx))
            self._nav_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)

        return sidebar

    def _build_content(self) -> QWidget:
        content = QWidget(self)
        content.setObjectName("contentShell")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("topBar")
        header.setFixedHeight(64)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(32, 12, 32, 12)
        header_layout.setSpacing(12)

        self._page_title.setObjectName("topBarTitle")
        header_layout.addWidget(self._page_title)
        header_layout.addStretch(1)

        self._workspace_badge.setObjectName("topBarBadge")
        self._status_overview.setObjectName("topBarStatus")
        header_layout.addWidget(self._workspace_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._switch_workspace_button.setObjectName("topBarToolButton")
        self._switch_workspace_button.setText("切换工作空间")
        self._switch_workspace_button.setToolTip("选择或切换当前工作空间")
        self._switch_workspace_button.clicked.connect(self._switch_workspace)
        header_layout.addWidget(self._switch_workspace_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._open_workspace_button.setObjectName("topBarToolButton")
        self._open_workspace_button.setText("打开目录")
        self._open_workspace_button.setToolTip("打开当前工作空间目录")
        self._open_workspace_button.clicked.connect(self._open_workspace_directory)
        header_layout.addWidget(self._open_workspace_button, 0, Qt.AlignmentFlag.AlignVCenter)

        header_layout.addWidget(self._status_overview, 0, Qt.AlignmentFlag.AlignVCenter)

        self._settings_button.setObjectName("topBarToolButton")
        self._settings_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        self._settings_button.setToolTip("运行环境与默认设置")
        self._settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(self._settings_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._page_stack = QStackedWidget(self)
        self._page_stack.setObjectName("contentStack")
        for page in self._pages:
            self._page_stack.addWidget(page)

        layout.addWidget(header)
        layout.addWidget(self._page_stack, 1)
        self._switch_page(0)
        return content

    def _wire_events(self) -> None:
        self._audio_page.preferences_changed.connect(self._refresh_all_pages)
        self._reference_page.preferences_changed.connect(self._refresh_all_pages)
        self._bgm_page.preferences_changed.connect(self._refresh_all_pages)
        self._audio_page.go_to_workbench.connect(lambda: self._switch_page(0))
        self._reference_page.go_to_workbench.connect(lambda: self._switch_page(0))
        self._bgm_page.go_to_workbench.connect(lambda: self._switch_page(0))
        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._app_context.task_controller.task_event.connect(self._handle_task_event)

    def _switch_page(self, index: int) -> None:
        self._page_stack.setCurrentIndex(index)
        for idx, button in enumerate(self._nav_buttons):
            button.setChecked(idx == index)
        if 0 <= index < len(self._page_titles):
            self._page_title.setText(self._page_titles[index])

    def _refresh_all_pages(self) -> None:
        for page in self._pages:
            refresh = getattr(page, "refresh_options", None)
            if callable(refresh):
                refresh()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._app_context, self)
        if dialog.exec():
            self._sync_workspace_inputs()
            self._refresh_all_pages()
            self._app_context.task_controller.publish_state()

    def _sync_state_legacy(self, state: PipelineState) -> None:
        workspace_name = Path(display_text(state.workspace, default="未命名视频")).name or "未命名视频"
        self._workspace_badge.setText(f"{workspace_name} - 草稿")

        if state.is_running:
            message = (
                f"{task_kind_text(state.active_task_kind)} | "
                f"{stage_text(state.active_stage)} | "
                f"{int(state.progress * 100)}%"
            )
        else:
            message = (
                f"{status_text(state.status)} | "
                f"上次任务：{task_kind_text(state.last_task_kind)} | "
                f"{display_text(state.last_message, default='等待新任务')}"
            )
        self._status_overview.setText(message)
        self._status_overview.setVisible(bool(message))

        self._status_bar.showMessage(message)
        self._sync_flow_bar(state)

    def _sync_state(self, state: PipelineState) -> None:
        workspace_path = self._current_workspace_path()
        if workspace_path is None:
            self._workspace_badge.setText("未选择工作空间")
            self._workspace_badge.setToolTip("请先选择工作空间。")
        else:
            self._workspace_badge.setText(f"工作空间：{workspace_path.name}")
            self._workspace_badge.setToolTip(str(workspace_path))
        self._open_workspace_button.setEnabled(workspace_path is not None)

        if state.is_running:
            message = (
                f"{task_kind_text(state.active_task_kind)} | "
                f"{stage_text(state.active_stage)} | "
                f"{int(state.progress * 100)}%"
            )
        else:
            message = (
                f"{status_text(state.status)} | "
                f"上次任务：{task_kind_text(state.last_task_kind)} | "
                f"{display_text(state.last_message, default='等待新任务')}"
            )
        self._status_overview.setText(message)
        self._status_overview.setVisible(bool(message))

        self._status_bar.showMessage(message)
        self._sync_flow_bar(state)

    def _schedule_workspace_prompt(self) -> None:
        QTimer.singleShot(0, self._prompt_for_workspace_if_needed)

    def _prompt_for_workspace_if_needed(self) -> None:
        if self._current_workspace_path() is not None:
            return
        app_state = load_app_state(self._app_context.settings)
        if app_state.last_workspace:
            message = f"上次工作空间不可用，请重新选择一个目录。\n\n上次路径：{app_state.last_workspace}"
        else:
            message = "请选择一个目录作为工作空间。"
        QMessageBox.information(self, "选择工作空间", message)
        self._prompt_for_workspace_selection()

    def _prompt_for_workspace_selection(self) -> bool:
        app_state = load_app_state(self._app_context.settings)
        start_dir = self._workspace_dialog_start_dir(app_state.last_workspace)
        selected = QFileDialog.getExistingDirectory(self, "选择工作空间", start_dir)
        if not selected:
            return False
        return self._set_workspace(selected)

    def _workspace_dialog_start_dir(self, last_workspace: str) -> str:
        current = self._current_workspace_path()
        if current is not None:
            return str(current)
        if last_workspace:
            try:
                return str(Path(last_workspace).expanduser().resolve().parent)
            except OSError:
                pass
        return str(Path.cwd())

    def _current_workspace_path(self) -> Path | None:
        workspace = self._app_context.state.workspace.strip()
        if not workspace:
            return None
        try:
            path = Path(workspace).expanduser().resolve()
        except OSError:
            return None
        try:
            if not path.exists() or not path.is_dir():
                return None
        except OSError:
            return None
        return path

    def _switch_workspace(self) -> None:
        if self._app_context.state.is_running:
            QMessageBox.warning(self, "无法切换工作空间", "当前任务运行中，请先完成或取消任务。")
            return
        self._prompt_for_workspace_selection()

    def _set_workspace(self, workspace: str | Path) -> bool:
        try:
            normalized = ensure_workspace(self._app_context, workspace)
        except RuntimeError as exc:
            QMessageBox.warning(self, "工作空间不可用", str(exc))
            return False
        self._sync_workspace_inputs(normalized)
        self._refresh_all_pages()
        self._app_context.task_controller.publish_state()
        return True

    def _sync_workspace_inputs(self, workspace: str | None = None) -> None:
        value = workspace if workspace is not None else self._app_context.state.workspace
        for page in self._pages:
            input_widget = getattr(page, "_workspace_input", None)
            if isinstance(input_widget, QLineEdit):
                input_widget.setText(value)

    def _open_workspace_directory(self) -> None:
        workspace_path = self._current_workspace_path()
        if workspace_path is None:
            QMessageBox.information(self, "未选择工作空间", "请先选择工作空间。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(workspace_path)))

    def _sync_flow_bar(self, state: PipelineState) -> None:
        active_index = self._flow_index(state)
        done_count = active_index if state.is_running else max(active_index, 0)
        if state.status.value == "succeeded":
            done_count = max(done_count, 7)

        for index, label in enumerate(self._flow_labels):
            phase = "idle"
            if index < done_count:
                phase = "done"
            if state.is_running and index == active_index:
                phase = "active"
            label.setProperty("phase", phase)
            label.style().unpolish(label)
            label.style().polish(label)

    def _flow_index(self, state: PipelineState) -> int:
        stage = (state.active_stage or "").strip().lower()
        mapping = {
            "content": 1,
            "rewrite": 2,
            "rewrite-text": 2,
            "tts": 3,
            "avatar": 4,
            "subtitle": 5,
            "postprocess": 6,
            "publish": 7,
        }
        if stage in mapping:
            return mapping[stage]
        last_kind = (state.last_task_kind or "").strip().lower()
        fallback = {
            "content": 1,
            "rewrite": 2,
            "rewrite-text": 2,
            "tts": 3,
            "avatar": 4,
            "subtitle": 5,
            "full-workflow": 7,
        }
        return fallback.get(last_kind, 0 if state.source_input else -1)

    def _handle_task_event(self, event: object) -> None:
        if not isinstance(event, WorkerEvent):
            return
        if event.event is not WorkerEventType.failed:
            return
        self._show_failure_logs(event)

    def _show_failure_logs(self, event: WorkerEvent) -> None:
        state = self._app_context.state
        error_message = (
            event.error.message
            if event.error
            else display_text(event.message, default=display_text(state.last_error, default="未知错误"))
        )
        logs = state.logs or [error_message]
        lines = [
            f"任务：{task_kind_text(event.task_kind.value)}",
            f"阶段：{stage_text(event.stage)}",
            f"错误：{error_message}",
            "",
            "日志：",
            *logs,
        ]
        dialog = FailureLogDialog("任务失败日志", "\n".join(lines), self)
        dialog.exec()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f8fafb;
                color: #191c1d;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QWidget#contentShell, QStackedWidget#contentStack {
                background: #f8fafb;
            }
            QFrame#sidebarRail {
                background: #f2f4f5;
                border-right: 1px solid rgba(191, 200, 204, 0.6);
            }
            QLabel#brandMark {
                color: #005a6e;
                font-size: 20px;
                font-weight: 800;
                min-width: 28px;
                min-height: 28px;
                max-width: 28px;
                max-height: 28px;
            }
            QLabel#brandName {
                color: #005a6e;
                font-size: 12px;
                font-weight: 700;
            }
            QFrame#topBar {
                background: #ffffff;
                border-bottom: 1px solid rgba(191, 200, 204, 0.45);
            }
            QLabel#topBarTitle {
                color: #004150;
                font-size: 22px;
                font-weight: 800;
            }
            QLabel#topBarBadge {
                color: #3f484c;
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.55);
                border-radius: 16px;
                padding: 7px 14px;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#topBarStatus {
                color: #005a6e;
                background: rgba(0, 90, 110, 0.08);
                border: 1px solid rgba(0, 90, 110, 0.16);
                border-radius: 14px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 700;
            }
            QToolButton#topBarToolButton {
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.55);
                border-radius: 16px;
                padding: 6px;
            }
            QToolButton#topBarToolButton:hover {
                background: #e8edef;
            }
            QFrame#panelCard, QFrame#heroCard, QFrame#subCard, QFrame#audioPreviewCard {
                background: #ffffff;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 8px;
            }
            QFrame#subCard[tone="warm"] {
                background: rgba(255, 164, 84, 0.08);
                border: 1px solid rgba(255, 164, 84, 0.28);
            }
            QFrame#toggleCard {
                background: #f8fafb;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 8px;
            }
            QFrame#toolbarChip {
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.55);
                border-radius: 8px;
            }
            QFrame#moreParamsShell {
                background: #ffffff;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 8px;
            }
            QWidget#moreParamsHeader {
                background: #f2f4f5;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid rgba(191, 200, 204, 0.35);
            }
            QFrame#groupBlock {
                background: transparent;
                border: none;
            }
            QFrame#previewStage, QLabel#coverPreviewImage {
                border: 1px solid rgba(0, 90, 110, 0.18);
                border-radius: 4px;
            }
            QFrame#previewStage {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #13161d,
                    stop:0.55 #101319,
                    stop:1 #0b0d12);
            }
            QVideoWidget#avatarVideoWidget {
                background: #000000;
                border-radius: 3px;
            }
            QFrame#previewRing {
                background: transparent;
                border: 3px solid rgba(0, 181, 220, 0.32);
                border-top-color: #00B5DC;
                border-radius: 34px;
            }
            QFrame#previewAccentLine {
                background: rgba(0, 181, 220, 0.85);
                border-radius: 1px;
            }
            QLabel#coverPreviewImage {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #9A6730,
                    stop:0.45 #734723,
                    stop:1 #3B2314);
                color: #ffffff;
                font-size: 18px;
                font-weight: 800;
                padding: 0;
            }
            QLabel#moduleBadge {
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                background: #005a6e;
                color: #ffffff;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 800;
                qproperty-alignment: AlignCenter;
            }
            QLabel#moduleTitle, QLabel#sectionTitle {
                color: #191c1d;
                font-size: 17px;
                font-weight: 800;
            }
            QFrame#moduleDivider {
                background: #eceeef;
                min-height: 1px;
                max-height: 1px;
                border: none;
            }
            QLabel#windowSubheader, QLabel#pageDesc, QLabel#mutedText, QLabel#sectionCaption, QLabel#eyebrow {
                font-size: 14px;
                color: #70787c;
            }
            QLabel#fieldLabel {
                font-size: 12px;
                font-weight: 700;
                color: #6D777B;
            }
            QLabel#copyCounter {
                color: #70787c;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.08em;
            }
            QLabel#toggleTitle, QLabel#audioVariantName {
                color: #191c1d;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#groupTitle {
                color: #70787c;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.12em;
            }
            QLabel#hexColorLabel {
                color: #3f484c;
                font-size: 12px;
                font-family: Consolas, "Courier New", monospace;
            }
            QLabel#audioVariantMeta {
                color: #70787c;
                font-size: 13px;
            }
            QLabel#previewBadge {
                min-width: 60px;
                max-width: 60px;
                padding: 4px 8px;
                border-radius: 999px;
                background: rgba(0, 90, 110, 0.2);
                color: #8ed0e7;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#previewTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 800;
            }
            QLabel#previewNote {
                color: rgba(255, 255, 255, 0.72);
                font-size: 13px;
            }
            QLabel#audioVariantIcon {
                color: #005a6e;
                font-size: 16px;
                min-width: 20px;
            }
            QLabel#audioVariantState {
                color: #70787c;
                font-size: 12px;
                font-weight: 700;
                min-width: 56px;
            }
            QLabel#audioVariantState[selected="true"] {
                color: #005a6e;
            }
            QLabel#audioVariantMarker {
                color: #70787c;
                font-size: 15px;
                font-weight: 700;
                min-width: 18px;
            }
            QLabel#audioVariantMarker[selected="true"] {
                color: #005a6e;
            }
            QFrame#audioVariantCard {
                background: #f8fafb;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 8px;
            }
            QFrame#audioVariantCard[selected="true"] {
                background: rgba(0, 90, 110, 0.06);
                border: 2px solid rgba(0, 90, 110, 0.8);
            }
            QFrame#audioVariantWave {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 90, 110, 0.15),
                    stop:0.45 rgba(0, 90, 110, 0.85),
                    stop:1 rgba(0, 90, 110, 0.2));
                min-height: 4px;
                max-height: 4px;
                border-radius: 2px;
            }
            QLabel#statusPill {
                padding: 6px 12px;
                border-radius: 999px;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#statusPill[tone="idle"] {
                color: #3f484c;
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.5);
            }
            QLabel#statusPill[tone="running"] {
                color: #904d00;
                background: rgba(255, 164, 84, 0.16);
                border: 1px solid rgba(255, 164, 84, 0.45);
            }
            QLabel#statusPill[tone="success"] {
                color: #005a6e;
                background: rgba(0, 90, 110, 0.08);
                border: 1px solid rgba(0, 90, 110, 0.16);
            }
            QLabel#statusPill[tone="error"] {
                color: #ba1a1a;
                background: rgba(255, 218, 214, 0.72);
                border: 1px solid rgba(186, 26, 26, 0.18);
            }
            QLabel#timeLabel {
                color: #70787c;
                font-size: 12px;
                font-weight: 600;
                min-width: 88px;
            }
            QLabel#workspaceBadge, QLabel#statusBadge {
                color: #3f484c;
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.55);
                border-radius: 12px;
                padding: 8px 10px;
            }
            QLabel#flowPill {
                color: #005a6e;
                background: rgba(0, 90, 110, 0.08);
                border: 1px solid rgba(0, 90, 110, 0.16);
                border-radius: 14px;
                padding: 6px 10px;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#flowPill[phase="done"] {
                color: white;
                background: #005a6e;
                border-color: #005a6e;
            }
            QLabel#flowPill[phase="active"] {
                color: white;
                background: #904d00;
                border-color: #904d00;
            }
            QToolButton#navButton, QToolButton#settingsButton {
                qproperty-iconSize: 17px;
                min-width: 56px;
                max-width: 56px;
                min-height: 60px;
                max-height: 60px;
                border-radius: 12px;
                padding: 6px 4px;
                background: transparent;
                color: #70787c;
                font-size: 12px;
                font-weight: 600;
            }
            QToolButton#navButton:hover, QToolButton#settingsButton:hover {
                background: rgba(255, 255, 255, 0.72);
                color: #004150;
            }
            QToolButton#navButton:checked {
                background: #ffffff;
                border: 1px solid rgba(191, 200, 204, 0.55);
                color: #005a6e;
            }
            QPushButton#settingsButton, QPushButton#subtleButton {
                background: #f2f4f5;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 4px;
                padding: 7px 12px;
                color: #004150;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#subtleButton:hover {
                background: #e6e8e9;
            }
            QPushButton#primaryButton {
                background: #005a6e;
                color: white;
                border: 1px solid #004150;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover {
                background: #004150;
            }
            QPushButton#accentButton {
                background: rgba(255, 164, 84, 0.14);
                color: #904d00;
                border: 1px solid rgba(255, 164, 84, 0.38);
                border-radius: 8px;
                padding: 12px;
                font-size: 14px;
                font-weight: 800;
            }
            QPushButton#accentButton:hover {
                background: rgba(255, 164, 84, 0.22);
            }
            QPushButton#bottomCtaButton {
                background: #005a6e;
                color: white;
                border: 1px solid #004150;
                border-radius: 4px;
                padding: 10px 22px;
                font-size: 15px;
                font-weight: 800;
                min-width: 220px;
            }
            QPushButton#bottomCtaButton:hover {
                background: #004150;
            }
            QPushButton#miniIconButton {
                background: transparent;
                border: none;
                color: #005a6e;
                font-size: 16px;
                font-weight: 800;
            }
            QPushButton#miniIconButton:hover {
                background: rgba(0, 90, 110, 0.08);
                border-radius: 14px;
            }
            QPushButton#ghostPlayButton {
                background: rgba(63, 72, 76, 0.14);
                color: #3f484c;
                border: none;
                border-radius: 16px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#disclosureButton {
                background: transparent;
                border: none;
                color: #005a6e;
                font-size: 14px;
                font-weight: 800;
                min-width: 20px;
                max-width: 20px;
            }
            QPushButton#helperChipButton {
                background: rgba(0, 90, 110, 0.08);
                border: 1px solid rgba(0, 90, 110, 0.18);
                border-radius: 4px;
                padding: 6px 10px;
                color: #005a6e;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton#helperChipButton:hover {
                background: rgba(0, 90, 110, 0.14);
            }
            QPushButton#helperWideButton {
                background: rgba(144, 77, 0, 0.08);
                border: 1px solid rgba(144, 77, 0, 0.18);
                border-radius: 8px;
                padding: 12px 16px;
                color: #904d00;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton#helperWideButton:hover {
                background: rgba(144, 77, 0, 0.14);
            }
            QPushButton#positionChoiceButton {
                background: #ffffff;
                border: 1px solid rgba(191, 200, 204, 0.7);
                border-radius: 4px;
                padding: 8px 10px;
                color: #3f484c;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#positionChoiceButton[selected="true"] {
                background: rgba(0, 90, 110, 0.08);
                border: 1px solid #005a6e;
                color: #005a6e;
            }
            QPushButton#primaryButton:disabled {
                background: #9bb2b8;
                border-color: #9bb2b8;
            }
            QPushButton#playButton {
                background: #005a6e;
                color: white;
                border: 1px solid #004150;
                border-radius: 17px;
                padding: 0;
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                font-size: 16px;
                font-weight: 700;
                text-align: center;
            }
            QPushButton#playButton:disabled {
                background: #d8dadb;
                color: #70787c;
                border-color: #d8dadb;
            }
            QCheckBox#audioVariantSelector {
                spacing: 0;
                padding: 0;
                margin: 0;
                min-width: 18px;
                max-width: 18px;
            }
            QCheckBox#audioVariantSelector::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid rgba(112, 120, 124, 0.7);
                background: #ffffff;
            }
            QCheckBox#audioVariantSelector::indicator:checked {
                border: 1px solid #005a6e;
                background: #005a6e;
            }
            QCheckBox#audioVariantSelector::indicator:unchecked:hover {
                border: 1px solid rgba(0, 90, 110, 0.55);
            }
            QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background: #f8fafb;
                border: 1px solid rgba(191, 200, 204, 0.8);
                border-radius: 4px;
                padding: 7px 8px;
                color: #191c1d;
                font-size: 14px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow,
            QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
                image: none;
            }
            QPlainTextEdit {
                padding: 10px;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
                border: 1px solid #005a6e;
            }
            QComboBox::drop-down {
                border: 0;
                width: 24px;
            }
            QSlider::groove:horizontal {
                background: #d3e4fe;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #005a6e;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QListWidget {
                background: #ffffff;
                border: 1px solid rgba(191, 200, 204, 0.6);
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget#audioVariantList {
                background: transparent;
                border: none;
                padding: 0;
            }
            QListWidget#audioVariantList::item {
                padding: 0;
                margin: 0 0 10px 0;
                border: none;
                background: transparent;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-radius: 6px;
                margin: 3px 0;
            }
            QListWidget::item:selected {
                background: rgba(0, 90, 110, 0.08);
                color: #004150;
            }
            QCheckBox {
                color: #191c1d;
                spacing: 8px;
                font-size: 13px;
            }
            QCheckBox#switchCheck {
                spacing: 0;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 3px;
                border: 1px solid rgba(191, 200, 204, 0.9);
                background: #ffffff;
            }
            QCheckBox#switchCheck::indicator {
                width: 36px;
                height: 20px;
                border-radius: 10px;
                border: 1px solid rgba(191, 200, 204, 0.9);
                background: #d8dadb;
            }
            QCheckBox::indicator:checked {
                background: #005a6e;
                border-color: #005a6e;
            }
            QCheckBox#switchCheck::indicator:checked {
                background: #005a6e;
                border-color: #005a6e;
            }
            QPlainTextEdit#compactLog {
                background: #f8fafb;
                border: 1px solid rgba(191, 200, 204, 0.7);
                border-radius: 4px;
                padding: 8px;
                color: #3f484c;
            }
            QStatusBar {
                background: #ffffff;
                border-top: 1px solid rgba(191, 200, 204, 0.45);
                color: #70787c;
            }
            """
        )
