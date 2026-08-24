import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.db import connect, migrate_project
from app.pipeline.analyze import analyze_job_handler
from app.pipeline.frames_cv import process_photo, process_video
from app.pipeline.scan import scan_project
from app.pipeline.scenes import detect_scenes
from app.pipeline.search import search_scenes

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


class FakeContext:
    def cancelled(self):
        return False

    def check_cancelled(self):
        return None

    async def progress(self, **kwargs):
        return None


def make_cut_clip(tmp_path: Path) -> Path:
    out = tmp_path / "cut.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=2:r=15",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2:r=15",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[out]",
            "-map",
            "[out]",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


@needs_ffmpeg
def test_detect_scenes_finds_hard_cut(tmp_path):
    clip = make_cut_clip(tmp_path)
    scenes = asyncio.run(detect_scenes(clip, 4.0, 15.0))
    assert len(scenes) >= 2
    first_cut = scenes[0][1]
    assert 1.0 <= first_cut <= 3.0


@needs_ffmpeg
def test_process_video_writes_frames_and_metrics(tmp_path):
    clip = make_cut_clip(tmp_path)
    cache = tmp_path / "cache"
    entries = process_video(clip, 0, [(0.0, 2.0), (2.0, 4.0)], cache, file_id=7)
    assert len(entries) == 2
    primary = cache / entries[0]["primary_rel"]
    thumb = cache / entries[0]["thumb_rel"]
    secondary = cache / entries[0]["secondary_rels"][0]
    assert primary.is_file()
    assert thumb.is_file()
    assert secondary.is_file()
    metrics = entries[1]["metrics"]
    for key in ("blur", "brightness", "contrast", "motion", "stability", "dominant_colors"):
        assert key in metrics


def test_process_photo(tmp_path):
    import cv2
    import numpy as np

    media = tmp_path / "media"
    media.mkdir()
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:, :] = (200, 100, 50)
    photo = media / "photo.jpg"
    cv2.imwrite(str(photo), img)

    cache = tmp_path / "cache"
    entry = process_photo(photo, 0, cache, file_id=9)
    assert (cache / entry["primary_rel"]).is_file()
    assert (cache / entry["thumb_rel"]).is_file()
    assert len(entry["metrics"]["dominant_colors"]) == 3


@pytest.mark.slow
def test_analyze_and_search_end_to_end(tmp_path):
    pytest.importorskip("torch")
    clip = make_cut_clip(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    shutil.copy(clip, media / "clip.mp4")

    db_path = tmp_path / "proj" / "cache" / "db.sqlite"

    async def run():
        conn = await connect(db_path)
        try:
            await migrate_project(conn)
            await scan_project(FakeContext(), {"media_path": str(media)}, conn)
        finally:
            await conn.close()

    asyncio.run(run())

    summary = asyncio.run(
        analyze_job_handler(FakeContext(), {"media_path": str(media), "db_path": str(db_path)})
    )
    assert summary["errors"] == [], summary
    assert summary["scenes"] >= 2
    assert summary["visuals"] >= 2
    assert summary["embedded"] >= 2

    async def query(text: str):
        conn = await connect(db_path)
        try:
            return await search_scenes(conn, text)
        finally:
            await conn.close()

    results = asyncio.run(query("blue"))
    assert results, "search returned no results"
    assert results[0]["tags"], "top scene has no tags"
    assert all(r["thumb_rel"] for r in results)
