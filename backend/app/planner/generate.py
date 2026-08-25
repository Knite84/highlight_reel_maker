import json
import math
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..ai.providers import LLMProvider, PlannerOutputError
from ..renderer.filtergraph import FPS as RENDER_FPS
from .candidates import select_candidates
from .schemas import EDIT_PLAN_JSON_SCHEMA, EditPlan, PlannedClip

PLANNER_SYSTEM = (
    "You are an expert highlight-reel edit planner for a local home-video library. "
    "You receive a shortlist of analyzed scenes and a creative brief. "
    "Choose and sequence clips to build the requested reel. Hard rules:\n"
    "1. Every clip's rel_path MUST be copied verbatim from the shortlist.\n"
    "2. start_sec and end_sec are SECONDS copied from that candidate's [start, end] span; "
    "start_sec MUST be strictly LESS than end_sec.\n"
    "3. Total clip seconds should approximate target_duration_sec (within 20%);\n"
    "   pick roughly target_duration/4 clips, never more than needed.\n"
    "4. Keep individual clips between 1.2s and 8s.\n"
    "5. Order the clips STRICTLY chronologically (oldest capture first) so the reel "
    "plays in real sequential time; final ordering is enforced regardless.\n"
    "6. Use 'crossfade' as the DEFAULT transition between adjacent clips — always "
    "around photos, and generally every few clips. Reserve 'cut' only for fast "
    "action within the same scene. 'fade_from_black' belongs on the first clip only.\n"
    "7. Add ken_burns mainly for longer or scenic clips, not fast action.\n"
    "8. Keep every clip's reason under 12 words.\n"
    "9. Respond ONLY with JSON matching the provided schema."
)

MIN_CLIP_SEC = 1.2
MAX_CLIP_SEC = 8.0
PHOTO_DEFAULT_SEC = 2.5


class PlannerInputError(Exception):
    pass


class CandidateRef(BaseModel):
    scene_id: int
    rel_path: str
    kind: str
    start_sec: float
    end_sec: float
    score: float
    tags: list[str] = Field(default_factory=list)
    description: str = ""

    def summary_line(self, index: int) -> str:
        tag_text = ",".join(self.tags[:4])
        desc = self.description[:140].replace("\n", " ")
        if self.kind == "photo":
            span_text = "still image (set start_sec=0 and end_sec=desired seconds, 2-4s)"
        else:
            span_text = f"{self.start_sec:.1f}-{self.end_sec:.1f}s"
        return (
            f"[{index}] {self.rel_path} {span_text} "
            f"score={self.score:.2f} type={self.kind} tags=[{tag_text}] desc: {desc}"
        )


async def _load_candidate_refs(conn: Any) -> list[CandidateRef]:
    rows = await select_candidates(conn, limit=60, max_per_file=10, min_gap_sec=2.0)
    if not rows:
        return []
    scene_ids = [int(row["scene_id"]) for row in rows]
    placeholders = ",".join("?" * len(scene_ids))
    cur = await conn.execute(
        f"SELECT scene_id, tag FROM scene_tags WHERE scene_id IN ({placeholders})",
        scene_ids,
    )
    tags_by_scene: dict[int, list[str]] = {}
    for tag_row in await cur.fetchall():
        tags_by_scene.setdefault(int(tag_row["scene_id"]), []).append(tag_row["tag"])
    cur = await conn.execute(
        f"SELECT scene_id, description FROM scene_descriptions WHERE scene_id IN ({placeholders})",
        scene_ids,
    )
    descriptions_by_scene = {
        int(desc_row["scene_id"]): desc_row["description"] for desc_row in await cur.fetchall()
    }
    return [
        CandidateRef(
            scene_id=int(row["scene_id"]),
            rel_path=row["rel_path"],
            kind=row["kind"],
            start_sec=row["start_sec"],
            end_sec=row["end_sec"],
            score=row["score"],
            tags=tags_by_scene.get(int(row["scene_id"]), []),
            description=descriptions_by_scene.get(int(row["scene_id"]), ""),
        )
        for row in rows
    ]


def build_planner_prompt(refs: list[CandidateRef], prompt: str, target_duration_sec: float) -> str:
    suggested_clips = max(3, min(24, round(target_duration_sec / 4)))
    photo_count = sum(1 for ref in refs if ref.kind == "photo")
    lines = [
        f"Creative brief: {prompt.strip() or 'best moments of this collection'}",
        f"Target reel duration: {target_duration_sec:.0f} seconds of clip time.",
        f"Aim for about {suggested_clips} clips averaging 3-5 seconds each.",
        "",
        (
            "Candidate scenes (type=photo entries are still images; they make good "
            "breather moments between action clips):"
        ),
    ]
    if photo_count == 0:
        lines[-1] = "Candidate scenes:"
    lines.extend(ref.summary_line(i) for i, ref in enumerate(refs))
    lines.append("")
    lines.append(
        "Produce the Edit Plan JSON now. Example clip entry: "
        '{"rel_path": "<verbatim from list>", "start_sec": 12.5, "end_sec": 16.0, '
        '"transition_in": "cut"}. For type=photo entries use start_sec=0 and set '
        "end_sec to the seconds you want the still shown. Use only listed rel_paths."
    )
    return "\n".join(lines)


async def validate_and_fix_plan(plan: EditPlan, refs: list[CandidateRef], conn: Any) -> EditPlan:
    durations: dict[str, float] = {}
    cur = await conn.execute("SELECT rel_path, duration_sec FROM files")
    for row in await cur.fetchall():
        if row["duration_sec"]:
            durations[row["rel_path"]] = float(row["duration_sec"])

    valid_clips: list[PlannedClip] = []
    seen_spans: set[tuple[str, int]] = set()
    for clip in plan.clips:
        ref = None
        for candidate in refs:
            if candidate.rel_path == clip.rel_path:
                ref = candidate
                break
        if ref is None:
            continue

        if ref.kind == "photo":
            span = clip.end_sec - clip.start_sec
            duration = max(span, 1.5)
            span_key = (clip.rel_path, 0)
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            valid_clips.append(
                clip.model_copy(update={"start_sec": 0.0, "end_sec": round(duration, 2)})
            )
            continue

        file_duration = durations.get(clip.rel_path)
        end_limit = min(ref.end_sec + 0.25, file_duration) if file_duration else ref.end_sec + 0.25
        start = max(clip.start_sec, max(0.0, ref.start_sec - 0.5))
        end = min(clip.end_sec, end_limit)
        if end - start < MIN_CLIP_SEC * 0.75:
            continue
        span_key = (clip.rel_path, int(start))
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        valid_clips.append(clip.model_copy(update={"start_sec": round(start, 2), "end_sec": round(end, 2)}))

    if not valid_clips:
        raise PlannerInputError("planner produced no usable clips from candidates")

    order = await _capture_order_map(conn)
    valid_clips.sort(key=lambda clip: order.get(clip.rel_path, (float("inf"), clip.rel_path)))

    target = plan.target_duration_sec

    total = sum(c.end_sec - c.start_sec for c in valid_clips)
    while total > target and len(valid_clips) > 1:
        removed = valid_clips.pop()
        total -= removed.end_sec - removed.start_sec

    valid_clips, total = _extend_toward_target(valid_clips, refs, durations, until=target)
    if total < target:
        valid_clips = _append_filler_clips(valid_clips, refs, target)

    valid_clips = fit_exact_duration(valid_clips, refs, durations, target)

    return plan.model_copy(update={"clips": valid_clips})


def expected_plan_duration(clips: list[PlannedClip]) -> float:
    if not clips:
        return 0.0
    runs: list[list[int]] = []
    for index, clip in enumerate(clips):
        starts_new_run = index > 0 and clip.transition_in != "cut"
        if not runs or starts_new_run:
            runs.append([index])
        else:
            runs[-1].append(index)

    run_durations: list[float] = []
    for indexes in runs:
        duration = sum(
            round(clips[i].end_sec - clips[i].start_sec + clips[i].freeze_tail_sec, 6)
            for i in indexes
        )
        run_durations.append(round(duration, 4))

    total = run_durations[0]
    for k in range(1, len(runs)):
        boundary = clips[runs[k][0]]
        d = min(boundary.transition_duration_sec, total * 0.5, run_durations[k] * 0.5)
        offset = round(total - d, 4)
        total = offset + run_durations[k]
    return round(total, 3)


def fit_exact_duration(
    clips: list[PlannedClip],
    refs: list[CandidateRef],
    durations: dict[str, float],
    target_seconds: float,
) -> list[PlannedClip]:
    updated = [c.model_copy(update={"freeze_tail_sec": 0.0}) for c in clips]
    quantum = 1.0 / RENDER_FPS
    max_iterations = 20000
    for _iteration in range(max_iterations):
        current = expected_plan_duration(updated)
        diff_frames = round((target_seconds - current) * RENDER_FPS)
        if diff_frames == 0:
            return updated
        if diff_frames > 0:
            progressed = _grow_one_frame(updated, refs, durations)
        else:
            progressed = _shrink_one_frame(updated, refs)
        if not progressed:
            break
        del diff_frames
    current = expected_plan_duration(updated)
    residual = round(target_seconds - current, 4)
    if residual > quantum:
        if updated:
            last = updated[-1]
            last_ref = next((r for r in refs if r.rel_path == last.rel_path), None)
            if last_ref is None or last_ref.kind != "photo":
                updated[-1] = last.model_copy(
                    update={"freeze_tail_sec": round(last.freeze_tail_sec + residual, 4)}
                )
    elif residual < -quantum:
        excess = -residual
        for index in range(len(updated) - 1, -1, -1):
            clip = updated[index]
            min_span = _clip_min_span(clip, refs)
            reducible = (clip.end_sec - clip.start_sec) - min_span
            if reducible <= 0:
                continue
            cut = min(excess, reducible)
            updated[index] = clip.model_copy(
                update={"end_sec": round(clip.end_sec - cut, 4)}
            )
            excess -= cut
            if excess <= 0:
                break
    return updated


def _grow_one_frame(
    updated: list[PlannedClip], refs: list[CandidateRef], durations: dict[str, float]
) -> bool:
    quantum = 1.0 / RENDER_FPS
    best_index = None
    best_span = None
    for index, clip in enumerate(updated):
        bounds = _clip_bounds(clip, refs, durations)
        if bounds is None:
            continue
        _low, high = bounds
        if high - clip.end_sec < quantum:
            continue
        span = clip.end_sec - clip.start_sec
        if best_span is None or span < best_span:
            best_span = span
            best_index = index
    if best_index is None:
        return False
    clip = updated[best_index]
    updated[best_index] = clip.model_copy(update={"end_sec": round(clip.end_sec + quantum, 6)})
    return True


def _shrink_one_frame(updated: list[PlannedClip], refs: list[CandidateRef]) -> bool:
    quantum = 1.0 / RENDER_FPS
    best_index = None
    best_span = None
    for index, clip in enumerate(updated):
        min_span = _clip_min_span(clip, refs)
        reducible = (clip.end_sec - clip.start_sec) - min_span
        if reducible < quantum:
            continue
        span = clip.end_sec - clip.start_sec
        if best_span is None or span >= best_span:
            best_span = span
            best_index = index
    if best_index is None:
        return False
    clip = updated[best_index]
    updated[best_index] = clip.model_copy(update={"end_sec": round(clip.end_sec - quantum, 6)})
    return True


def _clip_min_span(clip: PlannedClip, refs: list[CandidateRef]) -> float:
    ref = next((r for r in refs if r.rel_path == clip.rel_path), None)
    return 1.5 if (ref is None or ref.kind == "photo") else MIN_CLIP_SEC


def _append_filler_clips(
    clips: list[PlannedClip], refs: list[CandidateRef], target: float
) -> list[PlannedClip]:
    def span_of(clip: PlannedClip) -> float:
        return clip.end_sec - clip.start_sec

    result = list(clips)
    used_keys = {(clip.rel_path, int(clip.start_sec)) for clip in result}
    for ref in refs:
        current_total = sum(span_of(c) for c in result)
        if current_total >= target:
            break
        key = (ref.rel_path, int(ref.start_sec))
        if key in used_keys:
            continue
        if ref.kind == "photo":
            result.append(
                PlannedClip(
                    rel_path=ref.rel_path,
                    start_sec=0.0,
                    end_sec=PHOTO_DEFAULT_SEC,
                    transition_in="crossfade",
                    reason="duration filler",
                )
            )
            used_keys.add((ref.rel_path, 0))
            continue
        available = ref.end_sec - ref.start_sec
        natural_span = min(max(available, MIN_CLIP_SEC), MAX_CLIP_SEC)
        span = min(natural_span, max(target - current_total, MIN_CLIP_SEC))
        if span < MIN_CLIP_SEC or available < MIN_CLIP_SEC:
            continue
        start = ref.start_sec + max(0.0, (available - span) / 2)
        end = min(ref.end_sec, start + span)
        if end - start < MIN_CLIP_SEC:
            continue
        result.append(
            PlannedClip(
                rel_path=ref.rel_path,
                start_sec=round(start, 2),
                end_sec=round(end, 2),
                transition_in="crossfade",
                reason="duration filler",
            )
        )
        used_keys.add(key)
    return result


_FILENAME_TS_RE = re.compile(r"(\d{8})_?(\d{6})")


async def _capture_order_map(conn: Any) -> dict[str, tuple[float, str]]:
    cur = await conn.execute("SELECT rel_path, captured_at, mtime FROM files")
    rows = await cur.fetchall()
    keys: dict[str, tuple[float, str]] = {}
    for row in rows:
        rel_path = row["rel_path"]
        timestamp: float | None = None
        captured_at = row["captured_at"]
        if captured_at:
            try:
                timestamp = datetime.fromisoformat(captured_at).timestamp()
            except ValueError:
                timestamp = None
        if timestamp is None:
            match = _FILENAME_TS_RE.search(rel_path)
            if match:
                try:
                    date_part, time_part = match.groups()
                    parsed = datetime(
                        int(date_part[:4]),
                        int(date_part[4:6]),
                        int(date_part[6:8]),
                        int(time_part[:2]),
                        int(time_part[2:4]),
                        int(time_part[4:6]),
                    )
                    timestamp = parsed.timestamp()
                except ValueError:
                    timestamp = None
        if timestamp is None:
            timestamp = float(row["mtime"] or 0)
        keys[rel_path] = (timestamp, rel_path)
    return keys


def _clip_bounds(
    clip: PlannedClip, refs: list[CandidateRef], durations: dict[str, float]
) -> tuple[float, float] | None:
    ref = next((r for r in refs if r.rel_path == clip.rel_path), None)
    if ref is None:
        return None
    if ref.kind == "photo":
        return 0.0, float("inf")
    file_duration = durations.get(clip.rel_path)
    hard_high = min(ref.end_sec + 0.25, file_duration) if file_duration else ref.end_sec + 0.25
    hard_low = max(0.0, ref.start_sec - 0.5)
    return hard_low, hard_high


def _extend_toward_target(
    clips: list[PlannedClip],
    refs: list[CandidateRef],
    durations: dict[str, float],
    until: float,
) -> tuple[list[PlannedClip], float]:
    floor = until

    def total_of(items: list[PlannedClip]) -> float:
        return sum(c.end_sec - c.start_sec for c in items)

    updated = list(clips)
    for _round in range(6):
        total = total_of(updated)
        if total >= floor:
            break
        progressed = False
        for index, clip in enumerate(updated):
            if total >= floor:
                break
            bounds = _clip_bounds(clip, refs, durations)
            if bounds is None:
                continue
            low, high = bounds
            if high > clip.end_sec + 0.01 and math.isfinite(high):
                grown = round(high, 2)
                total += grown - clip.end_sec
                updated[index] = clip.model_copy(update={"end_sec": grown})
                clip = updated[index]
                progressed = True
                if total >= floor:
                    break
            if low < clip.start_sec - 0.01 and total < floor:
                shrunk = round(low, 2)
                total += clip.start_sec - shrunk
                updated[index] = clip.model_copy(update={"start_sec": shrunk})
                progressed = True
        if not progressed:
            break
    return updated, total_of(updated)


async def generate_plan(
    provider: LLMProvider,
    model: str,
    conn: Any,
    *,
    prompt: str,
    target_duration_sec: float,
    seed: int,
) -> tuple[EditPlan, list[CandidateRef]]:
    refs = await _load_candidate_refs(conn)
    if not refs:
        raise PlannerInputError(
            "no analyzed scenes available; run analysis before planning"
        )
    user_text = build_planner_prompt(refs, prompt, target_duration_sec)
    data = await provider.chat_json(
        system=PLANNER_SYSTEM,
        user_parts=user_text,
        model=model,
        schema=EDIT_PLAN_JSON_SCHEMA,
        temperature=0.6,
        retries=2,
        max_tokens=6000,
    )
    try:
        plan = EditPlan.model_validate(data)
    except Exception as exc:
        raise PlannerOutputError(f"plan failed validation: {exc}", raw=json.dumps(data)[:400]) from exc
    plan = plan.model_copy(update={"prompt": prompt, "seed": seed})
    fixed = await validate_and_fix_plan(plan, refs, conn)
    return fixed, refs
