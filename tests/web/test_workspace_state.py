from __future__ import annotations

import os
from pathlib import Path
import shutil
from contextlib import contextmanager
from uuid import uuid4

from santiszr.config.settings import AppSettings
from santiszr.core.app_state import load_app_state
from santiszr.web.workspaces import (
    get_current_workspace,
    get_recent_workspaces,
    scan_workspace_assets,
    select_workspace,
)


@contextmanager
def _test_root() -> Path:
    root_parent = Path.cwd() / ".tmp_web_tests"
    root_parent.mkdir(parents=True, exist_ok=True)
    root = root_parent / f"workspace-state-{uuid4().hex}"
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


def test_select_workspace_initializes_layout_and_persists_recent_state() -> None:
    with _test_root() as tmp_path:
        settings = _settings(tmp_path)
        workspace = tmp_path / "custom-workspaces" / "job-001"

        summary = select_workspace(settings, workspace)
        state = load_app_state(settings)

        assert summary.path == str(workspace.resolve())
        assert state.last_workspace == str(workspace.resolve())
        assert state.recent_workspaces[0] == str(workspace.resolve())
        assert get_current_workspace(settings) is not None
        assert get_current_workspace(settings).path == str(workspace.resolve())
        assert get_recent_workspaces(settings)[0].path == str(workspace.resolve())
        for name in (
            "content",
            "rewrite",
            "tts",
            "subtitle",
            "avatar",
            "cover",
            "bgm",
            "publish",
            "uploads",
            "drafts",
            "postprocess",
        ):
            assert (workspace / name).exists()
        assert (workspace / "workspace-manifest.json").exists()


def test_scan_workspace_assets_restores_audio_and_linked_text_without_manifest() -> None:
    with _test_root() as tmp_path:
        settings = _settings(tmp_path)
        workspace = tmp_path / "manual-workspace"
        tts_dir = workspace / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        newer_audio = tts_dir / "2.wav"
        newer_text = tts_dir / "2.txt"
        older_audio = tts_dir / "1.wav"
        newer_audio.write_bytes(b"audio-2")
        newer_text.write_text("Second generated script body for audio two.", encoding="utf-8")
        older_audio.write_bytes(b"audio-1")
        os.utime(older_audio, (1_700_000_000, 1_700_000_000))
        os.utime(newer_audio, (1_700_000_200, 1_700_000_200))

        snapshot = scan_workspace_assets(workspace, settings)

        assert snapshot.manifest is None
        assert [asset.display_name for asset in snapshot.generated_audio] == ["2", "1"]
        assert snapshot.generated_audio[0].linked_text_path == str(newer_text.resolve())
        assert snapshot.generated_audio[0].text_preview == "Second generated script body for audio two."
        assert snapshot.generated_audio[0].relative_path == "tts/2.wav"
        assert snapshot.generated_audio[0].file_ref == "workspace/tts/2.wav"
