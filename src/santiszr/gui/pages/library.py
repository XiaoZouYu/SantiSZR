from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from santiszr.app import AppContext
from santiszr.core.asset_library import AssetCategory, ManagedAsset


def _asset_summary(asset: ManagedAsset | None) -> str:
    if asset is None:
        return "未选择资源。"

    path = Path(asset.path)
    if not path.exists():
        return f"路径：{asset.path}\n状态：文件不存在"

    updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    size_mb = path.stat().st_size / (1024 * 1024)
    return (
        f"名称：{asset.display_name}\n"
        f"原文件：{asset.original_filename}\n"
        f"路径：{path}\n"
        f"大小：{size_mb:.2f} MB\n"
        f"更新时间：{updated}"
    )


class _ManagementPageBase(QWidget):
    preferences_changed = Signal()
    go_to_workbench = Signal()

    def _build_header(self, title_text: str, desc_text: str) -> QWidget:
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(4)

        title = QLabel(title_text)
        title.setObjectName("pageTitle")
        desc = QLabel(desc_text)
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        title_box.addWidget(title)
        title_box.addWidget(desc)

        back_button = QPushButton("返回工作台")
        back_button.setObjectName("subtleButton")
        back_button.clicked.connect(self.go_to_workbench.emit)

        layout.addLayout(title_box, 1)
        layout.addWidget(back_button)
        return header

    def refresh_options(self) -> None:
        return None


class _ManagedAssetPageBase(_ManagementPageBase):
    def __init__(
        self,
        app_context: AppContext,
        *,
        category: AssetCategory,
        title: str,
        description: str,
        search_placeholder: str,
        current_button_text: str,
        empty_hint: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._category = category
        self._empty_hint = empty_hint
        self._all_assets: list[ManagedAsset] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_header(title, description))

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(search_placeholder)
        self._search_input.textChanged.connect(self._apply_filter)
        upload_button = QPushButton("上传文件")
        upload_button.setObjectName("primaryButton")
        upload_button.clicked.connect(self._upload_assets)
        refresh_button = QPushButton("刷新列表")
        refresh_button.setObjectName("subtleButton")
        refresh_button.clicked.connect(self.refresh_options)
        filter_row.addWidget(self._search_input, 1)
        filter_row.addWidget(upload_button)
        filter_row.addWidget(refresh_button)
        layout.addLayout(filter_row)

        self._status_label = QLabel("资源会复制到项目本地资源库，工作台下拉框会直接读取这里的内容。")
        self._status_label.setObjectName("mutedText")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        self._asset_list = QListWidget()
        self._asset_list.currentItemChanged.connect(self._update_details)

        side = QFrame()
        side.setObjectName("panelCard")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(20, 20, 20, 20)
        side_layout.setSpacing(12)

        info_title = QLabel("资源详情")
        info_title.setObjectName("sectionTitle")
        self._detail_view = QPlainTextEdit()
        self._detail_view.setReadOnly(True)
        self._detail_view.setMaximumHeight(220)

        self._use_button = QPushButton(current_button_text)
        self._use_button.setObjectName("primaryButton")
        self._use_button.clicked.connect(self._set_current_asset)

        delete_button = QPushButton("删除所选")
        delete_button.clicked.connect(self._delete_selected_asset)

        open_dir_button = QPushButton("打开所在目录")
        open_dir_button.setObjectName("subtleButton")
        open_dir_button.clicked.connect(self._open_selected_dir)

        side_layout.addWidget(info_title)
        side_layout.addWidget(self._detail_view)
        side_layout.addWidget(self._use_button)
        side_layout.addWidget(delete_button)
        side_layout.addWidget(open_dir_button)
        side_layout.addStretch(1)

        content_row.addWidget(self._asset_list, 2)
        content_row.addWidget(side, 3)
        layout.addLayout(content_row, 1)

        self.refresh_options()

    def refresh_options(self) -> None:
        self._all_assets = self._app_context.media_library.list_assets(self._category)
        self._apply_filter()

    def _apply_filter(self) -> None:
        keyword = self._search_input.text().strip().lower()
        selected_id = self._current_asset_id()
        self._asset_list.clear()

        for asset in self._all_assets:
            if keyword:
                haystack = f"{asset.display_name} {asset.original_filename}".lower()
                if keyword not in haystack:
                    continue
            label = asset.display_name
            if self._is_current_asset(asset):
                label = f"{label}  · 当前"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
            self._asset_list.addItem(item)

        if self._asset_list.count() == 0:
            self._detail_view.setPlainText(self._empty_hint)
            return

        for index in range(self._asset_list.count()):
            item = self._asset_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == selected_id:
                self._asset_list.setCurrentRow(index)
                break
        else:
            current_id = self._current_asset_id_from_state()
            if current_id:
                for index in range(self._asset_list.count()):
                    item = self._asset_list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) == current_id:
                        self._asset_list.setCurrentRow(index)
                        break
                else:
                    self._asset_list.setCurrentRow(0)
            else:
                self._asset_list.setCurrentRow(0)

    def _upload_assets(self) -> None:
        start_dir = str(Path(self._app_context.state.workspace or Path.cwd()))
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            f"上传{self._app_context.media_library.category_label(self._category)}",
            start_dir,
            self._app_context.media_library.file_filter(self._category),
        )
        if not file_paths:
            return

        imported_ids: list[str] = []
        failed_messages: list[str] = []
        for file_path in file_paths:
            try:
                asset = self._app_context.media_library.import_file(self._category, file_path)
            except Exception as exc:
                failed_messages.append(f"{Path(file_path).name}: {exc}")
                continue
            imported_ids.append(asset.asset_id)

        if not imported_ids and failed_messages:
            self._status_label.setText("\n".join(failed_messages))
            return

        self.refresh_options()
        self._select_asset_by_id(imported_ids[-1])
        if failed_messages:
            self._status_label.setText(
                f"已添加 {len(imported_ids)} 个资源，{len(failed_messages)} 个失败。\n" + "\n".join(failed_messages)
            )
        else:
            self._status_label.setText(f"已添加 {len(imported_ids)} 个资源。")
        self.preferences_changed.emit()

    def _delete_selected_asset(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return

        removed = self._app_context.media_library.delete_asset(self._category, asset.asset_id)
        if removed is None:
            return

        self._clear_state_for_asset(removed.path)
        self.refresh_options()
        self._status_label.setText(f"已删除：{removed.display_name}")
        self.preferences_changed.emit()
        self._app_context.task_controller.publish_state()

    def _set_current_asset(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        self._apply_current_asset(asset.path)
        self.refresh_options()
        self._status_label.setText(f"当前使用：{asset.display_name}")
        self.preferences_changed.emit()
        self._app_context.task_controller.publish_state()

    def _open_selected_dir(self) -> None:
        asset = self._current_asset()
        if asset is None:
            return
        parent = Path(asset.path).parent
        if parent.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(parent)))

    def _update_details(self, *_args: object) -> None:
        asset = self._current_asset()
        if asset is None:
            self._detail_view.setPlainText("未选择资源。")
            return
        self._detail_view.setPlainText(_asset_summary(asset))

    def _current_asset(self) -> ManagedAsset | None:
        asset_id = self._current_asset_id()
        if not asset_id:
            return None
        return next((asset for asset in self._all_assets if asset.asset_id == asset_id), None)

    def _current_asset_id(self) -> str:
        item = self._asset_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _select_asset_by_id(self, asset_id: str) -> None:
        for index in range(self._asset_list.count()):
            item = self._asset_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == asset_id:
                self._asset_list.setCurrentRow(index)
                return

    def _current_asset_id_from_state(self) -> str:
        current_path = self._current_asset_path_from_state()
        if not current_path:
            return ""
        asset = next((item for item in self._all_assets if item.path == current_path), None)
        return asset.asset_id if asset else ""

    def _is_current_asset(self, asset: ManagedAsset) -> bool:
        return asset.path == self._current_asset_path_from_state()

    def _current_asset_path_from_state(self) -> str:
        raise NotImplementedError

    def _apply_current_asset(self, path: str) -> None:
        raise NotImplementedError

    def _clear_state_for_asset(self, path: str) -> None:
        raise NotImplementedError


class AudioManagementPage(_ManagedAssetPageBase):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(
            app_context,
            category=AssetCategory.audio,
            title="音频管理",
            description="上传可复用的旁白音频或成品音频，工作台可以直接选择并试听。",
            search_placeholder="搜索音频名称",
            current_button_text="设为当前音频",
            empty_hint="当前还没有已管理音频。",
            parent=parent,
        )

    def _current_asset_path_from_state(self) -> str:
        return self._app_context.state.preferred_audio or self._app_context.state.audio_path

    def _apply_current_asset(self, path: str) -> None:
        display_name = Path(path).stem
        self._app_context.state.upsert_audio_variant(
            path=path,
            label=display_name,
            source="library",
            make_selected=True,
        )

    def _clear_state_for_asset(self, path: str) -> None:
        if self._app_context.state.preferred_audio == path:
            self._app_context.state.preferred_audio = ""
        if self._app_context.state.audio_path == path:
            self._app_context.state.audio_path = ""
        self._app_context.state.remove_audio_variant(path)


class ReferenceVideoManagementPage(_ManagedAssetPageBase):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(
            app_context,
            category=AssetCategory.reference_video,
            title="参考视频管理",
            description="统一维护工作台可选的参考视频，上传后会出现在数字人视频区域的下拉框里。",
            search_placeholder="搜索参考视频名称",
            current_button_text="设为当前参考视频",
            empty_hint="当前还没有已管理参考视频。",
            parent=parent,
        )

    def _current_asset_path_from_state(self) -> str:
        return self._app_context.state.preferred_reference_video

    def _apply_current_asset(self, path: str) -> None:
        self._app_context.state.preferred_reference_video = path

    def _clear_state_for_asset(self, path: str) -> None:
        if self._app_context.state.preferred_reference_video == path:
            self._app_context.state.preferred_reference_video = ""


class BackgroundMusicManagementPage(_ManagedAssetPageBase):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(
            app_context,
            category=AssetCategory.background_music,
            title="背景音乐管理",
            description="统一维护工作台里的背景音乐素材，上传后 BGM 下拉框会直接读取这里的内容。",
            search_placeholder="搜索背景音乐名称",
            current_button_text="设为当前背景音乐",
            empty_hint="当前还没有已管理背景音乐。",
            parent=parent,
        )

    def _current_asset_path_from_state(self) -> str:
        return self._app_context.state.preferred_bgm

    def _apply_current_asset(self, path: str) -> None:
        self._app_context.state.preferred_bgm = path

    def _clear_state_for_asset(self, path: str) -> None:
        if self._app_context.state.preferred_bgm == path:
            self._app_context.state.preferred_bgm = ""
