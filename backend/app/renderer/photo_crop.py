from __future__ import annotations

import logging
from pathlib import Path

import cv2
import httpx
import numpy as np

from ..core.config import get_settings
from ..pipeline.frames_cv import _apply_rotation, _load_image_bgr

logger = logging.getLogger(__name__)

YUNET_MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
DETECT_MAX_SIDE = 1024
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3
TOP_K = 8
FALLBACK_FOCUS = (0.5, 0.4)
DOWNLOAD_TIMEOUT_SEC = 60.0
DEFAULT_TARGET_ASPECT = 16 / 9
HEAD_PAD_TOP = 0.6
HEAD_PAD_BOTTOM = 0.3
HEAD_PAD_SIDE = 0.35
SAFETY_MARGIN_FRAC = 0.05


def model_path() -> Path:
    return get_settings().data_root / "models" / YUNET_MODEL_FILENAME


def _ensure_model(path: Path | None = None) -> Path:
    target = path or model_path()
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    try:
        with (
            httpx.Client(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client,
            client.stream("GET", YUNET_MODEL_URL) as resp,
        ):
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return target


def _create_detector(width: int, height: int) -> object | None:
    try:
        model_file = _ensure_model()
    except Exception as exc:
        logger.warning("face model unavailable (%s); using heuristic crop", exc)
        return None
    try:
        detector = cv2.FaceDetectorYN.create(
            str(model_file),
            "",
            (width, height),
            SCORE_THRESHOLD,
            NMS_THRESHOLD,
            TOP_K,
        )
    except cv2.error as exc:
        logger.warning("failed to load face detector (%s); using heuristic crop", exc)
        return None
    return detector


def _detect_faces(img: np.ndarray) -> np.ndarray:
    height, width = img.shape[:2]
    detector = _create_detector(width, height)
    if detector is None:
        return np.empty((0, 15), dtype=np.float32)
    _, faces = detector.detect(img)
    if faces is None:
        return np.empty((0, 15), dtype=np.float32)
    return faces


def _head_boxes(faces: np.ndarray) -> np.ndarray:
    padded = faces[:, :4].astype(np.float64).copy()
    widths, heights = padded[:, 2], padded[:, 3]
    padded[:, 0] -= widths * HEAD_PAD_SIDE
    padded[:, 1] -= heights * HEAD_PAD_TOP
    padded[:, 2] += widths * HEAD_PAD_SIDE * 2
    padded[:, 3] += heights * (HEAD_PAD_TOP + HEAD_PAD_BOTTOM)
    return padded


def focus_from_faces(
    faces: np.ndarray,
    image_w: int,
    image_h: int,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
) -> tuple[float, float]:
    heads = _head_boxes(faces)
    areas = faces[:, 2] * faces[:, 3]
    total = float(areas.sum())
    if total <= 0.0:
        return FALLBACK_FOCUS
    centers_x = heads[:, 0] + heads[:, 2] / 2.0
    centers_y = heads[:, 1] + heads[:, 3] / 2.0
    cx = float((centers_x * areas).sum() / total)
    cy = float((centers_y * areas).sum() / total)

    win_h = image_w / target_aspect
    margin = SAFETY_MARGIN_FRAC * win_h
    largest = heads[int(np.argmax(areas))]
    req_top = largest[1] - margin
    req_bottom = largest[1] + largest[3] + margin
    lo = req_bottom - win_h / 2.0
    hi = req_top + win_h / 2.0
    if lo <= hi:
        cy = min(max(cy, lo), hi)
    else:
        cy = (req_top + req_bottom) / 2.0
    cy = min(max(cy, win_h / 2.0), image_h - win_h / 2.0)
    cx = min(max(cx, 0.0), float(image_w))
    return cx / image_w, cy / image_h


def compute_focus_point(
    path: Path,
    rotation: int = 0,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
) -> tuple[float, float]:
    img, already_transposed = _load_image_bgr(path)
    if not already_transposed:
        img = _apply_rotation(img, rotation)
    scale = DETECT_MAX_SIDE / max(img.shape[1], img.shape[0])
    if scale < 1.0:
        new_w = max(1, round(img.shape[1] * scale))
        new_h = max(1, round(img.shape[0] * scale))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    height, width = img.shape[:2]
    faces = _detect_faces(img)
    if len(faces) == 0:
        return FALLBACK_FOCUS
    cx, cy = focus_from_faces(faces, width, height, target_aspect)
    return min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0)


def compute_focus_points(
    sources: dict[str, Path],
    rotations: dict[str, int],
) -> dict[str, tuple[float, float]]:
    hints: dict[str, tuple[float, float]] = {}
    for rel_path, source in sources.items():
        try:
            hints[rel_path] = compute_focus_point(source, rotations.get(rel_path, 0))
        except Exception as exc:
            logger.warning("focus detection failed for %s (%s); using heuristic", rel_path, exc)
            hints[rel_path] = FALLBACK_FOCUS
    return hints
