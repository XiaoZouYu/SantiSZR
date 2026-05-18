from __future__ import annotations

from pathlib import Path
import shutil
from contextlib import contextmanager
from uuid import uuid4

import pytest

from santiszr.config.settings import AppSettings
from santiszr.web.files import build_file_ref, describe_safe_file, iter_file_chunks, resolve_safe_file
from santiszr.web.workspaces import select_workspace


@contextmanager
def _test_root() -> Path:
    root_parent = Path.cwd() / ".tmp_web_tests"
    root_parent.mkdir(parents=True, exist_ok=True)
    root = root_parent / f"files-state-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
    )


def test_resolve_safe_file_allows_workspace_and_data_library_but_rejects_outside() -> None:
    with _test_root() as tmp_path:
        settings = _settings(tmp_path)
        workspace = tmp_path / "workspaces" / "web-files"
        select_workspace(settings, workspace)

        workspace_audio = workspace / "tts" / "1.wav"
        workspace_audio.write_bytes(b"workspace-audio")
        library_audio = settings.data_dir / "media-library" / "audio" / "ref.wav"
        library_audio.parent.mkdir(parents=True, exist_ok=True)
        library_audio.write_bytes(b"library-audio")
        data_cover = settings.data_dir / "cover" / "preview.png"
        data_cover.parent.mkdir(parents=True, exist_ok=True)
        data_cover.write_bytes(b"cover")
        outside = tmp_path / "outside.txt"
        outside.write_text("nope", encoding="utf-8")

        assert resolve_safe_file("workspace/tts/1.wav", workspace, settings) == workspace_audio.resolve()
        assert resolve_safe_file(str(library_audio.resolve()), workspace, settings) == library_audio.resolve()
        assert resolve_safe_file(str(data_cover.resolve()), workspace, settings) == data_cover.resolve()
        assert build_file_ref(library_audio, workspace=workspace, settings=settings) == "data/media-library/audio/ref.wav"
        assert build_file_ref(data_cover, workspace=workspace, settings=settings) == "data/cover/preview.png"
        with pytest.raises(PermissionError):
            resolve_safe_file("../outside.txt", workspace, settings)
        with pytest.raises(PermissionError):
            resolve_safe_file(str(outside.resolve()), workspace, settings)


def test_describe_safe_file_and_iter_chunks_return_workspace_file_metadata() -> None:
    with _test_root() as tmp_path:
        settings = _settings(tmp_path)
        workspace = tmp_path / "workspaces" / "web-describe"
        select_workspace(settings, workspace)

        draft = workspace / "drafts" / "draft.txt"
        draft.write_text("draft body", encoding="utf-8")

        info = describe_safe_file("workspace/drafts/draft.txt", workspace, settings)
        payload = b"".join(iter_file_chunks("workspace/drafts/draft.txt", workspace, settings, chunk_size=4))

        assert info.ref == "workspace/drafts/draft.txt"
        assert info.relative_path == "drafts/draft.txt"
        assert info.file_name == "draft.txt"
        assert info.media_type.startswith("text/")
        assert payload == b"draft body"
