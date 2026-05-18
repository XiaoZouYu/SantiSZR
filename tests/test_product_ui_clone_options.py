from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from santiszr.app import bootstrap
from santiszr.gui import ultimate_clone
from santiszr.gui.pages import product_ui


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_resolve_tts_clone_options_returns_disabled_without_reference() -> None:
    context = bootstrap()
    context.state.ultimate_clone_enabled = True

    assert product_ui.resolve_tts_clone_options(context, None) == (False, None)


def test_resolve_tts_clone_options_returns_none_when_cache_is_missing(temp_workspace: Path) -> None:
    context = bootstrap()
    context.state.ultimate_clone_enabled = True
    reference_audio = temp_workspace / "reference.wav"
    reference_audio.write_bytes(b"ref")

    assert product_ui.resolve_tts_clone_options(context, str(reference_audio)) == (True, None)


def test_resolve_tts_clone_options_returns_cached_prompt_text(temp_workspace: Path) -> None:
    context = bootstrap()
    context.state.ultimate_clone_enabled = True
    reference_audio = temp_workspace / "reference.wav"
    reference_audio.write_bytes(b"ref")
    cache_key = context.state.reference_transcript_key(str(reference_audio))
    context.state.reference_transcript_cache[cache_key] = "recognized reference transcript"

    assert product_ui.resolve_tts_clone_options(context, str(reference_audio)) == (
        True,
        "recognized reference transcript",
    )


def test_resolve_tts_clone_options_does_not_call_gui_transcription(monkeypatch, temp_workspace: Path) -> None:
    context = bootstrap()
    context.state.ultimate_clone_enabled = True
    reference_audio = temp_workspace / "reference.wav"
    reference_audio.write_bytes(b"ref")

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("GUI must not transcribe reference audio")

    monkeypatch.setattr(product_ui, "resolve_ultimate_clone_prompt_text", _raise, raising=False)

    assert product_ui.resolve_tts_clone_options(context, str(reference_audio)) == (True, None)


def test_ultimate_clone_prompt_resolution_reuses_memory_cache(temp_workspace: Path) -> None:
    context = bootstrap()
    reference_audio = temp_workspace / "reference.wav"
    reference_audio.write_bytes(b"ref")

    class FakeTranscriber:
        def __init__(self) -> None:
            self.ready_calls = 0
            self.calls: list[str] = []

        def ensure_ready(self) -> None:
            self.ready_calls += 1

        def transcribe(self, source: str) -> str:
            self.calls.append(source)
            return "recognized reference transcript"

    transcriber = FakeTranscriber()
    context.services.content.transcriber = transcriber

    first = ultimate_clone.resolve_ultimate_clone_prompt_text(context, str(reference_audio))
    second = ultimate_clone.resolve_ultimate_clone_prompt_text(context, str(reference_audio))

    assert first == "recognized reference transcript"
    assert second == "recognized reference transcript"
    assert transcriber.ready_calls == 1
    assert transcriber.calls == [str(reference_audio.resolve())]


def test_step_workflow_tts_prepare_is_not_started_twice(monkeypatch, tmp_path: Path) -> None:
    _app()
    context = bootstrap()
    context.state.ultimate_clone_enabled = True
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(b"ref")
    context.state.preferred_audio = str(reference_audio)

    page = product_ui.StepWorkflowPage(context)
    page._workspace_input.setText(str(tmp_path / "workspace"))
    page._tts_text_input.setPlainText("step workflow target text")

    prepare_calls: list[dict[str, object]] = []
    submit_calls: list[object] = []

    def _fake_prepare(owner, app_context, reference_audio_path, *, on_ready, on_failed):  # noqa: ANN001
        prepare_calls.append(
            {
                "owner": owner,
                "app_context": app_context,
                "reference_audio_path": reference_audio_path,
                "on_ready": on_ready,
                "on_failed": on_failed,
            }
        )

    monkeypatch.setattr(product_ui, "prepare_ultimate_clone_prompt_text_async", _fake_prepare)
    monkeypatch.setattr(context.task_controller, "submit_task", lambda *args, **kwargs: submit_calls.append((args, kwargs)) or "task-step")

    page._run_tts()
    page._run_tts()

    assert len(prepare_calls) == 1
    assert submit_calls == []
    assert page._ultimate_clone_prepare_in_progress is True
    assert page._tts_button.isEnabled() is False

    cache_key = context.state.reference_transcript_key(str(reference_audio))
    context.state.reference_transcript_cache[cache_key] = "recognized prompt text"
    prepare_calls[0]["on_ready"]("recognized prompt text")
    prepare_calls[0]["on_ready"]("recognized prompt text")

    assert len(submit_calls) == 1
    assert page._ultimate_clone_prepare_in_progress is False
    payload = submit_calls[0][0][1]
    assert payload.prompt_text == "recognized prompt text"
    assert page._tts_button.isEnabled() is False

    page.deleteLater()
