import asyncio
import json
import shutil
import subprocess

import pytest

from app.core.db import connect, migrate_project
from app.planner.generate import CandidateRef, generate_plan, validate_and_fix_plan
from app.planner.ordering import requested_order
from app.planner.review import render_review
from app.planner.schemas import EditPlan
from app.planner.timing import expected_plan_duration
from app.renderer.filtergraph import build_render_args


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("Tell a moving story about our trip", "chronological"),
        ("Use narrative order, not chronological order", "narrative"),
        ("Use reverse chronological order", "reverse_chronological"),
        ("Open with the finale then return to arrival", "narrative"),
        ("Use flashbacks", "narrative"),
        ("Don't use narrative order", "chronological"),
        ("No flashbacks; keep chronological order", "chronological"),
        ("Use chronological order instead of narrative order", "chronological"),
        ("Use narrative order. Actually use chronological order", "chronological"),
    ],
)
def test_only_explicit_affirmative_brief_directives_change_order(brief, expected):
    assert requested_order(brief)[0] == expected


async def scene_library(path):
    conn = await connect(path)
    await migrate_project(conn)
    # All scenes belong to one source; within-source time is the ordering key.
    await conn.execute(
        "INSERT INTO files(rel_path,kind,size_bytes,mtime,duration_sec,xxhash) "
        "VALUES('video.mp4','video',1,1,20,'known')"
    )
    refs = []
    for index, start in enumerate((0, 6, 12), 1):
        await conn.execute(
            "INSERT INTO scenes(file_id,start_sec,end_sec) VALUES(1,?,?)", (start, start + 4)
        )
        await conn.execute(
            "INSERT INTO scene_frames(scene_id,role,path,blur,brightness,stability) "
            "VALUES(?,'primary','fixture.jpg',100,120,0.9)",
            (index,),
        )
        refs.append(
            CandidateRef(
                scene_id=index,
                file_id=1,
                rel_path="video.mp4",
                kind="video",
                start_sec=start,
                end_sec=start + 4,
                score=0.9,
                source_hash="known",
            )
        )
    await conn.commit()
    return conn, refs


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("A cinematic story", [1, 2, 3]),
        ("Use narrative order", [3, 1, 2]),
        ("Use reverse chronological order", [3, 2, 1]),
    ],
)
def test_ordering_is_applied_to_the_complete_selection_including_filler(tmp_path, brief, expected):
    async def run():
        conn, refs = await scene_library(tmp_path / "db.sqlite")
        try:
            plan = EditPlan(
                prompt=brief,
                target_duration_sec=12,
                clips=[
                    {
                        "scene_id": ref.scene_id,
                        "rel_path": ref.rel_path,
                        "start_sec": ref.start_sec,
                        "end_sec": ref.end_sec,
                        "transition_in": "cut",
                    }
                    for ref in (refs[2], refs[0])
                ],
            )
            fixed = await validate_and_fix_plan(plan, refs, conn)
            assert [clip.scene_id for clip in fixed.clips] == expected
            assert any(clip.duration_filler and clip.scene_id == 2 for clip in fixed.clips)
            assert expected_plan_duration(fixed.clips) == pytest.approx(12, abs=1 / 30)
            review = await render_review(conn, fixed.model_dump_json(), "proxy")
            assert any("additional scene" in warning for warning in review["warnings"])
        finally:
            await conn.close()

    asyncio.run(run())


def test_model_cannot_override_request_ordering_or_forge_adjustments(tmp_path):
    class Provider:
        async def chat_json(self, **kwargs):
            return {
                "sequencing": "narrative",
                "sequencing_evidence": "invented",
                "timing_adjustments": ["model invented repair"],
                "clips": [
                    {"scene_id": 3, "rel_path": "video.mp4", "start_sec": 12, "end_sec": 16},
                    {"scene_id": 1, "rel_path": "video.mp4", "start_sec": 0, "end_sec": 4},
                ],
            }

    async def run():
        conn, _refs = await scene_library(tmp_path / "db.sqlite")
        try:
            plan, _ = await generate_plan(
                Provider(), "fake", conn, prompt="a story", target_duration_sec=8, seed=0
            )
            assert plan.sequencing == "chronological"
            assert plan.sequencing_evidence is None
            assert [clip.scene_id for clip in plan.clips] == [1, 3]
            assert plan.timing_adjustments == []
            assert plan.seed == 0
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind,target", [("photo", 15), ("video", 15), ("photo", 1800)])
def test_sparse_sources_keep_requested_duration_and_disclose_long_holds_or_freezes(
    tmp_path, kind, target
):
    async def run():
        conn, refs = await scene_library(tmp_path / "db.sqlite")
        ref = refs[0]
        if kind == "photo":
            await conn.execute("UPDATE files SET kind='photo' WHERE id=1")
            ref = ref.model_copy(update={"kind": "photo"})
        try:
            plan = EditPlan(
                prompt="sparse",
                target_duration_sec=target,
                clips=[
                    {
                        "scene_id": 1,
                        "rel_path": "video.mp4",
                        "start_sec": 0,
                        "end_sec": 2,
                    }
                ],
            )
            fixed = await validate_and_fix_plan(plan, [ref], conn)
            assert expected_plan_duration(fixed.clips) == pytest.approx(target, abs=1 / 30)
            review = await render_review(conn, fixed.model_dump_json(), "proxy")
            phrase = "still image" if kind == "photo" else "Frozen end frames"
            assert any(phrase in warning for warning in review["warnings"])
            assert review["target_duration_sec"] == target
        finally:
            await conn.close()

    asyncio.run(run())


def test_legacy_review_is_read_only_and_tokens_bind_profile_and_content(tmp_path):
    async def run():
        conn, _ = await scene_library(tmp_path / "db.sqlite")
        saved = json.dumps(
            {
                "prompt": "legacy",
                "target_duration_sec": 10,
                "clips": [
                    {"rel_path": "video.mp4", "start_sec": 0, "end_sec": 4, "freeze_tail_sec": 6}
                ],
            }
        )
        try:
            review = await render_review(conn, saved, "proxy")
            assert review["sequencing"] == "saved_legacy_order"
            assert json.loads(saved)["clips"][0]["freeze_tail_sec"] == 6
            assert review["token"] == (await render_review(conn, saved, "proxy"))["token"]
            assert review["token"] != (await render_review(conn, saved, "final"))["token"]
            assert (
                review["token"]
                != (
                    await render_review(
                        conn, saved.replace('"freeze_tail_sec": 6', '"freeze_tail_sec": 5'), "proxy"
                    )
                )["token"]
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required"
)
@pytest.mark.parametrize(
    "transitions", [["cut", "cut", "crossfade"], ["fade_from_black", "crossfade", "fade_to_black"]]
)
def test_shared_timing_agrees_with_actual_encoder_for_mixed_fractional_spans(tmp_path, transitions):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=256x144:d=8:r=30",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    clips = [
        {
            "rel_path": source.name,
            "start_sec": 0,
            "end_sec": span,
            "transition_in": transition,
            "transition_duration_sec": 0.37,
            "freeze_tail_sec": 0.6 if index == 2 else 0,
        }
        for index, (span, transition) in enumerate(zip([1.23, 1.77, 2.91], transitions))
    ]
    plan = EditPlan(prompt="timing", target_duration_sec=6, clips=clips)
    output = tmp_path / "out.mp4"
    args, estimated = build_render_args(
        plan,
        {source.name: source},
        canvas_w=256,
        canvas_h=144,
        encoder="libx264",
        encoder_flags=["-preset", "ultrafast"],
        output_path=output,
        no_audio_rels={source.name},
    )
    subprocess.run(args, check=True, capture_output=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-of", "json", "-show_format", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert estimated == pytest.approx(expected_plan_duration(plan.clips), abs=0.001)
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(
        estimated, abs=2 / 30
    )
