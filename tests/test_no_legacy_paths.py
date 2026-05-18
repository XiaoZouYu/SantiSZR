from __future__ import annotations

from pathlib import Path


FORBIDDEN_TOKENS = (
    r"D:\shuziren\HD_HUMAN",
    "social-auto-upload-main",
    "LEGACY_TUILIONNX_ROOTS",
    r"D:\\shuziren",
)


def test_src_tree_does_not_reference_legacy_project_paths() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "santiszr"
    offenders: list[str] = []

    for path in src_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN_TOKENS:
            if token in content:
                offenders.append(f"{path}: {token}")

    assert offenders == []
