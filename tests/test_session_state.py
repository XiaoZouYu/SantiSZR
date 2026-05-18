from __future__ import annotations

from santiszr.gui.state.session import PipelineState


def test_pipeline_state_tracks_audio_variants_and_selection() -> None:
    state = PipelineState()

    state.upsert_audio_variant(
        path="D:/tmp/audio-a.wav",
        label="audio-a",
        voice="female",
        speed=1.0,
        source="generated",
        duration_sec=3.2,
        make_selected=True,
    )
    state.upsert_audio_variant(
        path="D:/tmp/audio-b.wav",
        label="audio-b",
        source="library",
        make_selected=True,
    )

    assert [item.path for item in state.audio_variants] == [
        "D:/tmp/audio-b.wav",
        "D:/tmp/audio-a.wav",
    ]
    assert state.audio_path == "D:/tmp/audio-b.wav"
    assert state.selected_audio_variant_path == "D:/tmp/audio-b.wav"
    assert state.preferred_audio == "D:/tmp/audio-b.wav"

    state.remove_audio_variant("D:/tmp/audio-b.wav")

    assert [item.path for item in state.audio_variants] == ["D:/tmp/audio-a.wav"]
    assert state.audio_path == "D:/tmp/audio-a.wav"
    assert state.selected_audio_variant_path == "D:/tmp/audio-a.wav"
    assert state.preferred_audio == ""


def test_pipeline_state_selecting_existing_audio_variant_preserves_order() -> None:
    state = PipelineState()

    state.upsert_audio_variant(path="D:/tmp/audio-a.wav", label="audio-a", source="generated", make_selected=False)
    state.upsert_audio_variant(path="D:/tmp/audio-b.wav", label="audio-b", source="generated", make_selected=False)

    assert [item.path for item in state.audio_variants] == [
        "D:/tmp/audio-b.wav",
        "D:/tmp/audio-a.wav",
    ]

    state.select_audio_variant("D:/tmp/audio-a.wav", preferred_audio=False)

    assert [item.path for item in state.audio_variants] == [
        "D:/tmp/audio-b.wav",
        "D:/tmp/audio-a.wav",
    ]
    assert state.audio_path == "D:/tmp/audio-a.wav"
    assert state.selected_audio_variant_path == "D:/tmp/audio-a.wav"
