from pathlib import Path

import cv2
import numpy as np

MAX_SIDE = 512
THUMB_SIDE = 384
JPEG_QUALITY = 85


def _sharpness(img: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    height, width = img.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1.0:
        return img
    return cv2.resize(img, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def _dominant_colors(img: np.ndarray, k: int = 3) -> list[str]:
    small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    data = np.float32(small.reshape(-1, 3))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _ret, labels, centers = cv2.kmeans(data, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(-counts)
    colors = []
    for i in order:
        blue, green, red = (int(c) for c in centers[i])
        colors.append(f"#{red:02x}{green:02x}{blue:02x}")
    return colors


def _apply_rotation(frame: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _grab_frame(cap: cv2.VideoCapture, index: int) -> np.ndarray | None:
    if index < 0:
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
    ok, frame = cap.read()
    return frame if ok else None


def _write_jpeg(cache_dir: Path, rel: str, img: np.ndarray) -> None:
    full_path = cache_dir / rel
    full_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(full_path), img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])


def _frame_metrics(frame: np.ndarray) -> dict:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return {
        "blur": round(_sharpness(frame), 3),
        "brightness": round(float(hsv[:, :, 2].mean()), 3),
        "contrast": round(float(hsv[:, :, 2].std()), 3),
        "dominant_colors": _dominant_colors(frame),
    }


def _motion_metrics(
    cap: cv2.VideoCapture, fps: float, total: int, start: float, end: float
) -> tuple[float, float]:
    samples = 6
    start_idx = max(0, int(start * fps))
    end_idx = min(total - 1, max(start_idx, int(end * fps) - 1))
    if end_idx <= start_idx:
        return 0.0, 1.0
    indices = [int(start_idx + (end_idx - start_idx) * i / (samples - 1)) for i in range(samples)]
    diffs: list[float] = []
    previous = None
    for idx in indices:
        frame = _grab_frame(cap, idx)
        if frame is None:
            continue
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36))
        if previous is not None:
            diffs.append(float(np.abs(gray.astype(np.int16) - previous).mean()))
        previous = gray
    motion_raw = float(np.mean(diffs)) if diffs else 0.0
    motion = round(min(1.0, motion_raw / 60.0), 3)
    stability = round(max(0.0, min(1.0, 1.0 - motion_raw / 30.0)), 3)
    return motion, stability


def process_video(
    path: Path,
    rotation: int,
    scenes: list[tuple[float, float]],
    cache_dir: Path,
    file_id: int,
) -> list[dict]:
    cap = cv2.VideoCapture(str(path))
    results: list[dict] = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for idx, (start, end) in enumerate(scenes):
            entry: dict = {
                "index": idx,
                "start": start,
                "end": end,
                "primary_rel": None,
                "secondary_rels": [],
                "thumb_rel": f"thumbs/{file_id}/s{idx}.jpg",
                "metrics": {},
            }
            candidates: list[tuple[float, np.ndarray]] = []
            for fraction in (0.25, 0.5, 0.75):
                t = start + (end - start) * fraction
                frame = _grab_frame(cap, int(t * fps))
                if frame is not None:
                    frame = _apply_rotation(frame, rotation)
                    candidates.append((_sharpness(frame), frame))
            candidates.sort(key=lambda item: -item[0])
            for role_order, (_score, frame) in enumerate(candidates[:3]):
                role = "primary" if role_order == 0 else f"secondary{role_order}"
                rel = f"frames/{file_id}/s{idx}_{role}.jpg"
                resized = _resize_max(frame, MAX_SIDE)
                _write_jpeg(cache_dir, rel, resized)
                if role_order == 0:
                    entry["primary_rel"] = rel
                    entry["metrics"] = _frame_metrics(resized)
                    _write_jpeg(
                        cache_dir,
                        entry["thumb_rel"],
                        _resize_max(frame, THUMB_SIDE),
                    )
                else:
                    entry["secondary_rels"].append(rel)
            motion, stability = _motion_metrics(cap, fps, total, start, end)
            entry["metrics"]["motion"] = motion
            entry["metrics"]["stability"] = stability
            results.append(entry)
    finally:
        cap.release()
    return results


def process_photo(path: Path, rotation: int, cache_dir: Path, file_id: int) -> dict:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"unsupported or unreadable image format: {path.suffix}")
    img = _apply_rotation(img, rotation)
    resized = _resize_max(img, MAX_SIDE)
    rel = f"frames/{file_id}/s0_primary.jpg"
    thumb_rel = f"thumbs/{file_id}/s0.jpg"
    _write_jpeg(cache_dir, rel, resized)
    _write_jpeg(cache_dir, thumb_rel, _resize_max(img, THUMB_SIDE))
    metrics = _frame_metrics(resized)
    metrics["motion"] = 0.0
    metrics["stability"] = 1.0
    return {
        "index": 0,
        "start": 0.0,
        "end": 0.0,
        "primary_rel": rel,
        "secondary_rels": [],
        "thumb_rel": thumb_rel,
        "metrics": metrics,
    }


async def process_video_async(
    path: Path, rotation: int, scenes: list[tuple[float, float]], cache_dir: Path, file_id: int
) -> list[dict]:
    import asyncio

    return await asyncio.to_thread(process_video, path, rotation, scenes, cache_dir, file_id)


async def process_photo_async(
    path: Path, rotation: int, cache_dir: Path, file_id: int
) -> dict:
    import asyncio

    return await asyncio.to_thread(process_photo, path, rotation, cache_dir, file_id)
