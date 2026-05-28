from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")
torch = pytest.importorskip("torch")
pytest.importorskip("insightface")
from santiszr.vendor.tuilionnx.lstmsync_func import LstmSync, _resize_frame_to_max_edge


def test_resize_frame_to_max_edge_preserves_small_frames() -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    resized = _resize_frame_to_max_edge(frame, 720)

    assert resized.shape == frame.shape


def test_resize_frame_to_max_edge_scales_large_frames() -> None:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    resized = _resize_frame_to_max_edge(frame, 720)

    assert resized.shape == (405, 720, 3)


class _FakeFaceDetector:
    def __init__(self, missing_indices: set[int]) -> None:
        self.missing_indices = missing_indices
        self.calls = 0

    def affine_transform(self, frame):
        index = self.calls
        self.calls += 1
        if index in self.missing_indices:
            raise RuntimeError("face missing")
        face = torch.zeros((3, 4, 4), dtype=torch.float32)
        return face, [0, 0, 4, 4], np.eye(2, 3, dtype=np.float32)


def test_face_detect_keeps_missing_frames_for_original_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sync = LstmSync.__new__(LstmSync)
    sync.detect_face = _FakeFaceDetector({1})
    frames = [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(3)]

    results = sync._LstmSync__face_detect(frames)

    assert len(results) == len(frames)
    assert [item[3] for item in results] == [True, False, True]
    assert results[1][0] is not None
    assert (tmp_path / "temp" / "noface.jpg").exists()


def test_face_detect_errors_only_when_all_frames_have_no_face(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sync = LstmSync.__new__(LstmSync)
    sync.detect_face = _FakeFaceDetector({0, 1})
    frames = [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(2)]

    with pytest.raises(RuntimeError, match="No face detected in any reference frame"):
        sync._LstmSync__face_detect(frames)
