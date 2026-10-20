"""A server-derived render disclosure; model-authored notes are not authoritative."""

import hashlib
import json

from .schemas import EditPlan
from .timing import expected_plan_duration


async def render_review(conn, plan_json: str, profile: str) -> dict:
    plan = EditPlan.model_validate(json.loads(plan_json))
    photos = {
        row["rel_path"]
        for row in await (
            await conn.execute("SELECT rel_path FROM files WHERE kind='photo'")
        ).fetchall()
    }
    warnings = list(plan.timing_adjustments)
    holds = [clip.end_sec - clip.start_sec for clip in plan.clips if clip.rel_path in photos]
    long_holds = [span for span in holds if span > 4 + 1 / 30]
    if long_holds:
        warnings.append(
            f"{len(long_holds)} still image(s) held longer than 4s; longest {max(long_holds):.1f}s to retain the requested duration."
        )
    freezes = sum(clip.freeze_tail_sec for clip in plan.clips)
    if freezes:
        warnings.append(f"Frozen end frames add {freezes:.1f}s to retain the requested duration.")
    fillers = sum(clip.duration_filler or clip.reason == "duration filler" for clip in plan.clips)
    if fillers:
        warnings.append(
            f"{fillers} additional scene(s) were added for duration; they were not in the model's original selection."
        )
    duration = expected_plan_duration(plan.clips)
    if abs(duration - plan.target_duration_sec) > 1 / 30:
        warnings.append(
            f"This saved plan is {duration:.1f}s, differing from its {plan.target_duration_sec:.1f}s target. Generate a new plan to refit it."
        )
    review = {
        "target_duration_sec": plan.target_duration_sec,
        "expected_duration_sec": duration,
        "sequencing": plan.sequencing or "saved_legacy_order",
        "sequencing_evidence": plan.sequencing_evidence,
        "warnings": warnings,
        "profile": profile,
    }
    review["token"] = hashlib.sha256(
        (plan_json + json.dumps(review, sort_keys=True) + "render-review.v1").encode()
    ).hexdigest()
    return review
