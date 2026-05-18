from __future__ import annotations

from pathlib import Path


MOJIBAKE_FRAGMENTS = (
    "\u93c2",
    "\u6d60",
    "\u7487",
    "\u93b4",
    "\u9359",
    "\u95ca",
    "\u9422",
    "\u701b",
    "\u6d93",
    "\u7edb",
    "\u95bf",
    "\u951b\u6b7f",
    "\u9225",
)


def test_source_tree_has_no_known_mojibake_fragments() -> None:
    source_root = Path("src") / "santiszr"
    offenders: list[str] = []

    for path in source_root.rglob("*.py"):
        if "__pycache__" in path.parts or "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [fragment for fragment in MOJIBAKE_FRAGMENTS if fragment in text]
        if hits:
            offenders.append(f"{path}: {', '.join(hits)}")

    assert offenders == []
