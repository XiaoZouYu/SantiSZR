from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from santiszr.app import bootstrap
from santiszr.gui.pages import product_ui


def _fail_if_called(*_args: object, **_kwargs: object) -> list[str]:
    raise AssertionError("expensive option loading should not run during dialog construction")


def test_settings_dialog_opens_without_sync_option_loading(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(product_ui.SettingsDialog, "_start_loading_options", lambda self: None)
    monkeypatch.setattr(product_ui, "list_voices", _fail_if_called)
    monkeypatch.setattr(product_ui, "list_avatar_models", _fail_if_called)
    monkeypatch.setattr(product_ui, "list_reference_videos", _fail_if_called)

    context = bootstrap()
    dialog = product_ui.SettingsDialog(context)

    assert app is not None
    assert product_ui.combo_value(dialog._default_voice_input) == product_ui.current_voice(context)
    assert product_ui.combo_value(dialog._default_avatar_input) == product_ui.current_avatar_model(context)
    assert dialog._default_reference_input.count() == 1
    assert dialog._option_status.text() == "正在加载可用语音和素材..."
    assert dialog._diagnostic_button.text() == "运行环境自检"
    assert "发布功能未内置" in dialog._diagnostic_output.toPlainText()

    dialog.deleteLater()
