from pathlib import Path

from ..planner.schemas import EditPlan

FPS = 30
TAIL_FADE_SEC = 0.4


def _kb_filter(direction: str, intensity: float, frames: int, out_res: str) -> str:
    frames = max(frames, 2)
    zoom_span = max(0.02, min(intensity, 0.5))
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    base = f"d=1:s={out_res}:fps={FPS}"
    if direction == "zoom_in":
        rate = zoom_span / frames
        return f"zoompan=z='1+{rate:.6f}*on':{center}:{base}"
    if direction == "zoom_out":
        rate = zoom_span / frames
        return f"zoompan=z='{1 + zoom_span:.4f}-{rate:.6f}*on':{center}:{base}"
    fixed_zoom = 1.0 + max(0.04, zoom_span)
    progress = f"(on/{frames})"
    if direction == "pan_left":
        return f"zoompan=z='{fixed_zoom:.4f}':x='(iw-iw/zoom)*{progress}':y='ih/2-(ih/zoom/2)':{base}"
    if direction == "pan_right":
        return f"zoompan=z='{fixed_zoom:.4f}':x='(iw-iw/zoom)*(1-{progress})':y='ih/2-(ih/zoom/2)':{base}"
    if direction == "pan_up":
        return f"zoompan=z='{fixed_zoom:.4f}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*{progress}':{base}"
    if direction == "pan_down":
        return f"zoompan=z='{fixed_zoom:.4f}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-{progress})':{base}"
    raise ValueError(f"unsupported ken_burns direction: {direction}")


def _clip_chain(
    index: int,
    span_seconds: float,
    ken_burns: dict | None,
    canvas_w: int,
    canvas_h: int,
    fade_in_sec: float | None,
) -> tuple[str, str]:
    label = f"c{index}"
    out_res = f"{canvas_w}x{canvas_h}"
    filters = [
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase",
        f"crop={canvas_w}:{canvas_h}",
        f"fps={FPS}",
        "setsar=1",
    ]
    if ken_burns:
        frames = max(int(span_seconds * FPS), 2)
        filters.append(
            _kb_filter(
                str(ken_burns.get("direction", "zoom_in")),
                float(ken_burns.get("intensity", 0.08)),
                frames,
                out_res,
            )
        )
    if fade_in_sec:
        filters.append(f"fade=t=in:st=0:d={fade_in_sec:.2f}")
    return f"[{index}:v]{','.join(filters)}[{label}]", label


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


def build_filtergraph(
    plan: EditPlan,
    *,
    canvas_w: int,
    canvas_h: int,
    title_textfile: Path | None = None,
) -> tuple[str, float]:
    clips = plan.clips
    chains: list[str] = []
    runs = _split_runs(plan)

    run_labels: list[str] = []
    run_durations: list[float] = []
    for run_index, indexes in enumerate(runs):
        labels: list[str] = []
        duration = 0.0
        for position, clip_index in enumerate(indexes):
            clip = clips[clip_index]
            span = clip.end_sec - clip.start_sec
            fade_in = (
                clip.transition_duration_sec
                if run_index == 0 and position == 0 and clip.transition_in == "fade_from_black"
                else None
            )
            chain, label = _clip_chain(
                clip_index,
                span,
                clip.ken_burns.model_dump() if clip.ken_burns else None,
                canvas_w,
                canvas_h,
                fade_in,
            )
            chains.append(chain)
            labels.append(label)
            duration += span
        run_label = labels[0]
        if len(labels) > 1:
            run_label = f"r{run_index}"
            inputs = "".join(f"[{label}]" for label in labels)
            chains.append(f"{inputs}concat=n={len(labels)}:v=1:a=0[{run_label}]")
        run_labels.append(run_label)
        run_durations.append(round(duration, 4))

    result_label = run_labels[0]
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
        result_label = merged_label
        total = offset + run_durations[k]

    if total > TAIL_FADE_SEC * 2:
        chains.append(
            f"[{result_label}]fade=t=out:st={total - TAIL_FADE_SEC:.3f}:d={TAIL_FADE_SEC:.2f}[vt]"
        )
        result_label = "vt"

    if plan.title is not None and title_textfile is not None:
        title_filters = _title_filters(plan, title_textfile, total, canvas_h)
        chains.append(f"[{result_label}]{','.join(title_filters)}[vout]")
        result_label = "vout"

    if result_label != "vout":
        chains.append(f"[{result_label}]null[vout]")
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
) -> tuple[list[str], float]:
    args: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for clip in plan.clips:
        source = sources[clip.rel_path]
        if clip.rel_path in image_rels:
            args += ["-loop", "1", "-t", f"{clip.end_sec - clip.start_sec:.3f}", "-i", str(source)]
        else:
            args += [
                "-ss",
                f"{clip.start_sec:.3f}",
                "-t",
                f"{clip.end_sec - clip.start_sec:.3f}",
                "-i",
                str(source),
            ]
    graph, total = build_filtergraph(
        plan,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        title_textfile=title_textfile,
    )
    args += [
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-an",
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
