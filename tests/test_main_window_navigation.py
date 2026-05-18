from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from santiszr.app import bootstrap
from santiszr.config.settings import AppSettings
from santiszr.core.app_state import load_app_state
from santiszr.core.asset_library import AssetCategory
from santiszr.gui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
    )


def _window(tmp_path: Path, monkeypatch) -> tuple[object, MainWindow]:
    monkeypatch.setattr(MainWindow, "_schedule_workspace_prompt", lambda self: None)
    context = bootstrap(_settings(tmp_path))
    return context, MainWindow(context)


def test_main_window_shows_four_primary_pages(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    context, window = _window(tmp_path, monkeypatch)

    assert app is not None
    assert context.state.workspace == ""
    assert window._page_stack.count() == 4
    assert len(window._nav_buttons) == 4
    assert isinstance(window._page_stack.currentWidget(), QWidget)
    assert window._nav_buttons[0].isChecked()

    window.deleteLater()


def test_studio_audio_generation_exposes_ultimate_clone_toggle(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    _, window = _window(tmp_path, monkeypatch)

    assert app is not None
    assert hasattr(window._studio_page, "_voice_combo") is False
    assert hasattr(window._studio_page, "_ultimate_clone_checkbox")
    assert window._studio_page._ultimate_clone_checkbox.toolTip()

    window.deleteLater()


def test_studio_publish_cover_defaults_to_top_layout(tmp_path: Path, monkeypatch) -> None:
    _app()
    _, window = _window(tmp_path, monkeypatch)

    assert window._studio_page._cover_position.currentText() == "top"
    assert window._studio_page._cover_position_buttons["top"].isChecked() is True

    window.deleteLater()


def test_studio_cover_preview_uses_letterbox_instead_of_center_crop(tmp_path: Path, monkeypatch) -> None:
    _app()
    _, window = _window(tmp_path, monkeypatch)
    page = window._studio_page

    source_path = tmp_path / "wide-cover-source.png"
    image = QImage(240, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#C62828"))
    assert image.save(str(source_path))

    page._cover_output_path = str(source_path)
    page._cover_text.setText("")
    page._cover_highlight.setText("")

    pixmap, has_background_frame = page._compose_cover_preview(QSize(182, 324))
    preview = pixmap.toImage()

    assert has_background_frame is True
    assert preview.width() == 182
    assert preview.height() == 324
    assert preview.pixelColor(4, 4) == QColor("#000000")
    assert preview.pixelColor(preview.width() // 2, preview.height() // 2) == QColor("#C62828")

    window.deleteLater()


def test_studio_default_subtitle_margin_uses_bottom_safe_area(tmp_path: Path, monkeypatch) -> None:
    _app()
    _, window = _window(tmp_path, monkeypatch)

    assert window._studio_page._subtitle_margin.value() == 48

    window.deleteLater()


def test_studio_audio_toolbar_keeps_reference_clone_and_generate_controls_on_one_row(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    _, window = _window(tmp_path, monkeypatch)
    page = window._studio_page

    window.resize(1280, 900)
    window.show()
    app.processEvents()

    combo_center = page._managed_audio_combo.mapTo(window, page._managed_audio_combo.rect().center()).y()
    checkbox_center = page._ultimate_clone_checkbox.mapTo(window, page._ultimate_clone_checkbox.rect().center()).y()
    button_center = page._generate_audio_button.mapTo(window, page._generate_audio_button.rect().center()).y()

    assert abs(combo_center - checkbox_center) <= 10
    assert abs(combo_center - button_center) <= 10
    assert page._generate_audio_button.minimumHeight() >= 36

    window.deleteLater()


def test_studio_media_selectors_read_from_managed_library(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    context = bootstrap(_settings(tmp_path))

    audio_file = tmp_path / "managed-audio.wav"
    audio_file.write_bytes(b"audio")
    reference_file = tmp_path / "managed-reference.mp4"
    reference_file.write_bytes(b"video")
    bgm_file = tmp_path / "managed-bgm.mp3"
    bgm_file.write_bytes(b"bgm")

    managed_audio = context.media_library.import_file(AssetCategory.audio, audio_file)
    managed_reference = context.media_library.import_file(AssetCategory.reference_video, reference_file)
    managed_bgm = context.media_library.import_file(AssetCategory.background_music, bgm_file)

    monkeypatch.setattr(MainWindow, "_schedule_workspace_prompt", lambda self: None)
    window = MainWindow(context)

    assert app is not None
    assert window._studio_page._managed_audio_combo.isEditable() is False
    assert window._studio_page._reference_video_combo.isEditable() is False
    assert window._studio_page._bgm_combo.isEditable() is False
    assert window._studio_page._managed_audio_combo.findData(managed_audio.path) >= 0
    assert window._studio_page._reference_video_combo.findData(managed_reference.path) >= 0
    assert window._studio_page._bgm_combo.findData(managed_bgm.path) >= 0

    window.deleteLater()


def test_studio_restores_workspace_audio_variants_from_tts_directory(tmp_path: Path, monkeypatch) -> None:
    _app()
    context, window = _window(tmp_path, monkeypatch)
    workspace = tmp_path / "restored-workspace"
    tts_dir = workspace / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    older = tts_dir / "studio-narration-1.wav"
    newer = tts_dir / "studio-narration-2.wav"
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_120, 1_700_000_120))

    assert window._set_workspace(workspace) is True

    variant_paths = [item.path for item in context.state.audio_variants]
    latest_path = str(newer.resolve())
    older_path = str(older.resolve())

    assert latest_path in variant_paths
    assert older_path in variant_paths
    assert len({path for path in variant_paths}) == len(variant_paths)
    assert context.state.audio_path == latest_path
    assert context.state.selected_audio_variant_path == latest_path
    assert window._studio_page._audio_variant_list.count() >= 2

    before_paths = [item.path for item in context.state.audio_variants]
    window._studio_page.refresh_options()
    after_paths = [item.path for item in context.state.audio_variants]

    assert after_paths == before_paths

    window.deleteLater()


def test_studio_workspace_audio_restore_preserves_current_valid_selection(tmp_path: Path, monkeypatch) -> None:
    _app()
    context = bootstrap(_settings(tmp_path))
    workspace = tmp_path / "selected-workspace"
    tts_dir = workspace / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    older = tts_dir / "studio-narration-1.wav"
    newer = tts_dir / "studio-narration-2.wav"
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_120, 1_700_000_120))

    context.state.workspace = str(workspace.resolve())
    context.state.upsert_audio_variant(
        path=str(older.resolve()),
        label=older.stem,
        source="generated",
        make_selected=True,
    )

    monkeypatch.setattr(MainWindow, "_schedule_workspace_prompt", lambda self: None)
    window = MainWindow(context)

    assert context.state.audio_path == str(older.resolve())
    assert context.state.selected_audio_variant_path == str(older.resolve())
    assert any(item.path == str(newer.resolve()) for item in context.state.audio_variants)

    window.deleteLater()


def test_studio_audio_variant_card_has_min_height_and_centered_play_button(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    context, window = _window(tmp_path, monkeypatch)
    page = window._studio_page

    audio_path = tmp_path / "variant-1.wav"
    audio_path.write_bytes(b"audio")
    context.state.upsert_audio_variant(path=str(audio_path), label="1", source="generated", make_selected=True)

    page._sync_audio_variant_list(context.state)
    window.resize(1280, 900)
    window.show()
    app.processEvents()

    item = page._audio_variant_list.item(0)
    widget = page._audio_variant_list.itemWidget(item)
    name_label = next(label for label in widget.findChildren(QLabel) if label.objectName() == "audioVariantName")
    meta_label = next(label for label in widget.findChildren(QLabel) if label.objectName() == "audioVariantMeta")

    play_center = page._play_audio_button.mapTo(widget, page._play_audio_button.rect().center()).y()
    card_center = widget.rect().center().y()

    assert widget.minimumHeight() >= 64
    assert 34 <= page._play_audio_button.minimumWidth() <= 36
    assert 34 <= page._play_audio_button.maximumWidth() <= 36
    assert 34 <= page._play_audio_button.minimumHeight() <= 36
    assert 34 <= page._play_audio_button.maximumHeight() <= 36
    assert abs(play_center - card_center) <= 10
    assert name_label.wordWrap() is False
    assert meta_label.wordWrap() is False

    window.deleteLater()


def test_studio_audio_variant_list_has_visible_spacing_and_selection_does_not_reorder(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    context, window = _window(tmp_path, monkeypatch)
    page = window._studio_page

    first_audio = tmp_path / "audio-a.wav"
    second_audio = tmp_path / "audio-b.wav"
    first_audio.write_bytes(b"a")
    second_audio.write_bytes(b"b")

    context.state.upsert_audio_variant(path=str(first_audio.resolve()), label="audio-a", source="generated", make_selected=False)
    context.state.upsert_audio_variant(path=str(second_audio.resolve()), label="audio-b", source="generated", make_selected=False)

    page._sync_audio_variant_list(context.state)
    window.show()
    app.processEvents()

    initial_order = [item.path for item in context.state.audio_variants]
    assert initial_order == [str(second_audio.resolve()), str(first_audio.resolve())]
    assert page._audio_variant_list.spacing() >= 8
    assert page._audio_variant_list.item(0).data(Qt.ItemDataRole.UserRole) == str(second_audio.resolve())

    page._select_audio_variant_path(str(first_audio.resolve()))

    assert [item.path for item in context.state.audio_variants] == initial_order
    assert context.state.selected_audio_variant_path == str(first_audio.resolve())
    current_item = page._audio_variant_list.currentItem()
    assert current_item is not None
    assert current_item.data(Qt.ItemDataRole.UserRole) == str(first_audio.resolve())
    assert page._audio_variant_list.item(0).data(Qt.ItemDataRole.UserRole) == str(second_audio.resolve())

    window.deleteLater()


def test_studio_audio_variant_sync_blocks_selection_signal(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    context, window = _window(tmp_path, monkeypatch)
    page = window._studio_page

    first_audio = str(tmp_path / "audio-a.wav")
    second_audio = str(tmp_path / "audio-b.wav")
    context.state.upsert_audio_variant(path=first_audio, label="audio-a", source="generated", make_selected=True)
    context.state.upsert_audio_variant(path=second_audio, label="audio-b", source="generated", make_selected=True)

    selection_events = {"count": 0}
    try:
        page._audio_variant_list.itemSelectionChanged.disconnect()
    except RuntimeError:
        pass
    page._audio_variant_list.itemSelectionChanged.connect(
        lambda: selection_events.__setitem__("count", selection_events["count"] + 1)
    )

    page._sync_audio_variant_list(context.state)

    assert app is not None
    assert selection_events["count"] == 0
    current_item = page._audio_variant_list.currentItem()
    assert current_item is not None
    assert current_item.data(Qt.ItemDataRole.UserRole) == second_audio

    window.deleteLater()


def test_main_window_empty_workspace_badge_is_friendly(tmp_path: Path, monkeypatch) -> None:
    _app()
    _, window = _window(tmp_path, monkeypatch)

    assert window._workspace_badge.text() == "未选择工作空间"
    assert window._open_workspace_button.isEnabled() is False

    window.deleteLater()


def test_main_window_set_workspace_updates_state_inputs_and_app_state(tmp_path: Path, monkeypatch) -> None:
    _app()
    context, window = _window(tmp_path, monkeypatch)
    selected_workspace = tmp_path / "chosen-workspace"

    assert window._set_workspace(selected_workspace) is True

    expected = str(selected_workspace.resolve())
    stored = load_app_state(context.settings)

    assert context.state.workspace == expected
    assert window._studio_page._workspace_input.text() == expected
    assert window._workspace_badge.text() == f"工作空间：{selected_workspace.name}"
    assert stored.last_workspace == expected
    assert stored.recent_workspaces[0] == expected

    window.deleteLater()
