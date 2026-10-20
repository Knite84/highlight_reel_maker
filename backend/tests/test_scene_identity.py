import asyncio
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app.core.db import connect, migrate_project
from app.pipeline.metadata import xxh3_file_sync
from app.planner.generate import PlannerInputError, expected_plan_duration, generate_plan
from app.planner.identity import validate_plan_identity
from app.planner.schemas import EditPlan


async def seed(db, *, source_hash="fixture-hash"):
    conn = await connect(db)
    await migrate_project(conn)
    await conn.execute(
        "INSERT INTO files(rel_path,kind,size_bytes,mtime,duration_sec,source_revision,analysis_revision,xxhash) "
        "VALUES('clip.mp4','video',1,1,10,3,3,?)",
        (source_hash,),
    )
    for start, end, blur in [(0, 4, 1000), (6, 10, 100)]:
        inserted = await conn.execute(
            "INSERT INTO scenes(file_id,start_sec,end_sec) VALUES(1,?,?) RETURNING id", (start, end)
        )
        scene = (await inserted.fetchone())[0]
        await conn.execute(
            "INSERT INTO scene_frames(scene_id,role,path,blur,brightness,stability) "
            "VALUES(?,'primary','frame.jpg',?,120,0.9)",
            (scene, blur),
        )
    await conn.commit()
    return conn


class Provider:
    def __init__(self, scene_id=2):
        self.scene_id = scene_id

    async def chat_json(self, **kwargs):
        assert "scene_id=2" in kwargs["user_parts"]
        assert "scene_id=1" in kwargs["user_parts"]
        return {
            "prompt": "later",
            "target_duration_sec": 4,
            "clips": [
                {
                    "rel_path": "clip.mp4",
                    "start_sec": 6,
                    "end_sec": 10,
                    "scene_id": self.scene_id,
                    "source_file_id": 999,
                    "source_revision": 999,
                    "source_hash": "model must not own these",
                }
            ],
        }


@pytest.mark.parametrize("scene_id", [2, None])
def test_later_scene_survives_quality_ranking_and_fitting(tmp_path, scene_id):
    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            plan, refs = await generate_plan(
                Provider(scene_id), "fake", conn, prompt="later", target_duration_sec=4, seed=7
            )
            assert (
                refs[0].scene_id == 1
            )  # Earlier scene has higher quality; SQL ranking is not identity.
            assert plan.schema_version == "v2"
            assert len(plan.clips) == 1
            clip = plan.clips[0]
            assert (clip.scene_id, clip.start_sec, clip.end_sec) == (2, 6, 10)
            assert (clip.source_file_id, clip.source_revision, clip.source_hash) == (
                1,
                3,
                "fixture-hash",
            )
            assert expected_plan_duration(plan.clips) == 4
            await validate_plan_identity(conn, plan)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("scene_id", [1, 999])
def test_wrong_or_unknown_scene_cannot_select_later_footage(tmp_path, scene_id):
    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            with pytest.raises(PlannerInputError):
                await generate_plan(
                    Provider(scene_id), "fake", conn, prompt="later", target_duration_sec=4, seed=7
                )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["delete_scene", "source_revision", "restored_revision", "hash", "bounds", "path"]
)
def test_v2_rejects_stale_source_or_scene(tmp_path, mutation):
    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            plan, _ = await generate_plan(
                Provider(), "fake", conn, prompt="later", target_duration_sec=4, seed=7
            )
            sql = {
                "delete_scene": "DELETE FROM scenes WHERE id=2",
                "source_revision": "UPDATE files SET source_revision=4 WHERE id=1",
                "restored_revision": "UPDATE files SET source_revision=2,analysis_revision=2 WHERE id=1",
                "hash": "UPDATE files SET xxhash='changed' WHERE id=1",
                "bounds": "UPDATE scenes SET end_sec=6 WHERE id=2",
                "path": "UPDATE files SET rel_path='replacement.mp4' WHERE id=1",
            }[mutation]
            await conn.execute(sql)
            with pytest.raises(ValueError, match="stale or ambiguous"):
                await validate_plan_identity(conn, plan)
        finally:
            await conn.close()

    asyncio.run(run())


def test_byte_identical_inventory_revision_keeps_plan_valid(tmp_path):
    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            plan, _ = await generate_plan(
                Provider(), "fake", conn, prompt="later", target_duration_sec=4, seed=7
            )
            await conn.execute("UPDATE files SET source_revision=4,analysis_revision=4 WHERE id=1")
            await validate_plan_identity(conn, plan)
        finally:
            await conn.close()

    asyncio.run(run())


def test_legacy_plan_is_readable_but_ambiguous_scene_is_rejected(tmp_path):
    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            plan = EditPlan(
                prompt="legacy",
                target_duration_sec=2,
                clips=[{"rel_path": "clip.mp4", "start_sec": 7, "end_sec": 9}],
            )
            before = plan.model_dump_json()
            await validate_plan_identity(conn, plan)
            assert plan.schema_version == "v1"
            assert plan.model_dump_json() == before  # Never silently migrate the stored plan.
            await conn.execute("INSERT INTO scenes(file_id,start_sec,end_sec) VALUES(1,6.5,9.5)")
            with pytest.raises(ValueError, match="ambiguous"):
                await validate_plan_identity(conn, plan)
        finally:
            await conn.close()

    asyncio.run(run())


def test_v2_requires_complete_identity():
    with pytest.raises(ValidationError, match="v2 clips require"):
        EditPlan(
            schema_version="v2",
            prompt="incomplete",
            target_duration_sec=4,
            clips=[{"rel_path": "clip.mp4", "start_sec": 6, "end_sec": 8}],
        )


def test_new_plan_requires_analyzed_source_hash(tmp_path):
    async def run():
        conn = await seed(tmp_path / "db.sqlite", source_hash=None)
        try:
            with pytest.raises(PlannerInputError, match="run Analyze"):
                await generate_plan(
                    Provider(), "fake", conn, prompt="later", target_duration_sec=4, seed=7
                )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("filler", [False, True])
def test_same_file_scenes_are_ordered_by_time_after_selection_and_filler(tmp_path, filler):
    class BothScenes(Provider):
        async def chat_json(self, **kwargs):
            data = await super().chat_json(**kwargs)
            if not filler:
                data["clips"].append(
                    {"scene_id": 1, "rel_path": "clip.mp4", "start_sec": 0, "end_sec": 4}
                )
            return data

    async def run():
        conn = await seed(tmp_path / "db.sqlite")
        try:
            plan, _ = await generate_plan(
                BothScenes(), "fake", conn, prompt="both scenes", target_duration_sec=8, seed=7
            )
            assert [clip.scene_id for clip in plan.clips] == [1, 2]
            for clip in plan.clips:
                if clip.scene_id == 1:
                    assert 0 <= clip.start_sec < clip.end_sec <= 4
                else:
                    assert 6 <= clip.start_sec < clip.end_sec <= 10
            await validate_plan_identity(conn, plan)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_render_uses_selected_later_scene_of_same_file(tmp_path, monkeypatch):
    from app.renderer import render

    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=128x72:d=6",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=128x72:d=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )

    async def encoder(profile):
        return "libx264", ["-preset", "ultrafast"]

    monkeypatch.setattr(render, "resolve_encoder", encoder)
    monkeypatch.setattr(render, "canvas_for", lambda profile: (128, 72))

    class Context:
        def check_cancelled(self):
            pass

        async def progress(self, **kwargs):
            pass

    async def run():
        db = tmp_path / "db.sqlite"
        conn = await seed(db, source_hash=xxh3_file_sync(source))
        try:
            plan, _ = await generate_plan(
                Provider(), "fake", conn, prompt="later", target_duration_sec=4, seed=7
            )
            await conn.execute(
                "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('later',4,?)",
                (plan.model_dump_json(),),
            )
            await conn.commit()
        finally:
            await conn.close()
        return await render.render_job_handler(
            Context(),
            {
                "db_path": str(db),
                "media_root": str(tmp_path),
                "export_dir": str(tmp_path / "exports"),
                "edit_id": 1,
                "profile": "proxy",
            },
        )

    result = asyncio.run(run())
    encoded = subprocess.run(
        [
            "ffmpeg",
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
    ).stdout
    frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
    blue, green, red = frame.mean(axis=(0, 1))
    assert blue > 180 and red < 60 and green < 60
