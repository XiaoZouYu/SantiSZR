from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from santiszr.app import AppContext


class PublishPage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("发布")
        title.setObjectName("pageTitle")
        desc = QLabel("发布模块保留结构化结果与批量调用入口，但当前不再默认依赖外部旧项目脚本。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        status = QPlainTextEdit()
        status.setReadOnly(True)
        status.setPlainText(
            "当前状态\n"
            "- 抖音 / 小红书 / 视频号适配器会返回结构化未配置失败\n"
            "- 不会再尝试调用外部旧项目脚本、模型目录或账号目录\n"
            "- 后续需要在 SantiSZR 内置新发布器，或显式接入外部 publisher root"
        )

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(status)
        layout.addStretch(1)
