import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.planner.schemas import EditPlan
from app.renderer.filtergraph import InputMaps, build_filtergraph, build_render_args

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def _simple_maps(clip_count: int) -> InputMaps:
    maps = InputMaps()
    for index in range(clip_count):
        maps.video_of[index] = index
        maps.audio_of[index] = index
    return maps


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
    graph, total = build_filtergraph(
        _sample_plan(), canvas_w=1280, canvas_h=720, maps=_simple_maps(3)
    )
    lines = graph.split(";")
    assert "[cv1][cv2]concat=n=2:v=1:a=0[r1]" in lines
    assert "[ca1][ca2]concat=n=2:v=0:a=1[ar1]" in lines
    assert "[cv0][r1]xfade=transition=fade:duration=0.800:offset=2.200[x1]" in lines
    assert "[ca0][ar1]acrossfade=d=0.800:c1=tri:c2=tri[xa1]" in lines
    assert any(l.startswith("[x1]fade=t=out:st=12.800:d=0.40[vt]") for l in lines)
    assert any(l.endswith("afade=t=out:st=12.800:d=0.40[aout]") for l in lines)
    assert any(l == "[vt]null[vout]" for l in lines)
    kb_chain = next(line for line in lines if line.startswith("[2:v]"))
    assert "zoompan=z='min(1+0.000476*on,1.1000)'" in kb_chain
    for plain_index in ("0", "1"):
        chain = next(line for line in lines if line.startswith(f"[{plain_index}:v]"))
        assert "zoompan" not in chain
        assert "unsharp" in chain
    assert "loudnorm" in graph
    assert total == pytest.approx(13.2)


def test_filtergraph_with_title():
    plan_dict = _sample_plan().model_dump() | {
        "title": {"text": "Trip: 2026", "subtitle": "day one"}
    }
    plan = EditPlan.model_validate(plan_dict)
    graph, total = build_filtergraph(
        plan,
        canvas_w=1280,
        canvas_h=720,
        maps=_simple_maps(3),
        title_textfile=Path("titles/1.txt"),
    )
    assert "drawtext=textfile='titles/1.txt'" in graph
    assert "drawtext=text='Trip\\: 2026':" not in graph
    assert "drawtext=text='day one':" in graph
    assert "enable='between(t,0,2.50)'" in graph
    assert total == pytest.approx(13.2)


def test_build_args_uses_single_frame_input_for_photos():
    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 6.0,
            "clips": [
                {
                    "rel_path": "pic.jpg",
                    "start_sec": 0.0,
                    "end_sec": 3.0,
                    "transition_in": "cut",
                    "ken_burns": {"direction": "zoom_in", "intensity": 0.08},
                },
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
    assert "-loop" not in args
    first_input_index = args.index("-i")
    assert args[first_input_index + 1] == "p.jpg"
    assert any("anullsrc" in a for a in args)
    assert len([a for a in args if a == "-ss"]) == 1
    graph = args[args.index("-filter_complex") + 1]
    image_chain = next(line for line in graph.split(";") if line.startswith("[0:v]"))
    assert "zoompan" in image_chain
    assert "s=256x144" in image_chain
    assert f"scale={256 * 4}:{144 * 4}:force_original_aspect_ratio=increase" in image_chain


@needs_ffmpeg
def test_static_photo_renders_full_duration(tmp_path):
    from PIL import Image

    photo = tmp_path / "p.jpg"
    Image.new("RGB", (320, 180), (10, 120, 200)).save(photo)
    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 3.0,
            "clips": [{"rel_path": "p.jpg", "start_sec": 0.0, "end_sec": 3.0}],
        }
    )
    out = tmp_path / "out.mp4"
    args, total = build_render_args(
        plan,
        {"p.jpg": photo},
        canvas_w=256,
        canvas_h=144,
        encoder="libx264",
        encoder_flags=["-preset", "ultrafast"],
        output_path=out,
        image_rels={"p.jpg"},
        no_audio_rels={"p.jpg"},
    )
    subprocess.run(args, check=True, capture_output=True, cwd=str(tmp_path))
    probe = subprocess.run(
        [
            "ffprobe" if shutil.which("ffprobe") else FFMPEG.replace("ffmpeg", "ffprobe"),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert float(video["duration"]) >= total * 0.9


def test_freeze_tail_adds_tpad_and_audio_pad():
    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 4.5,
            "clips": [
                {
                    "rel_path": "a.mp4",
                    "start_sec": 0.0,
                    "end_sec": 3.0,
                    "freeze_tail_sec": 1.5,
                }
            ],
        }
    )
    maps = _simple_maps(1)
    graph, total = build_filtergraph(plan, canvas_w=256, canvas_h=144, maps=maps)
    assert "tpad=stop_mode=clone:stop_duration=1.500" in graph
    assert "apad=whole_dur=4.500" in graph
    assert total == pytest.approx(4.5)


def test_hdr_engine_selection():
    plan = EditPlan.model_validate(
        {
            "prompt": "t",
            "target_duration_sec": 4.0,
            "clips": [{"rel_path": "a.mp4", "start_sec": 0.0, "end_sec": 3.0}],
        }
    )
    maps = _simple_maps(1)
    maps.hdr_inputs.add(0)
    graph, _total = build_filtergraph(
        plan, canvas_w=256, canvas_h=144, maps=maps, hdr_engine="libplacebo"
    )
    assert "libplacebo=" in graph and "bt.2390" in graph
    graph_zscale, _total = build_filtergraph(
        plan, canvas_w=256, canvas_h=144, maps=maps, hdr_engine="zscale"
    )
    assert "tonemap=hable" in graph_zscale

    args, _total = build_render_args(
        plan,
        {"a.mp4": Path("a.mp4")},
        canvas_w=256,
        canvas_h=144,
        encoder="libx264",
        encoder_flags=[],
        output_path=Path("out.mp4"),
        hdr_rels={"a.mp4"},
    )
    assert "-init_hw_device" in args


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
        no_audio_rels={"src.mp4"},
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

    streams_probe = subprocess.run(
        [
            "ffprobe" if shutil.which("ffprobe") else FFMPEG.replace("ffmpeg", "ffprobe"),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream_types = [
        s.get("codec_type") for s in json.loads(streams_probe.stdout)["streams"]
    ]
    assert "audio" in stream_types
    assert "video" in stream_types
