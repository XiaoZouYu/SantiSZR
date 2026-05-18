from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
from santiszr.domain.schemas.audio import TTSRequest
from santiszr.gui.i18n import status_text, task_kind_text, voice_text
from santiszr.gui.state.session import PipelineState
from santiszr.gui.ultimate_clone import cached_ultimate_clone_prompt_text, prepare_ultimate_clone_prompt_text_async
from santiszr.gui.workspace import ensure_workspace
from santiszr.workers.protocol import WorkerTaskKind


class VoicePage(QWidget):
    def __init__(self, app_context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._ultimate_clone_prepare_in_progress = False
        self._ultimate_clone_prepare_token = 0
        self._ultimate_clone_prepare_reference = ""
        self._run_button_default_text = "生成音频"
        self._run_button_prepare_text = "识别中..."
        self._run_button_running_text = "生成中..."

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("配音")
        title.setObjectName("pageTitle")
        desc = QLabel("将语音合成任务提交到后台执行。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)

        form = QFormLayout()
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
        self._speed_input = QDoubleSpinBox()
        self._speed_input.setRange(0.5, 2.0)
        self._speed_input.setSingleStep(0.1)
        self._speed_input.setValue(1.0)
        self._ultimate_clone_checkbox = QCheckBox("极致克隆 / 精准匹配")
        self._ultimate_clone_checkbox.setChecked(False)
        self._ultimate_clone_checkbox.setToolTip("开启后先在后台识别参考音频文字，再生成精准匹配音频。")
        self._text_input = QPlainTextEdit()
        self._text_input.setPlaceholderText("请输入要合成的文本，或从最近一次改写结果填充。")
        self._text_input.setPlainText(app_context.state.rewritten_text)
        fill_button = QPushButton("使用最近改写")
        fill_button.clicked.connect(self._fill_latest_text)
        self._run_button = QPushButton("生成音频")
        self._run_button.clicked.connect(self._generate_audio)
        actions = QHBoxLayout()
        actions.addWidget(fill_button)
        actions.addWidget(self._run_button)
        self._result_view = QPlainTextEdit()
        self._result_view.setReadOnly(True)

        form.addRow("工作区", self._workspace_input)
        form.addRow("音色", self._voice_input)
        form.addRow("语速", self._speed_input)
        form.addRow("", self._ultimate_clone_checkbox)
        form.addRow("文本", self._text_input)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self._result_view)

        self._app_context.task_controller.state_changed.connect(self._sync_state)
        self._sync_state(self._app_context.state)

    def _fill_latest_text(self) -> None:
        self._text_input.setPlainText(self._app_context.state.rewritten_text or self._app_context.state.extracted_text)

    def _is_audio_action_busy(self) -> bool:
        return self._ultimate_clone_prepare_in_progress or self._app_context.state.is_running

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
        self._ultimate_clone_prepare_reference = ""
        self._ultimate_clone_prepare_token += 1
        self._refresh_run_button_state()
        return True

    def _begin_ultimate_clone_prepare(self, reference_audio_path: str) -> None:
        reference_path = str(Path(reference_audio_path).expanduser().resolve())
        self._ultimate_clone_prepare_token += 1
        token = self._ultimate_clone_prepare_token
        self._ultimate_clone_prepare_in_progress = True
        self._ultimate_clone_prepare_reference = reference_path
        self._refresh_run_button_state()
        self._result_view.setPlainText("正在识别参考音频文字，用于精准匹配...")
        prepare_ultimate_clone_prompt_text_async(
            self,
            self._app_context,
            reference_path,
            on_ready=lambda _prompt_text: self._handle_ultimate_clone_prompt_ready(token, reference_path),
            on_failed=lambda message: self._handle_ultimate_clone_prompt_error(token, message),
        )

    def _generate_audio(self) -> None:
        if self._ultimate_clone_prepare_in_progress:
            self._result_view.setPlainText("正在识别参考音频文字，请稍候。")
            return
        if self._app_context.state.is_running:
            self._result_view.setPlainText("已有任务正在运行，请稍候或取消当前任务。")
            return

        text = self._text_input.toPlainText().strip()
        workspace = self._workspace_input.text().strip()
        selected_voice = self._voice_input.currentData()
        current_voice_text = self._voice_input.currentText().strip()
        voice = (
            str(selected_voice)
            if selected_voice and current_voice_text == voice_text(str(selected_voice))
            else current_voice_text
        )
        if not text:
            self._result_view.setPlainText("请输入要合成的文本。")
            return

        try:
            workspace = ensure_workspace(self._app_context, self._workspace_input.text())
        except RuntimeError as exc:
            self._result_view.setPlainText(str(exc))
            return

        ultimate_clone = self._ultimate_clone_checkbox.isChecked()
        reference_audio_path = self._app_context.state.preferred_audio or None
        if ultimate_clone:
            if not reference_audio_path:
                self._result_view.setPlainText("请先选择参考音频，再启用极致克隆。")
                return
            if not Path(reference_audio_path).exists():
                self._result_view.setPlainText(f"参考音频不存在：{reference_audio_path}")
                return
            self._app_context.state.ultimate_clone_enabled = True
            prompt_text = cached_ultimate_clone_prompt_text(self._app_context, reference_audio_path)
            if not prompt_text:
                self._begin_ultimate_clone_prepare(reference_audio_path)
                return
            self._submit_tts_request(
                text=text,
                voice=voice,
                reference_audio_path=reference_audio_path,
                ultimate_clone=True,
                prompt_text=prompt_text,
                workspace=workspace,
            )
            return

        self._app_context.state.ultimate_clone_enabled = False
        self._submit_tts_request(
            text=text,
            voice=voice,
            reference_audio_path=reference_audio_path,
            ultimate_clone=False,
            prompt_text=None,
            workspace=workspace,
        )

    def _submit_tts_request(
        self,
        *,
        text: str,
        voice: str,
        reference_audio_path: str | None,
        ultimate_clone: bool,
        prompt_text: str | None,
        workspace: str,
    ) -> str:
        self._refresh_run_button_state()
        self._run_button.setEnabled(False)
        self._run_button.setText(self._run_button_running_text)
        task_id = self._app_context.task_controller.submit_task(
            WorkerTaskKind.tts,
            TTSRequest(
                text=text,
                voice=voice,
                reference_audio_path=reference_audio_path,
                ultimate_clone=ultimate_clone,
                prompt_text=prompt_text,
                speed=float(self._speed_input.value()),
                workspace=workspace,
                output_name="voice-page",
            ),
        )
        if not task_id:
            self._result_view.setPlainText(self._app_context.state.last_error or "音频任务提交失败，请稍后重试。")
            self._refresh_run_button_state()
        return task_id

    def _handle_ultimate_clone_prompt_ready(self, token: int, reference_audio_path: str) -> None:
        if token != self._ultimate_clone_prepare_token:
            return
        if reference_audio_path != self._ultimate_clone_prepare_reference:
            return
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._generate_audio()

    def _handle_ultimate_clone_prompt_error(self, token: int, message: str) -> None:
        if not self._finish_ultimate_clone_prepare(token):
            return
        self._result_view.setPlainText(f"极致克隆准备失败：{message}")

    def _sync_state(self, state: PipelineState) -> None:
        if self._workspace_input.text() != state.workspace:
            self._workspace_input.setText(state.workspace)
        self._refresh_run_button_state()
        if self._ultimate_clone_prepare_in_progress and not state.is_running:
            return
        lines = [
            f"状态：{status_text(state.status)}",
            f"任务：{task_kind_text(state.active_task_kind or state.last_task_kind)}",
            f"进度：{int(state.progress * 100)}%",
            f"音频：{state.audio_path or '无'}",
        ]
        if state.last_error:
            lines.append(f"错误：{state.last_error}")
        self._result_view.setPlainText("\n".join(lines))
