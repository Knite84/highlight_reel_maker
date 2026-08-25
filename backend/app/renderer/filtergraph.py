from pathlib import Path

from ..planner.schemas import EditPlan

FPS = 30
TAIL_FADE_SEC = 0.4
ZSCALE_HDR = "zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:r=tv"
LIBPLACEBO_HDR = (
    "libplacebo=colorspace=bt709:color_primaries=bt709:color_trc=bt709:"
    "range=limited:tonemapping=bt.2390:peak_detect=false"
)


def _hdr_filter(engine: str) -> str:
    return LIBPLACEBO_HDR if engine == "libplacebo" else ZSCALE_HDR


def _kb_filter(
    direction: str,
    intensity: float,
    frames: int,
    out_res: str,
    *,
    duration_frames: int | None = None,
) -> str:
    total = max(duration_frames or 1, 1)
    zoom_span = max(0.02, min(intensity, 0.5))
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    base = f"d={total}:s={out_res}:fps={FPS}"
    if direction == "zoom_in":
        rate = zoom_span / max(frames, 2)
        return f"zoompan=z='min(1+{rate:.6f}*on,{1 + zoom_span:.4f})':{center}:{base}"
    if direction == "zoom_out":
        rate = zoom_span / max(frames, 2)
        start_zoom = 1.0 + zoom_span
        return f"zoompan=z='max({start_zoom:.4f}-{rate:.6f}*on,1.0)':{center}:{base}"
    fixed_zoom = 1.0 + max(0.04, zoom_span)
    progress = f"(on/{max(frames, 2)})"
    if direction == "pan_left":
        return f"zoompan=z='{fixed_zoom:.4f}':x='(iw-iw/zoom)*{progress}':y='ih/2-(ih/zoom/2)':{base}"
    if direction == "pan_right":
        return f"zoompan=z='{fixed_zoom:.4f}':x='(iw-iw/zoom)*(1-{progress})':y='ih/2-(ih/zoom/2)':{base}"
    if direction == "pan_up":
        return f"zoompan=z='{fixed_zoom:.4f}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*{progress}':{base}"
    if direction == "pan_down":
        return f"zoompan=z='{fixed_zoom:.4f}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-{progress})':{base}"
    raise ValueError(f"unsupported ken_burns direction: {direction}")


def _split_runs(plan: EditPlan) -> list[list[int]]:
    runs: list[list[int]] = []
    for index, clip in enumerate(plan.clips):
        starts_new_run = index > 0 and clip.transition_in != "cut"
        if not runs or starts_new_run:
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )


def _title_filters(plan: EditPlan, title_textfile: Path, total: float, canvas_h: int) -> list[str]:
    title = plan.title
    assert title is not None
    filters = [
        (
            f"drawtext=textfile='{title_textfile.as_posix()}':"
            f"fontsize={int(canvas_h * 0.075)}:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=(w-text_w)/2:y=(h-text_h)/2-40:"
            f"enable='between(t,0,{min(title.duration_sec, total):.2f})'"
        )
    ]
    if title.subtitle:
        filters.append(
            
                f"drawtext=text='{_escape_drawtext(title.subtitle)}':"
                f"fontsize={int(canvas_h * 0.045)}:fontcolor=white:borderw=2:bordercolor=black:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+30:"
                f"enable='between(t,0,{min(title.duration_sec + 1.0, total):.2f})'"
            
        )
    return filters


class InputMaps:
    def __init__(self) -> None:
        self.video_of: dict[int, int] = {}
        self.audio_of: dict[int, int] = {}
        self.image_inputs: set[int] = set()
        self.hdr_inputs: set[int] = set()


def _transpose_filters(rotation: int) -> list[str]:
    if rotation == 90:
        return ["transpose=1"]
    if rotation == 180:
        return ["transpose=1", "transpose=1"]
    if rotation == 270:
        return ["transpose=2"]
    return []


def _clip_video_chain(
    clip_index: int,
    input_index: int,
    span_seconds: float,
    ken_burns: dict | None,
    canvas_w: int,
    canvas_h: int,
    fade_in_sec: float | None,
    maps: InputMaps,
    hdr_engine: str = "libplacebo",
    is_image: bool = False,
    freeze_tail_sec: float = 0.0,
    rotation: int = 0,
    focus: tuple[float, float] | None = None,
) -> str:
    label = f"cv{input_index}"
    out_res = f"{canvas_w}x{canvas_h}"
    filters: list[str] = []
    if input_index in maps.hdr_inputs and not is_image:
        filters.append(_hdr_filter(hdr_engine))
    if is_image and rotation:
        filters.extend(_transpose_filters(rotation))
    frames = max(int(span_seconds * FPS), 2)
    if is_image:
        big_w, big_h = canvas_w * 4, canvas_h * 4
        filters.append(f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase")
        if focus is not None:
            cx, cy = max(0.0, min(1.0, focus[0])), max(0.0, min(1.0, focus[1]))
            filters.append(
                f"crop={big_w}:{big_h}:x='clip({cx:.4f}*iw-ow/2,0,iw-ow)'"
                f":y='clip({cy:.4f}*ih-oh/2,0,ih-oh)'"
            )
        else:
            filters.append(f"crop={big_w}:{big_h}")
        if ken_burns:
            filters.append(
                _kb_filter(
                    str(ken_burns.get("direction", "zoom_in")),
                    float(ken_burns.get("intensity", 0.08)),
                    frames,
                    out_res,
                    duration_frames=frames,
                )
            )
        else:
            filters.append(
                f"zoompan=z='1':x='0':y='0':d={frames}:s={out_res}:fps={FPS}"
            )
    else:
        filters += [
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase",
            f"crop={canvas_w}:{canvas_h}",
            f"fps={FPS}",
        ]
        if ken_burns:
            filters.append(
                _kb_filter(
                    str(ken_burns.get("direction", "zoom_in")),
                    float(ken_burns.get("intensity", 0.08)),
                    frames,
                    out_res,
                    duration_frames=1,
                )
            )
    filters.append("settb=AVTB")
    filters.append("setsar=1")
    filters.append("unsharp=5:5:0.3:5:5:0.0")
    if fade_in_sec:
        filters.append(f"fade=t=in:st=0:d={fade_in_sec:.2f}")
    if not is_image and freeze_tail_sec > 0:
        filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_tail_sec:.3f}")
    filters.append("format=yuv420p")
    return f"[{input_index}:v]{','.join(filters)}[{label}]"


def _clip_audio_chain(input_index: int, pad_to_sec: float | None = None) -> str:
    chain = f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo"
    if pad_to_sec is not None:
        chain += f",apad=whole_dur={pad_to_sec:.3f}"
    return f"{chain}[ca{input_index}]"


def build_filtergraph(
    plan: EditPlan,
    *,
    canvas_w: int,
    canvas_h: int,
    maps: InputMaps,
    title_textfile: Path | None = None,
    hdr_engine: str = "libplacebo",
    image_rels: frozenset[str] | set[str] = frozenset(),
    photo_rotations: dict[str, int] | None = None,
    photo_focus: dict[str, tuple[float, float]] | None = None,
) -> tuple[str, float]:
    clips = plan.clips
    chains: list[str] = []
    runs = _split_runs(plan)

    run_labels: list[str] = []
    run_audio_labels: list[str] = []
    run_durations: list[float] = []

    for run_index, indexes in enumerate(runs):
        labels: list[str] = []
        audio_labels: list[str] = []
        duration = 0.0
        for position, clip_index in enumerate(indexes):
            clip = clips[clip_index]
            span = clip.end_sec - clip.start_sec
            fade_in = (
                clip.transition_duration_sec
                if run_index == 0 and position == 0 and clip.transition_in == "fade_from_black"
                else None
            )
            video_input = maps.video_of[clip_index]
            audio_input = maps.audio_of[clip_index]
            chains.append(
                _clip_video_chain(
                    clip_index,
                    video_input,
                    span,
                    clip.ken_burns.model_dump() if clip.ken_burns else None,
                    canvas_w,
                    canvas_h,
                    fade_in,
                    maps,
                    hdr_engine,
                    is_image=clip.rel_path in image_rels,
                    freeze_tail_sec=clip.freeze_tail_sec,
                    rotation=(photo_rotations or {}).get(clip.rel_path, 0),
                    focus=(photo_focus or {}).get(clip.rel_path),
                )
            )
            pad_to = (
                span + clip.freeze_tail_sec if clip.freeze_tail_sec > 0 else None
            )
            chains.append(_clip_audio_chain(audio_input, pad_to_sec=pad_to))
            labels.append(f"cv{video_input}")
            audio_labels.append(f"ca{audio_input}")
            duration += span + clip.freeze_tail_sec
        run_label = labels[0]
        run_audio_label = audio_labels[0]
        if len(labels) > 1:
            run_label = f"r{run_index}"
            run_audio_label = f"ar{run_index}"
            v_inputs = "".join(f"[{label}]" for label in labels)
            a_inputs = "".join(f"[{label}]" for label in audio_labels)
            chains.append(f"{v_inputs}concat=n={len(labels)}:v=1:a=0[{run_label}]")
            chains.append(f"{a_inputs}concat=n={len(labels)}:v=0:a=1[{run_audio_label}]")
        run_labels.append(run_label)
        run_audio_labels.append(run_audio_label)
        run_durations.append(round(duration, 4))

    result_label = run_labels[0]
    result_audio = run_audio_labels[0]
    total = run_durations[0]
    for k in range(1, len(run_labels)):
        boundary_clip = plan.clips[runs[k][0]]
        name = "fade" if boundary_clip.transition_in == "crossfade" else "fadeblack"
        duration = min(
            boundary_clip.transition_duration_sec, total * 0.5, run_durations[k] * 0.5
        )
        offset = round(total - duration, 4)
        merged_label = f"x{k}"
        chains.append(
            f"[{result_label}][{run_labels[k]}]"
            f"xfade=transition={name}:duration={duration:.3f}:offset={offset:.3f}[{merged_label}]"
        )
        merged_audio = f"xa{k}"
        chains.append(
            f"[{result_audio}][{run_audio_labels[k]}]"
            f"acrossfade=d={duration:.3f}:c1=tri:c2=tri[{merged_audio}]"
        )
        result_label = merged_label
        result_audio = merged_audio
        total = offset + run_durations[k]

    if total > TAIL_FADE_SEC * 2:
        chains.append(
            f"[{result_label}]fade=t=out:st={total - TAIL_FADE_SEC:.3f}:d={TAIL_FADE_SEC:.2f}[vt]"
        )
        result_label = "vt"

    if plan.title is not None and title_textfile is not None:
        title_filters = _title_filters(plan, title_textfile, total, canvas_h)
        chains.append(f"[{result_label}]{','.join(title_filters)}[vout]")
    elif result_label != "vout":
        chains.append(f"[{result_label}]null[vout]")

    chains.append(
        f"[{result_audio}]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
        f"afade=t=out:st={max(total - TAIL_FADE_SEC, 0):.3f}:d={TAIL_FADE_SEC:.2f}[aout]"
    )
    return ";".join(chains), round(total, 3)


def build_render_args(
    plan: EditPlan,
    sources: dict[str, Path],
    *,
    canvas_w: int,
    canvas_h: int,
    encoder: str,
    encoder_flags: list[str],
    output_path: Path,
    title_textfile: Path | None = None,
    image_rels: frozenset[str] | set[str] = frozenset(),
    no_audio_rels: frozenset[str] | set[str] = frozenset(),
    hdr_rels: frozenset[str] | set[str] = frozenset(),
    hdr_engine: str = "libplacebo",
    photo_rotations: dict[str, int] | None = None,
    photo_focus: dict[str, tuple[float, float]] | None = None,
) -> tuple[list[str], float]:
    args: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if hdr_rels and hdr_engine == "libplacebo":
        args += ["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"]
    args.append("-y")
    maps = InputMaps()
    next_input = 0

    for clip_index, clip in enumerate(plan.clips):
        source = sources[clip.rel_path]
        span = clip.end_sec - clip.start_sec
        if clip.rel_path in image_rels:
            maps.image_inputs.add(next_input)
            args += ["-i", str(source)]
            maps.video_of[clip_index] = next_input
            next_input += 1
        else:
            args += [
                "-ss",
                f"{clip.start_sec:.3f}",
                "-t",
                f"{span:.3f}",
                "-i",
                str(source),
            ]
            maps.video_of[clip_index] = next_input
            next_input += 1

        if clip.rel_path in no_audio_rels or clip.rel_path in image_rels:
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{span:.3f}",
                "-i",
                "anullsrc=r=48000:cl=stereo",
            ]
            maps.audio_of[clip_index] = next_input
            next_input += 1
        else:
            maps.audio_of[clip_index] = maps.video_of[clip_index]

        if clip.rel_path in hdr_rels and clip.rel_path not in image_rels:
            maps.hdr_inputs.add(maps.video_of[clip_index])

    graph, total = build_filtergraph(
        plan,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        maps=maps,
        title_textfile=title_textfile,
        hdr_engine=hdr_engine,
        image_rels=image_rels,
        photo_rotations=photo_rotations,
        photo_focus=photo_focus,
    )

    args += [
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-r",
        str(FPS),
        "-c:v",
        encoder,
        *encoder_flags,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return args, total
