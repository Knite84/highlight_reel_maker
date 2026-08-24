import shutil
import subprocess
from pathlib import Path

import pytest

from app.planner.schemas import EditPlan
from app.renderer.filtergraph import build_filtergraph, build_render_args

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def _sample_plan() -> EditPlan:
    return EditPlan.model_validate(
        {
            "prompt": "test",
            "target_duration_sec": 12.0,
            "seed": 1,
            "clips": [
                {"rel_path": "a.mp4", "start_sec": 0.0, "end_sec": 3.0},
                {
                    "rel_path": "b.mp4",
                    "start_sec": 0.0,
                    "end_sec": 4.0,
                    "transition_in": "crossfade",
                    "transition_duration_sec": 0.8,
                },
                {
                    "rel_path": "a.mp4",
                    "start_sec": 1.0,
                    "end_sec": 8.0,
                    "transition_in": "cut",
                    "ken_burns": {"direction": "zoom_in", "intensity": 0.1},
                },
            ],
        }
    )


def test_filtergraph_structure():
    graph, total = build_filtergraph(_sample_plan(), canvas_w=1280, canvas_h=720)
    lines = graph.split(";")
    assert "[c1][c2]concat=n=2:v=1:a=0[r1]" in lines
    assert "[c0][r1]xfade=transition=fade:duration=0.800:offset=2.200[x1]" in lines
    assert "[x1]fade=t=out:st=12.800:d=0.40[vt]" in lines
    assert lines[-1] == "[vt]null[vout]"
    assert len(lines) == 7
    kb_chain = next(line for line in lines if line.startswith("[2:v]"))
    assert "zoompan=z='1+0.000476*on'" in kb_chain
    for plain_index in ("0", "1"):
        chain = next(line for line in lines if line.startswith(f"[{plain_index}:v]"))
        assert "zoompan" not in chain
    assert total == pytest.approx(13.2)


def test_filtergraph_with_title():
    plan_dict = _sample_plan().model_dump() | {
        "title": {"text": "Trip: 2026", "subtitle": "day one"}
    }
    plan = EditPlan.model_validate(plan_dict)
    graph, total = build_filtergraph(
        plan, canvas_w=1280, canvas_h=720, title_textfile=Path("titles/1.txt")
    )
    assert "drawtext=textfile='titles/1.txt'" in graph
    assert "drawtext=text='Trip\\: 2026':" not in graph
    assert "drawtext=text='day one':" in graph
    assert "enable='between(t,0,2.50)'" in graph
    assert total == pytest.approx(13.2)


def test_build_args_uses_loop_for_photos():
    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 6.0,
            "clips": [
                {"rel_path": "pic.jpg", "start_sec": 0.0, "end_sec": 3.0, "transition_in": "cut"},
                {"rel_path": "clip.mp4", "start_sec": 0.0, "end_sec": 3.0},
            ],
        }
    )
    args, _total = build_render_args(
        plan,
        {"pic.jpg": Path("p.jpg"), "clip.mp4": Path("c.mp4")},
        canvas_w=256,
        canvas_h=144,
        encoder="libx264",
        encoder_flags=[],
        output_path=Path("out.mp4"),
        image_rels={"pic.jpg"},
    )
    assert "-loop" in args
    first_input_index = args.index("-i")
    assert args[first_input_index + 1] == "p.jpg"
    assert len([a for a in args if a == "-ss"]) == 1


@needs_ffmpeg
def test_render_smoke_produces_video(tmp_path):
    clip = tmp_path / "src.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=4:r=15",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    title_file = tmp_path / "title.txt"
    title_file.write_text("Test Reel\nsmoke", encoding="utf-8")

    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 4.0,
            "clips": [
                {
                    "rel_path": "src.mp4",
                    "start_sec": 0.0,
                    "end_sec": 4.0,
                    "ken_burns": {"direction": "zoom_in", "intensity": 0.08},
                }
            ],
            "title": {"text": "Test Reel", "subtitle": "smoke"},
        }
    )
    output_path = tmp_path / "out.mp4"
    args, total = build_render_args(
        plan,
        {"src.mp4": clip},
        canvas_w=256,
        canvas_h=144,
        encoder="libx264",
        encoder_flags=["-preset", "ultrafast", "-crf", "35"],
        output_path=output_path,
        title_textfile=Path(title_file.name),
    )
    subprocess.run(args, check=True, capture_output=True, cwd=str(tmp_path))

    assert output_path.is_file()
    assert output_path.stat().st_size > 1000
    assert total == pytest.approx(4.0)

    probe = subprocess.run(
        [
            "ffprobe" if shutil.which("ffprobe") else FFMPEG.replace("ffmpeg", "ffprobe"),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    duration = float(json.loads(probe.stdout)["format"]["duration"])
    assert duration == pytest.approx(4.0, abs=0.6)
