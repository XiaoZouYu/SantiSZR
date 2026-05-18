from __future__ import annotations

from pathlib import Path

from santiszr.app import AppContext
from santiszr.core.app_state import remember_workspace


WORKSPACE_REQUIRED_MESSAGE = "请先选择工作空间。"


def ensure_workspace(app_context: AppContext, raw_workspace: str | Path | None) -> str:
    workspace = str(raw_workspace or app_context.state.workspace or "").strip()
    if not workspace:
        raise RuntimeError(WORKSPACE_REQUIRED_MESSAGE)

    try:
        path = Path(workspace).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"工作空间不可用：{exc}") from exc

    normalized = str(path)
    app_context.state.workspace = normalized
    remember_workspace(app_context.settings, normalized)
    return normalized
