import asyncio
import os
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from PIL import Image

from app.core.db import connect, migrate_project
from app.pipeline.frames_cv import _load_image_bgr, process_photo, process_video
from app.pipeline.images import display_image_metadata_sync, prepare_display_photo
from app.pipeline.metadata import probe_media_sync, xxh3_file_sync
from app.renderer import render

COLORS = np.array([[240, 20, 20], [20, 200, 20], [20, 20, 240], [240, 220, 20]])
EXPECTED = {
    1: [0, 1, 2, 3],
    2: [1, 0, 3, 2],
    3: [3, 2, 1, 0],
    4: [2, 3, 0, 1],
    5: [0, 2, 1, 3],
    6: [2, 0, 3, 1],
    7: [3, 1, 2, 0],
    8: [1, 3, 0, 2],
}
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def quadrant_image():
    image = Image.new("RGB", (160, 96))
    for box, color in zip(
        [(0, 0, 80, 48), (80, 0, 160, 48), (0, 48, 80, 96), (80, 48, 160, 96)], COLORS
    ):
        image.paste(tuple(int(c) for c in color), box)
    return image


def corner_ids(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return [
        int(np.argmin(np.linalg.norm(COLORS - rgb[y, x], axis=1)))
        for y, x in [
            (h // 4, w // 4),
            (h // 4, 3 * w // 4),
            (3 * h // 4, w // 4),
            (3 * h // 4, 3 * w // 4),
        ]
    ]


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_pixels_metadata_and_thumbnail_agree(tmp_path, monkeypatch, orientation):
    photo = tmp_path / f"exif-{orientation}.jpg"
    image = quadrant_image()
    exif = image.getexif()
    exif[274] = orientation
    image.save(photo, exif=exif, quality=100, subsampling=0)
    pixels, oriented = _load_image_bgr(photo)
    assert oriented
    assert corner_ids(pixels) == EXPECTED[orientation]
    from app.renderer import photo_crop

    seen = []

    def detect(display_pixels):
        seen.append(corner_ids(display_pixels))
        return np.empty((0, 15), dtype=np.float32)

    monkeypatch.setattr(photo_crop, "_detect_faces", detect)
    photo_crop.compute_focus_point(photo, rotation=90)
    assert seen == [EXPECTED[orientation]]
    assert display_image_metadata_sync(photo)["size"] == (
        (96, 160) if orientation >= 5 else (160, 96)
    )
    # Supplying old degree-only metadata must not apply orientation a second time.
    entry = process_photo(photo, 90, tmp_path, 1)
    assert corner_ids(cv2.imread(str(tmp_path / entry["thumb_rel"]))) == EXPECTED[orientation]
    normalized = tmp_path / "normalized.png"
    prepare_display_photo(photo, normalized)
    assert corner_ids(cv2.imread(str(normalized))) == EXPECTED[orientation]
    with Image.open(normalized) as output:
        assert output.getexif().get(274, 1) == 1


class Context:
    def check_cancelled(self):
        pass

    async def progress(self, **kwargs):
        pass


async def render_fixture(tmp_path, source, kind, monkeypatch):
    async def encoder(profile):
        return "libx264", ["-preset", "ultrafast", "-crf", "18"]

    monkeypatch.setattr(render, "resolve_encoder", encoder)
    monkeypatch.setattr(render, "canvas_for", lambda profile: (128, 72))
    monkeypatch.setattr(render, "compute_focus_points", lambda *args: {})
    conn = await connect(tmp_path / "db.sqlite")
    try:
        await migrate_project(conn)
        await conn.execute(
            "INSERT INTO files(rel_path,kind,size_bytes,mtime,xxhash,rotation) VALUES(?,?,?,?,?,90)",
            (
                source.name,
                kind,
                source.stat().st_size,
                source.stat().st_mtime,
                xxh3_file_sync(source),
            ),
        )
        from app.planner.schemas import EditPlan

        plan = EditPlan(
            prompt="orientation",
            target_duration_sec=4,
            clips=[{"rel_path": source.name, "start_sec": 0, "end_sec": 4}],
        )
        await conn.execute(
            "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('orientation',4,?)",
            (plan.model_dump_json(),),
        )
        await conn.commit()
    finally:
        await conn.close()
    result = await render.render_job_handler(
        Context(),
        {
            "db_path": str(tmp_path / "db.sqlite"),
            "media_root": str(tmp_path),
            "export_dir": os.path.relpath(tmp_path / "exports"),
            "edit_id": 1,
            "profile": "proxy",
        },
    )
    decoded = await asyncio.to_thread(
        subprocess.run,
        [
            FFMPEG,
            "-v",
            "error",
            "-i",
            result["output"],
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    frame = decoded.stdout
    assert not list((tmp_path / "exports").glob(".*.photo-*.png"))
    return cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)


@needs_ffmpeg
@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_render_handler_matches_display_pixels(tmp_path, monkeypatch, orientation):
    photo = tmp_path / "oriented.jpg"
    image = quadrant_image()
    exif = image.getexif()
    exif[274] = orientation
    image.save(photo, exif=exif, quality=100, subsampling=0)
    output = asyncio.run(render_fixture(tmp_path, photo, "photo", monkeypatch))
    assert corner_ids(output) == EXPECTED[orientation]


@needs_ffmpeg
@pytest.mark.parametrize("orientation", [2, 6])
def test_heic_render_uses_same_display_pixels_as_analysis(tmp_path, monkeypatch, orientation):
    from pillow_heif import register_heif_opener

    register_heif_opener()
    photo = tmp_path / "phone.heic"
    image = quadrant_image()
    exif = image.getexif()
    exif[274] = orientation
    image.info["exif"] = exif.tobytes()
    image.save(photo, format="HEIF", exif=exif.tobytes())
    pixels, _ = _load_image_bgr(photo)
    assert corner_ids(pixels) == EXPECTED[orientation]
    output = asyncio.run(render_fixture(tmp_path, photo, "photo", monkeypatch))
    assert corner_ids(output) == corner_ids(pixels)


@needs_ffmpeg
@pytest.mark.parametrize("angle,orientation", [(90, 8), (180, 3), (270, 6)])
def test_rotated_phone_video_analysis_and_render_agree(tmp_path, monkeypatch, angle, orientation):
    image = tmp_path / "coded.png"
    quadrant_image().save(image)
    coded = tmp_path / "coded.mp4"
    source = tmp_path / "phone.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(image),
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(coded),
        ],
        check=True,
    )
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-display_rotation",
            str(angle),
            "-i",
            str(coded),
            "-c",
            "copy",
            str(source),
        ],
        check=True,
    )
    meta = probe_media_sync(source)
    assert meta["rotation"] == (-angle) % 360
    entry = process_video(source, meta["rotation"], [(0, 0.5)], tmp_path / "cache", 1)[0]
    assert (
        corner_ids(cv2.imread(str(tmp_path / "cache" / entry["primary_rel"])))
        == EXPECTED[orientation]
    )
    output = asyncio.run(render_fixture(tmp_path, source, "video", monkeypatch))
    assert corner_ids(output) == EXPECTED[orientation]
