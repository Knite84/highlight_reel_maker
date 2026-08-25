from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.renderer import photo_crop


def _save_jpg(tmp_path: Path, width: int, height: int, name: str = "p.jpg") -> Path:
    photo = tmp_path / name
    Image.new("RGB", (width, height), (10, 120, 200)).save(photo)
    return photo


def test_focus_from_faces_weights_and_contains_largest_head():
    faces = np.array(
        [
            [180.0, 100.0, 40.0, 40.0],
            [100.0, 1200.0, 200.0, 200.0],
        ],
        dtype=np.float32,
    )
    cx, cy = photo_crop.focus_from_faces(faces, 400, 1600)
    assert cx == pytest.approx(0.5, abs=0.35)
    win_h = 400 / (16 / 9)
    margin = photo_crop.SAFETY_MARGIN_FRAC * win_h
    largest_head_top = 1200.0 - 200.0 * photo_crop.HEAD_PAD_TOP
    largest_head_bottom = 1200.0 + 200.0 + 200.0 * photo_crop.HEAD_PAD_BOTTOM
    padded_span = (largest_head_bottom - largest_head_top) + 2 * margin
    if padded_span <= win_h:
        window_top = cy * 1600 - win_h / 2
        window_bottom = cy * 1600 + win_h / 2
        assert window_top <= largest_head_top - margin + 1e-6
        assert window_bottom >= largest_head_bottom + margin - 1e-6
    else:
        span_mid = (largest_head_top - margin + largest_head_bottom + margin) / 2
        assert cy == pytest.approx(span_mid / 1600.0)


def test_focus_from_faces_single_face_keeps_full_head():
    faces = np.array([[150.0, 150.0, 100.0, 100.0]], dtype=np.float32)
    _cx, cy = photo_crop.focus_from_faces(faces, 400, 800)
    win_h = 400 / (16 / 9)
    window_top = cy * 800 - win_h / 2
    margin = photo_crop.SAFETY_MARGIN_FRAC * win_h
    head_top = 150.0 - 100.0 * photo_crop.HEAD_PAD_TOP
    head_bottom = 150.0 + 100.0 + 100.0 * photo_crop.HEAD_PAD_BOTTOM
    assert window_top <= head_top - margin + 1e-6
    assert window_top + win_h >= head_bottom + margin - 1e-6


def test_focus_from_faces_zero_area_returns_fallback():
    faces = np.array([[10.0, 10.0, 0.0, 0.0]], dtype=np.float32)
    assert photo_crop.focus_from_faces(faces, 400, 800) == photo_crop.FALLBACK_FOCUS


def test_compute_focus_point_no_faces_uses_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        photo_crop,
        "_detect_faces",
        lambda img: np.empty((0, 15), dtype=np.float32),
    )
    photo = _save_jpg(tmp_path, 640, 480)
    assert photo_crop.compute_focus_point(photo) == photo_crop.FALLBACK_FOCUS


def test_compute_focus_point_normalizes_face_center(tmp_path, monkeypatch):
    def fake_detect(img: np.ndarray) -> np.ndarray:
        height, width = img.shape[:2]
        return np.array([[width / 2 - 50, height / 4 - 50, 100.0, 100.0]], dtype=np.float32)

    monkeypatch.setattr(photo_crop, "_detect_faces", fake_detect)
    photo = _save_jpg(tmp_path, 400, 800)
    cx, cy = photo_crop.compute_focus_point(photo)
    assert cx == pytest.approx(0.5)
    assert cy == pytest.approx(185.0 / 800.0)


def test_compute_focus_point_applies_rotation_before_detection(tmp_path, monkeypatch):
    seen_shapes = []

    def fake_detect(img: np.ndarray) -> np.ndarray:
        seen_shapes.append(img.shape[:2])
        return np.empty((0, 15), dtype=np.float32)

    monkeypatch.setattr(photo_crop, "_detect_faces", fake_detect)
    photo = _save_jpg(tmp_path, 800, 400)
    photo_crop.compute_focus_point(photo, rotation=90)
    assert seen_shapes[0] == (800, 400)


def test_compute_focus_points_error_falls_back_per_photo(tmp_path):
    missing = tmp_path / "missing.jpg"
    hints = photo_crop.compute_focus_points({"a.jpg": missing}, {"a.jpg": 90})
    assert hints["a.jpg"] == photo_crop.FALLBACK_FOCUS


def test_compute_focus_points_mixed_results(tmp_path, monkeypatch):
    good = _save_jpg(tmp_path, 400, 400)

    def fake_detect(img: np.ndarray) -> np.ndarray:
        height, width = img.shape[:2]
        return np.array([[width / 2 - 50, height / 2 - 50, 100.0, 100.0]], dtype=np.float32)

    monkeypatch.setattr(photo_crop, "_detect_faces", fake_detect)
    hints = photo_crop.compute_focus_points(
        {"good.jpg": good, "bad.jpg": tmp_path / "nope.jpg"},
        {},
    )
    assert hints["good.jpg"] == (pytest.approx(0.5), pytest.approx(185.0 / 400.0))
    assert hints["bad.jpg"] == photo_crop.FALLBACK_FOCUS
