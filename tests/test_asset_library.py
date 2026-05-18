from __future__ import annotations

from pathlib import Path

from santiszr.config.settings import AppSettings
from santiszr.core.asset_library import AssetCategory, MediaLibrary


def test_media_library_imports_lists_and_deletes_assets(temp_workspace: Path) -> None:
    settings = AppSettings(
        data_dir=temp_workspace / "data",
        cache_dir=temp_workspace / "cache",
        log_dir=temp_workspace / "logs",
    )
    library = MediaLibrary(settings)

    source_audio = temp_workspace / "source.wav"
    source_audio.write_bytes(b"fake-audio")

    imported = library.import_file(AssetCategory.audio, source_audio)
    listed = library.list_assets(AssetCategory.audio)

    assert len(listed) == 1
    assert listed[0].asset_id == imported.asset_id
    assert listed[0].path != str(source_audio)
    assert Path(listed[0].path).exists()

    removed = library.delete_asset(AssetCategory.audio, imported.asset_id)

    assert removed is not None
    assert removed.asset_id == imported.asset_id
    assert library.list_assets(AssetCategory.audio) == []
    assert Path(removed.path).exists() is False


def test_media_library_separates_categories(temp_workspace: Path) -> None:
    settings = AppSettings(
        data_dir=temp_workspace / "data",
        cache_dir=temp_workspace / "cache",
        log_dir=temp_workspace / "logs",
    )
    library = MediaLibrary(settings)

    audio_file = temp_workspace / "narration.mp3"
    audio_file.write_bytes(b"audio")
    video_file = temp_workspace / "reference.mp4"
    video_file.write_bytes(b"video")

    library.import_file(AssetCategory.audio, audio_file)
    library.import_file(AssetCategory.reference_video, video_file)

    assert len(library.list_assets(AssetCategory.audio)) == 1
    assert len(library.list_assets(AssetCategory.reference_video)) == 1
    assert library.list_assets(AssetCategory.background_music) == []
