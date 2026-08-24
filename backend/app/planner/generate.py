import json
from typing import Any

from pydantic import BaseModel, Field

from ..ai.providers import LLMProvider, PlannerOutputError
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
    "5. Prefer chronological ordering unless a deliberate montage is stronger.\n"
    "6. Use 'cut' transitions mostly; use 'crossfade' sparingly at section changes; "
    "'fade_from_black' only on the first clip.\n"
    "7. Add ken_burns mainly for longer or scenic clips, not fast action.\n"
    "8. Keep every clip's reason under 12 words.\n"
    "9. Respond ONLY with JSON matching the provided schema."
)

MIN_CLIP_SEC = 1.2
MAX_CLIP_SEC = 8.0


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
        return (
            f"[{index}] {self.rel_path} {self.start_sec:.1f}-{self.end_sec:.1f}s "
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
        '"transition_in": "cut"}. For type=photo entries use the full listed span '
        "(typically 2-4 seconds). Use only listed rel_paths and spans."
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

    total = sum(c.end_sec - c.start_sec for c in valid_clips)
    target = plan.target_duration_sec
    while total > target * 1.35 and len(valid_clips) > 1:
        removed = valid_clips.pop()
        total -= removed.end_sec - removed.start_sec

    valid_clips, total = _extend_toward_target(valid_clips, refs, durations, target)

    return plan.model_copy(update={"clips": valid_clips})


def _clip_bounds(
    clip: PlannedClip, refs: list[CandidateRef], durations: dict[str, float]
) -> tuple[float, float] | None:
    ref = next((r for r in refs if r.rel_path == clip.rel_path), None)
    if ref is None:
        return None
    file_duration = durations.get(clip.rel_path)
    hard_high = min(ref.end_sec + 0.25, file_duration) if file_duration else ref.end_sec + 0.25
    hard_low = max(0.0, ref.start_sec - 0.5)
    return hard_low, hard_high


def _extend_toward_target(
    clips: list[PlannedClip],
    refs: list[CandidateRef],
    durations: dict[str, float],
    target: float,
) -> tuple[list[PlannedClip], float]:
    floor = target * 0.85

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
            if high > clip.end_sec + 0.01:
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
