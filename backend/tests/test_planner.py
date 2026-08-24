import asyncio

import pytest
from pydantic import ValidationError

from app.ai.providers import extract_json
from app.core.db import connect, migrate_project
from app.planner.candidates import select_candidates
from app.planner.schemas import EditPlan


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_and_prose():
    text = 'Here you go:\n```json\n{"a": [1, 2], "b": {"c": "x"}}\n```\nHope that helps!'
    assert extract_json(text) == {"a": [1, 2], "b": {"c": "x"}}


def test_extract_json_rejects_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here at all")


def test_extract_json_salvages_truncated_output():
    truncated = (
        '{"prompt": "hike", "clips": ['
        '{"rel_path": "a.mp4", "start_sec": 1.0, "end_sec": 2.5, "reason": "view"}, '
        '{"rel_path": "b.mp4", "start_sec": 0.0, "end_sec": 3.'
    )
    data = extract_json(truncated)
    assert data["prompt"] == "hike"
    assert len(data["clips"]) == 1
    assert data["clips"][0]["end_sec"] == 2.5


VALID_PLAN = {
    "prompt": "best moments",
    "target_duration_sec": 30.0,
    "seed": 7,
    "clips": [
        {"rel_path": "a.mp4", "start_sec": 0.0, "end_sec": 4.0},
        {
            "rel_path": "a.mp4",
            "start_sec": 10.0,
            "end_sec": 14.0,
            "transition_in": "crossfade",
            "ken_burns": {"direction": "zoom_out", "intensity": 0.1},
            "reason": "high stability",
        },
    ],
    "title": {"text": "Trip Highlights"},
}


def test_edit_plan_roundtrip():
    plan = EditPlan.model_validate(VALID_PLAN)
    assert plan.total_clip_seconds == pytest.approx(8.0)
    assert plan.clips[1].ken_burns is not None
    assert EditPlan.model_validate(plan.model_dump()).clips[0].transition_in == "cut"


def test_edit_plan_rejects_bad_spans():
    bad = {**VALID_PLAN, "clips": [{"rel_path": "a.mp4", "start_sec": 5.0, "end_sec": 5.0}]}
    with pytest.raises(ValidationError):
        EditPlan.model_validate(bad)


def test_edit_plan_schema_is_object():
    schema = EditPlan.model_json_schema()
    assert schema["title"] == "EditPlan"
    assert "clips" in schema["properties"]


def test_edit_plan_sanitizer_salvages_spans():
    raw = {
        "prompt": "p",
        "target_duration_sec": 30.0,
        "clips": [
            {"rel_path": "a.mp4", "start_sec": 8.0, "end_sec": 3.0},
            {"rel_path": "b.mp4", "start_sec": 5.0, "end_sec": 5.0},
            {"rel_path": "c.mp4", "start_sec": "2", "end_sec": "6.5"},
            {"rel_path": None, "start_sec": 1.0, "end_sec": 4.0},
            {"rel_path": "d.mp4", "start_sec": "x", "end_sec": 9.0},
        ],
    }
    plan = EditPlan.model_validate(raw)
    assert [c.rel_path for c in plan.clips] == ["a.mp4", "c.mp4"]
    first = plan.clips[0]
    assert (first.start_sec, first.end_sec) == (3.0, 8.0)
    second = plan.clips[1]
    assert (second.start_sec, second.end_sec) == (2.0, 6.5)


async def _seed_candidates_db(db_path):
    conn = await connect(db_path)
    await migrate_project(conn)
    await conn.execute(
        "INSERT INTO files(rel_path, kind, size_bytes, mtime, duration_sec) VALUES"
        " ('v1.mp4','video',10,1,80.0),('v2.mp4','video',20,2,80.0)"
    )
    for file_id in (1, 2):
        for scene_index in range(5):
            start = scene_index * 10
            await conn.execute(
                "INSERT INTO scenes(file_id, start_sec, end_sec) VALUES (?, ?, ?)",
                (file_id, float(start), float(start + 8)),
            )
            scene_row = await (
                await conn.execute(
                    "SELECT id FROM scenes WHERE file_id=? AND start_sec=?",
                    (file_id, float(start)),
                )
            ).fetchone()
            blur = 50.0 + file_id * 100 + scene_index * 10
            await conn.execute(
                "INSERT INTO scene_frames(scene_id, role, path, blur, brightness, stability)"
                " VALUES (?, 'primary', ?, ?, ?, ?)",
                (scene_row[0], f"frames/{file_id}/s{scene_index}_primary.jpg", blur, 120.0, 0.9),
            )
    await conn.commit()
    return conn


def test_select_candidates_respects_limits(tmp_path):
    async def run():
        conn = await _seed_candidates_db(tmp_path / "db.sqlite")
        try:
            picked = await select_candidates(conn, limit=6, max_per_file=3, min_gap_sec=5.0)
            overlap_free = True
            spans: dict[int, list] = {}
            for item in picked:
                if item["kind"] == "video":
                    span = (item["start_sec"], item["end_sec"])
                    for other in spans.get(item["file_id"], []):
                        if not (
                            span[1] + 5 <= other[0] or span[0] >= other[1] + 5
                        ):
                            overlap_free = False
                    spans.setdefault(item["file_id"], []).append(span)
            counts: dict[int, int] = {}
            for item in picked:
                counts[item["file_id"]] = counts.get(item["file_id"], 0) + 1
            await conn.close()
            return picked, overlap_free, counts
        except BaseException:
            await conn.close()
            raise

    picked, overlap_free, counts = asyncio.run(run())
    assert len(picked) == 6
    assert all(count == 3 for count in counts.values())
    assert overlap_free
    assert all({"scene_id", "rel_path", "score"} <= set(item) for item in picked)


def test_validate_and_fix_plan_drops_and_trims(tmp_path):
    from app.planner.candidates import select_candidates as _sel
    from app.planner.generate import CandidateRef, validate_and_fix_plan
    from app.planner.schemas import EditPlan

    del _sel

    async def run():
        conn = await _seed_candidates_db(tmp_path / "db.sqlite")
        try:
            refs = [
                CandidateRef(
                    scene_id=1,
                    rel_path="v1.mp4",
                    kind="video",
                    start_sec=0.0,
                    end_sec=8.0,
                    score=0.9,
                ),
                CandidateRef(
                    scene_id=6,
                    rel_path="v2.mp4",
                    kind="video",
                    start_sec=0.0,
                    end_sec=8.0,
                    score=0.8,
                ),
            ]
            raw = EditPlan.model_validate(
                {
                    "prompt": "p",
                    "target_duration_sec": 20.0,
                    "clips": [
                        {"rel_path": "ghost.mp4", "start_sec": 0.0, "end_sec": 4.0},
                        {"rel_path": "v1.mp4", "start_sec": 0.0, "end_sec": 30.0},
                        {"rel_path": "v2.mp4", "start_sec": 0.0, "end_sec": 0.5},
                    ],
                }
            )
            return await validate_and_fix_plan(raw, refs, conn)
        finally:
            await conn.close()

    fixed = asyncio.run(run())
    assert [clip.rel_path for clip in fixed.clips] == ["v1.mp4"]
    assert fixed.clips[0].start_sec == 0.0
    assert fixed.clips[0].end_sec == pytest.approx(8.25)


def test_validate_extends_toward_target(tmp_path):
    from app.planner.generate import CandidateRef, validate_and_fix_plan
    from app.planner.schemas import EditPlan

    async def run():
        conn = await _seed_candidates_db(tmp_path / "db.sqlite")
        try:
            refs = [
                CandidateRef(
                    scene_id=1,
                    rel_path="v1.mp4",
                    kind="video",
                    start_sec=0.0,
                    end_sec=40.0,
                    score=0.9,
                ),
            ]
            raw = EditPlan.model_validate(
                {
                    "prompt": "p",
                    "target_duration_sec": 30.0,
                    "clips": [{"rel_path": "v1.mp4", "start_sec": 0.0, "end_sec": 6.0}],
                }
            )
            return await validate_and_fix_plan(raw, refs, conn)
        finally:
            await conn.close()

    fixed = asyncio.run(run())
    total = sum(c.end_sec - c.start_sec for c in fixed.clips)
    assert total >= 30.0 * 0.85
    assert all(c.end_sec <= 40.25 for c in fixed.clips)
