"""Timeline policy shared by planning, review, and FFmpeg graph construction."""

from dataclasses import dataclass

from .schemas import PlannedClip

FPS = 30


@dataclass(frozen=True)
class Timeline:
    runs: list[list[int]]
    run_durations: list[float]
    overlaps: list[float]
    offsets: list[float]
    duration: float


def timeline(clips: list[PlannedClip]) -> Timeline:
    runs: list[list[int]] = []
    for index, clip in enumerate(clips):
        if not runs or (index > 0 and clip.transition_in != "cut"):
            runs.append([index])
        else:
            runs[-1].append(index)
    durations = [
        round(
            sum(
                round(clips[i].end_sec - clips[i].start_sec + clips[i].freeze_tail_sec, 6)
                for i in run
            ),
            4,
        )
        for run in runs
    ]
    total = durations[0] if durations else 0.0
    overlaps, offsets = [], []
    for index in range(1, len(runs)):
        boundary = clips[runs[index][0]]
        overlap = min(boundary.transition_duration_sec, total * 0.5, durations[index] * 0.5)
        offset = round(total - overlap, 4)
        overlaps.append(overlap)
        offsets.append(offset)
        total = offset + durations[index]
    return Timeline(runs, durations, overlaps, offsets, total)


def expected_plan_duration(clips: list[PlannedClip]) -> float:
    return round(timeline(clips).duration, 3)
