import asyncio
from pathlib import Path

from scenedetect import ContentDetector, detect

MIN_SCENE_SEC = 0.4


def detect_scenes_sync(path: Path, threshold: float, min_scene_len: int) -> list[tuple[float, float]]:
    scene_list = detect(str(path), ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    scenes: list[tuple[float, float]] = []
    for start, end in scene_list:
        start_s = round(start.seconds, 3)
        end_s = round(end.seconds, 3)
        if end_s - start_s >= MIN_SCENE_SEC:
            scenes.append((start_s, end_s))
    return scenes


async def detect_scenes(
    path: Path, duration_sec: float | None, fps: float | None
) -> list[tuple[float, float]]:
    min_scene_len = max(8, int((fps or 30.0) * 0.5))
    try:
        scenes = await asyncio.to_thread(detect_scenes_sync, path, 27.0, min_scene_len)
    except Exception as exc:
        raise RuntimeError(f"scene detection failed: {exc}") from exc
    if not scenes:
        end = duration_sec if duration_sec and duration_sec > MIN_SCENE_SEC else MIN_SCENE_SEC
        scenes = [(0.0, round(end, 3))]
    return scenes
