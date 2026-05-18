from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from santiszr.app import bootstrap
from santiszr.gui.pages import studio, voice


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_studio_ultimate_clone_prepare_deduplicates_clicks(monkeypatch, tmp_path: Path) -> None:
    _app()
    context = bootstrap()
    page = studio.PipelineStudioPage(context)
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(b"ref")
    context.state.preferred_audio = str(reference_audio)
    page._copy_editor.setPlainText("studio target text")
    page._ultimate_clone_checkbox.setChecked(True)
    page._workspace_input.setText(str(tmp_path / "workspace"))

    prepare_calls: list[dict[str, object]] = []
    submit_calls: list[object] = []

    def _fake_prepare(owner, app_context, reference_audio_path, *, on_ready, on_failed):  # noqa: ANN001
        prepare_calls.append(
            {
                "reference_audio_path": reference_audio_path,
                "on_ready": on_ready,
                "on_failed": on_failed,
            }
        )

    monkeypatch.setattr(studio, "prepare_ultimate_clone_prompt_text_async", _fake_prepare)
    monkeypatch.setattr(context.task_controller, "submit_task", lambda *args, **kwargs: submit_calls.append((args, kwargs)) or "task-tts")

    page._submit_tts_task()
    page._submit_tts_task()

    assert len(prepare_calls) == 1
    assert submit_calls == []
    assert page._ultimate_clone_prepare_in_progress is True
    assert page._generate_audio_button.isEnabled() is False
    assert page._all_in_one_button.isEnabled() is False

    page.deleteLater()


def test_studio_ultimate_clone_prepare_ready_submits_once(monkeypatch, tmp_path: Path) -> None:
    _app()
    context = bootstrap()
    page = studio.PipelineStudioPage(context)
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(b"ref")
    context.state.preferred_audio = str(reference_audio)
    page._copy_editor.setPlainText("studio target text")
    page._ultimate_clone_checkbox.setChecked(True)
    page._workspace_input.setText(str(tmp_path / "workspace"))

    prepare_calls: list[dict[str, object]] = []
    submit_calls: list[object] = []

    def _fake_prepare(owner, app_context, reference_audio_path, *, on_ready, on_failed):  # noqa: ANN001
        prepare_calls.append(
            {
                "reference_audio_path": reference_audio_path,
                "on_ready": on_ready,
                "on_failed": on_failed,
            }
        )

    monkeypatch.setattr(studio, "prepare_ultimate_clone_prompt_text_async", _fake_prepare)
    monkeypatch.setattr(context.task_controller, "submit_task", lambda *args, **kwargs: submit_calls.append((args, kwargs)) or "task-tts")

    page._submit_tts_task()

    cache_key = context.state.reference_transcript_key(str(reference_audio))
    context.state.reference_transcript_cache[cache_key] = "recognized prompt text"
    prepare_calls[0]["on_ready"]("recognized prompt text")
    prepare_calls[0]["on_ready"]("recognized prompt text")

    assert len(submit_calls) == 1
    assert page._ultimate_clone_prepare_in_progress is False
    payload = submit_calls[0][0][1]
    assert payload.prompt_text == "recognized prompt text"

    page.deleteLater()


def test_studio_ultimate_clone_prepare_failure_restores_button(monkeypatch, tmp_path: Path) -> None:
    _app()
    context = bootstrap()
    page = studio.PipelineStudioPage(context)
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(b"ref")
    context.state.preferred_audio = str(reference_audio)
    page._copy_editor.setPlainText("studio target text")
    page._ultimate_clone_checkbox.setChecked(True)
    page._workspace_input.setText(str(tmp_path / "workspace"))

    prepare_calls: list[dict[str, object]] = []

    def _fake_prepare(owner, app_context, reference_audio_path, *, on_ready, on_failed):  # noqa: ANN001
        prepare_calls.append(
            {
                "reference_audio_path": reference_audio_path,
                "on_ready": on_ready,
                "on_failed": on_failed,
            }
        )

    monkeypatch.setattr(studio, "prepare_ultimate_clone_prompt_text_async", _fake_prepare)

    page._submit_tts_task()
    prepare_calls[0]["on_failed"]("boom")

    assert page._ultimate_clone_prepare_in_progress is False
    assert page._generate_audio_button.isEnabled() is True
    assert "boom" in page._audio_status_hint.text()

    page.deleteLater()


def test_voice_page_keeps_generate_button_disabled_while_prepare_pending() -> None:
    _app()
    context = bootstrap()
    page = voice.VoicePage(context)
    page._ultimate_clone_prepare_in_progress = True
    page._ultimate_clone_prepare_token = 1
    page._result_view.setPlainText("正在识别参考音频文字，用于精准匹配...")

    context.state.is_running = False
    page._sync_state(context.state)

    assert page._run_button.isEnabled() is False
    assert page._run_button.text() == "识别中..."

    page.deleteLater()
