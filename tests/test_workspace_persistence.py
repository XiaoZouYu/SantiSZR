from __future__ import annotations

from pathlib import Path

import pytest

from santiszr.app import bootstrap
from santiszr.config.settings import AppSettings
from santiszr.core.app_state import (
    AppState,
    app_state_path,
    load_app_state,
    remember_workspace,
    resolve_saved_workspace,
)
from santiszr.gui.pages import product_ui


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
    )


def test_load_app_state_returns_empty_for_missing_or_empty_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert load_app_state(settings) == AppState()

    state_file = app_state_path(settings)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("", encoding="utf-8")

    assert load_app_state(settings) == AppState()


def test_remember_workspace_updates_last_and_recent_workspaces(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = tmp_path / "workspace-a"
    second = tmp_path / "workspace-b"
    first.mkdir()
    second.mkdir()

    remember_workspace(settings, first)
    remember_workspace(settings, second)

    state = load_app_state(settings)
    assert state.last_workspace == str(second.resolve())
    assert state.recent_workspaces == [str(second.resolve()), str(first.resolve())]


def test_resolve_saved_workspace_only_returns_existing_directories(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    missing = tmp_path / "missing-workspace"
    remember_workspace(settings, missing)

    assert resolve_saved_workspace(settings) is None

    existing = tmp_path / "existing-workspace"
    existing.mkdir()
    remember_workspace(settings, existing)

    assert resolve_saved_workspace(settings) == existing.resolve()


def test_bootstrap_without_saved_workspace_keeps_workspace_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    context = bootstrap(settings)

    assert context.state.workspace == ""
    assert list((settings.data_dir / "workspaces").iterdir()) == []


def test_bootstrap_uses_saved_workspace_when_available(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = tmp_path / "saved-workspace"
    workspace.mkdir()
    remember_workspace(settings, workspace)

    context = bootstrap(settings)

    assert context.state.workspace == str(workspace.resolve())


def test_product_ui_ensure_workspace_requires_explicit_selection(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    context = bootstrap(settings)
    context.state.workspace = ""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="请先选择工作空间。"):
        product_ui.ensure_workspace(context, "")

    assert not (tmp_path / "data" / "gui-workspace").exists()
    assert context.state.workspace == ""


def test_product_ui_ensure_workspace_creates_and_persists_selected_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    context = bootstrap(settings)
    workspace = tmp_path / "chosen-workspace"

    resolved = product_ui.ensure_workspace(context, str(workspace))
    stored = load_app_state(settings)

    assert resolved == str(workspace.resolve())
    assert workspace.exists() and workspace.is_dir()
    assert context.state.workspace == str(workspace.resolve())
    assert stored.last_workspace == str(workspace.resolve())
    assert stored.recent_workspaces[0] == str(workspace.resolve())
