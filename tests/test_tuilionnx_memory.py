from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("torch")
pytest.importorskip("insightface")
from santiszr.vendor.tuilionnx.lstmsync_func import _resize_frame_to_max_edge


def test_resize_frame_to_max_edge_preserves_small_frames() -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    resized = _resize_frame_to_max_edge(frame, 720)

    assert resized.shape == frame.shape


def test_resize_frame_to_max_edge_scales_large_frames() -> None:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    resized = _resize_frame_to_max_edge(frame, 720)

    assert resized.shape == (405, 720, 3)
