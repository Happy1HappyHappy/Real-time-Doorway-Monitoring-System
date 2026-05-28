"""
Authors: Claire Liu, Yu-Jing Wei
Description: Unit tests for the pure helpers in kafka_worker — bbox cropping,
JPEG resize/encode, and embedding averaging. Heavy deps (YOLO/torch/kafka) are
stubbed via conftest.py so this runs in any Python env with numpy + opencv.

Run: pytest test_kafka_worker.py -v
"""
import base64
import math

import cv2
import numpy as np
import pytest

from kafka_worker import _crop_bbox, _encode_crop_b64, _average_embeddings


def _solid_frame(h=200, w=400, color=(0, 0, 255)) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


class TestCropBbox:
    def test_center_crop_returns_correct_region(self):
        frame = _solid_frame(200, 400)
        # cx=0.5, cy=0.5, w=0.5, h=0.5 → 200x100 box centred
        crop = _crop_bbox(frame, [0.5, 0.5, 0.5, 0.5])
        assert crop is not None
        assert crop.shape == (100, 200, 3)

    def test_bbox_partially_outside_is_clamped(self):
        frame = _solid_frame(100, 100)
        # Centred at (0,0) with full size — clamps to top-left quadrant.
        crop = _crop_bbox(frame, [0.0, 0.0, 1.0, 1.0])
        assert crop is not None
        assert crop.shape[0] > 0 and crop.shape[1] > 0

    def test_zero_size_bbox_returns_none(self):
        frame = _solid_frame()
        assert _crop_bbox(frame, [0.5, 0.5, 0.0, 0.0]) is None

    def test_bbox_entirely_outside_returns_none(self):
        frame = _solid_frame(100, 100)
        # Centred at (2, 2) — completely outside the [0,1] frame.
        assert _crop_bbox(frame, [2.0, 2.0, 0.1, 0.1]) is None


class TestEncodeCropB64:
    def test_returns_valid_base64_jpeg(self):
        crop = _solid_frame(50, 50)
        b64 = _encode_crop_b64(crop)
        assert b64 is not None
        raw = base64.b64decode(b64)
        # JPEG SOI marker
        assert raw[:2] == b"\xff\xd8"

    def test_large_crop_is_resized(self):
        # 2000x1000 should be capped to max side = 512 (default).
        crop = _solid_frame(1000, 2000)
        b64 = _encode_crop_b64(crop)
        decoded = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
        assert max(decoded.shape[:2]) <= 512

    def test_none_input_returns_none(self):
        assert _encode_crop_b64(None) is None

    def test_empty_array_returns_none(self):
        assert _encode_crop_b64(np.array([])) is None


class TestAverageEmbeddings:
    def test_average_is_l2_normalised(self):
        embs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        avg = _average_embeddings(embs)
        norm = math.sqrt(sum(x * x for x in avg))
        assert abs(norm - 1.0) < 1e-6

    def test_average_direction_correct(self):
        embs = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        avg = _average_embeddings(embs)
        assert pytest.approx(avg[0], abs=1e-6) == 1.0
        assert pytest.approx(avg[1], abs=1e-6) == 0.0

    def test_single_embedding_normalised(self):
        avg = _average_embeddings([[3.0, 4.0]])
        assert pytest.approx(avg[0], abs=1e-6) == 0.6
        assert pytest.approx(avg[1], abs=1e-6) == 0.8

    def test_zero_vectors_returns_zero(self):
        # All-zero input → norm=0 branch; output should not contain NaN.
        avg = _average_embeddings([[0.0, 0.0], [0.0, 0.0]])
        assert all(not math.isnan(x) for x in avg)
        assert avg == [0.0, 0.0]
