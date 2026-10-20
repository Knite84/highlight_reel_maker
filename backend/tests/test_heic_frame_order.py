import asyncio
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.core.db import connect, migrate_project
from app.pipeline.analyze import _store_scene_visuals
from app.pipeline.describe import describe_pending_scenes
from app.pipeline.frames_cv import process_video


@pytest.mark.parametrize("extension", [".heic", ".HEIF"])
def test_heic_scan_metadata_and_visuals_in_fresh_process(tmp_path, extension):
    from pillow_heif import register_heif_opener

    # Only the parent registers to create the fixture. The child must initialize itself.
    register_heif_opener()
    media = tmp_path / "media"
    media.mkdir()
    source = media / f"first photo{extension}"
    Image.new("RGB", (128, 72), (30, 80, 150)).save(source, format="HEIF")
    code = """
import asyncio, json, sys
from pathlib import Path
from app.core.db import connect, migrate_project
from app.pipeline.metadata import extract_metadata
from app.pipeline.scan import scan_project

class Context:
    def check_cancelled(self): pass
    async def progress(self, **kwargs): pass

async def run():
    source=Path(sys.argv[1])
    cache=Path(sys.argv[2])
    conn=await connect(cache/'db.sqlite')
    try:
        await migrate_project(conn)
        counts=await scan_project(Context(), {'media_path': str(source.parent)}, conn)
        meta=await extract_metadata(source, 'photo')
        from app.pipeline.frames_cv import process_photo_async
        entry=await process_photo_async(source, meta['rotation'], cache, 1)
        assert (cache/entry['primary_rel']).is_file()
        assert (cache/entry['thumb_rel']).is_file()
        print(json.dumps({'photos':counts['photos'],'width':meta['width'],'height':meta['height']}))
    finally:
        await conn.close()
asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), str(tmp_path / "cache")],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == {"photos": 1, "width": 128, "height": 72}


def sampled_scene(tmp_path, monkeypatch, estimated=False):
    from app.pipeline import frames_cv

    class Capture:
        index = 0

        def get(self, key):
            if key == cv2.CAP_PROP_FPS:
                return 10
            if key == cv2.CAP_PROP_FRAME_COUNT:
                return 40
            if key == cv2.CAP_PROP_POS_MSEC:
                return 0 if estimated else self.index * 100
            return 0

        def set(self, key, value):
            self.index = int(value)

        def read(self):
            return True, np.full((40, 80, 3), self.index, dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(frames_cv.cv2, "VideoCapture", lambda path: Capture())
    monkeypatch.setattr(frames_cv, "_sharpness", lambda frame: float(frame[0, 0, 0]))
    monkeypatch.setattr(frames_cv, "_motion_metrics", lambda *args: (0.0, 1.0))
    return process_video(tmp_path / "clip.mp4", 0, [(0.0, 4.0)], tmp_path, 1)[0]


class CapturingProvider:
    paths = None
    prompt = None

    def build_vision_prompt(self, prompt, paths):
        self.prompt = prompt
        self.paths = paths
        return prompt

    async def chat_json(self, **kwargs):
        return {"description": "Scene test"}


@pytest.mark.parametrize("estimated", [False, True])
def test_sharpest_last_thumbnail_but_vision_inputs_chronological(tmp_path, monkeypatch, estimated):
    entry = sampled_scene(tmp_path, monkeypatch, estimated)
    assert int(cv2.imread(str(tmp_path / entry["thumb_rel"]))[0, 0, 0]) == 30

    async def run():
        conn = await connect(tmp_path / "db.sqlite")
        try:
            await migrate_project(conn)
            await conn.execute(
                "INSERT INTO files(rel_path,kind,size_bytes,mtime) VALUES('clip.mp4','video',1,1)"
            )
            await conn.execute("INSERT INTO scenes(file_id,start_sec,end_sec) VALUES(1,0,4)")
            await _store_scene_visuals(conn, 1, entry)
            await conn.commit()
            frames = await (await conn.execute("SELECT * FROM scene_frames ORDER BY id")).fetchall()
            assert [row["timestamp_sec"] for row in frames] == [3.0, 2.0, 1.0]
            source = "frame_index_estimate" if estimated else "decoder_msec"
            assert all(row["timestamp_source"] == source for row in frames)
            provider = CapturingProvider()
            result = await describe_pending_scenes(conn, provider, "fake", tmp_path, file_id=1)
            assert result == {"described": 1, "failed": 0}
            assert [int(cv2.imread(str(path))[0, 0, 0]) for path in provider.paths] == [10, 20, 30]
            assert "chronological order" in provider.prompt
            assert (
                provider.prompt.index("1.000s")
                < provider.prompt.index("2.000s")
                < provider.prompt.index("3.000s")
            )
            assert ("estimated" in provider.prompt) == estimated
        finally:
            await conn.close()

    asyncio.run(run())


def test_legacy_frame_migration_preserves_artifacts_without_inventing_order(tmp_path, monkeypatch):
    entry = sampled_scene(tmp_path, monkeypatch)

    async def run():
        conn = await connect(tmp_path / "db.sqlite")
        try:
            await migrate_project(conn)
            await conn.execute(
                "INSERT INTO files(rel_path,kind,size_bytes,mtime) VALUES('clip.mp4','video',1,1)"
            )
            await conn.execute("INSERT INTO scenes(file_id,start_sec,end_sec) VALUES(1,0,4)")
            await _store_scene_visuals(conn, 1, entry)
            await conn.execute("ALTER TABLE scene_frames DROP COLUMN timestamp_sec")
            await conn.execute("ALTER TABLE scene_frames DROP COLUMN timestamp_source")
            await conn.commit()
            await migrate_project(conn)
            rows = await (await conn.execute("SELECT * FROM scene_frames")).fetchall()
            assert len(rows) == 3
            assert all(row["timestamp_sec"] is None for row in rows)
            assert all((tmp_path / row["path"]).is_file() for row in rows)
            provider = CapturingProvider()
            await describe_pending_scenes(conn, provider, "fake", tmp_path, file_id=1)
            assert len(provider.paths) == 1
            assert int(cv2.imread(str(provider.paths[0]))[0, 0, 0]) == 30
            assert "timestamp is unknown" in provider.prompt
            assert "chronological order" not in provider.prompt
        finally:
            await conn.close()

    asyncio.run(run())
