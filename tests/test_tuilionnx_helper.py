from pathlib import Path

import pytest

from santiszr.infra.avatar.tuilionnx_helper import _finalize_output


class _LockedFileError(PermissionError):
    def __init__(self, target: Path) -> None:
        super().__init__(32, "The process cannot access the file because it is being used by another process.", str(target))
        self.winerror = 32


def test_finalize_output_uses_fallback_name_when_target_file_is_locked(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    final_source = temp_workspace / "avatar" / "pending.mp4"
    final_source.parent.mkdir(parents=True, exist_ok=True)
    final_source.write_bytes(b"new-video")
    output_path = temp_workspace / "avatar" / "target.mp4"
    output_path.write_bytes(b"old-video")
    notes: list[str] = []

    original_unlink = Path.unlink

    def locked_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == output_path:
            raise _LockedFileError(self)
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    actual_output = _finalize_output(final_source, output_path, notes)

    assert actual_output != output_path
    assert actual_output.parent == output_path.parent
    assert actual_output.name.startswith("target-locked-")
    assert actual_output.suffix == ".mp4"
    assert actual_output.read_bytes() == b"new-video"
    assert output_path.read_bytes() == b"old-video"
    assert any("Target file was locked" in note for note in notes)
