"""Validate persisted references without silently rebinding plans to replacement media."""

from .schemas import EditPlan

REPLAN = "Plan scene/source reference is stale or ambiguous; Analyze then Generate a new plan. Previous exports remain downloadable."


async def validate_plan_identity(conn, plan: EditPlan) -> None:
    for clip in plan.clips:
        if plan.schema_version == "v2":
            row = await (
                await conn.execute(
                    "SELECT s.start_sec,s.end_sec,f.id,f.rel_path,f.kind,f.source_revision,"
                    "f.analysis_revision,f.xxhash FROM scenes s JOIN files f ON f.id=s.file_id "
                    "WHERE s.id=?",
                    (clip.scene_id,),
                )
            ).fetchone()
            if (
                row is None
                or row["id"] != clip.source_file_id
                or row["rel_path"] != clip.rel_path
                or row["analysis_revision"] != row["source_revision"]
                or clip.source_revision > row["source_revision"]
                or row["xxhash"] != clip.source_hash
            ):
                raise ValueError(REPLAN)
            if row["kind"] == "video" and not (
                clip.start_sec >= row["start_sec"] - 0.001
                and clip.end_sec <= row["end_sec"] + 0.001
            ):
                raise ValueError(REPLAN)
        else:
            # v1 timestamps remain authoritative. A unique matching scene may be
            # checked, but neither quality rank nor SQL row order supplies identity.
            rows = await (
                await conn.execute(
                    "SELECT s.start_sec,s.end_sec,f.kind FROM scenes s JOIN files f ON f.id=s.file_id "
                    "WHERE f.rel_path=?",
                    (clip.rel_path,),
                )
            ).fetchall()
            if rows:
                matches = [
                    row
                    for row in rows
                    if row["kind"] == "photo"
                    or (
                        clip.start_sec >= max(0, row["start_sec"] - 0.5)
                        and clip.end_sec <= row["end_sec"] + 0.25
                    )
                ]
                if len(matches) != 1:
                    raise ValueError(REPLAN)
