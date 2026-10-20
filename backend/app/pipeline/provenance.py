"""Cache contracts. Legacy evidence is retained without claiming verified provenance."""

import base64
import json
from pathlib import Path

CONTRACTS = {
    "metadata": "meta.v1",
    "scenes": "scenes.v1",
    "visuals": "visuals.v1",
    "embeddings": "siglip-tags.v1",
    "descriptions": "describe.v1",
}


def source_stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def check_source(path: Path, expected: tuple[int, int]) -> None:
    if source_stamp(path) != expected:
        raise RuntimeError("Source changed during analysis; retry Analyze")


async def mark_source_edits(conn, rel_path, *, identical=False):
    """Retain old plans/exports, but require new planning after changed footage."""
    rows = await (await conn.execute("SELECT id,plan_json,stale_sources FROM edits")).fetchall()
    source = await (
        await conn.execute("SELECT xxhash FROM files WHERE rel_path=?", (rel_path,))
    ).fetchone()
    source_hash = source[0] if source else None
    for row in rows:
        plan = json.loads(row["plan_json"])
        if not any(clip.get("rel_path") == rel_path for clip in plan.get("clips", [])):
            continue
        stale = json.loads(row["stale_sources"])
        if isinstance(stale, list):
            stale = dict.fromkeys(stale)
        if identical and source_hash and stale.get(rel_path) == source_hash:
            stale.pop(rel_path, None)
        else:
            stale.setdefault(rel_path, source_hash)
        await conn.execute(
            "UPDATE edits SET stale_sources=? WHERE id=?",
            (json.dumps(stale, sort_keys=True), row["id"]),
        )


async def archive_knowledge(conn, file_id: int, reason: str) -> None:
    """Snapshot SQL rows including vectors, leaving referenced artifact files intact."""
    snapshot = {}
    queries = {
        "files": "SELECT * FROM files WHERE id=?",
        "scenes": "SELECT * FROM scenes WHERE file_id=?",
        "analysis_stages": "SELECT * FROM analysis_stages WHERE file_id=?",
    }
    for table in ("scene_frames", "scene_tags", "embeddings", "scene_descriptions"):
        queries[table] = (
            f"SELECT * FROM {table} WHERE scene_id IN (SELECT id FROM scenes WHERE file_id=?)"
        )
    for table, sql in queries.items():
        rows = await (await conn.execute(sql, (file_id,))).fetchall()
        snapshot[table] = [dict(row) for row in rows]
    await conn.execute(
        "INSERT INTO knowledge_history(file_id,reason,snapshot_json) VALUES(?,?,?)",
        (
            file_id,
            reason,
            json.dumps(snapshot, default=lambda b: {"base64": base64.b64encode(b).decode()}),
        ),
    )


async def record_stage(conn, file_id, stage, revision, model=None, *, legacy=False):
    await conn.execute(
        "INSERT OR REPLACE INTO analysis_stages"
        "(file_id,stage,input_revision,contract_version,model_id,status) VALUES(?,?,?,?,?,?)",
        (file_id, stage, revision, CONTRACTS[stage], model, "legacy" if legacy else "success"),
    )


async def invalidate_contracts(conn, file_id, *, embedding_model, description_model):
    rows = await (
        await conn.execute("SELECT * FROM analysis_stages WHERE file_id=?", (file_id,))
    ).fetchall()
    changed = set()
    for row in rows:
        stage = row["stage"]
        if row["contract_version"] != CONTRACTS.get(stage):
            changed.add(stage)
        desired = {"embeddings": embedding_model, "descriptions": description_model}.get(stage)
        if desired and row["model_id"] != desired:
            changed.add(stage)
    if not changed:
        return
    await archive_knowledge(
        conn, file_id, "Stage/model contract changed: " + ", ".join(sorted(changed))
    )
    if "metadata" in changed:
        await conn.execute("UPDATE files SET analyzed_at=NULL WHERE id=?", (file_id,))
        changed.update(("scenes", "visuals", "embeddings", "descriptions"))
    if "scenes" in changed:
        await conn.execute("DELETE FROM scenes WHERE file_id=?", (file_id,))
        changed.update(("visuals", "embeddings", "descriptions"))
    if "visuals" in changed:
        await conn.execute(
            "DELETE FROM scene_frames WHERE scene_id IN (SELECT id FROM scenes WHERE file_id=?)",
            (file_id,),
        )
        changed.update(("embeddings", "descriptions"))
    for stage, tables in {
        "embeddings": ("embeddings", "scene_tags"),
        "descriptions": ("scene_descriptions",),
    }.items():
        if stage in changed:
            for table in tables:
                await conn.execute(
                    f"DELETE FROM {table} WHERE scene_id IN (SELECT id FROM scenes WHERE file_id=?)",
                    (file_id,),
                )
    for stage in changed:
        await conn.execute(
            "DELETE FROM analysis_stages WHERE file_id=? AND stage=?", (file_id, stage)
        )


async def stage_present(conn, file_id, stage):
    count = (
        await (
            await conn.execute("SELECT COUNT(*) FROM scenes WHERE file_id=?", (file_id,))
        ).fetchone()
    )[0]
    if stage == "metadata":
        return (
            await (
                await conn.execute("SELECT analyzed_at FROM files WHERE id=?", (file_id,))
            ).fetchone()
        )[0] is not None
    if stage in ("scenes", "visuals"):
        return count > 0
    table = {"embeddings": "embeddings", "descriptions": "scene_descriptions"}[stage]
    actual = (
        await (
            await conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE scene_id IN (SELECT id FROM scenes WHERE file_id=?)",
                (file_id,),
            )
        ).fetchone()
    )[0]
    return count > 0 and count == actual


def artifact_exists(cache_dir: Path, rel: str | None) -> bool:
    if not rel:
        return False
    path = (cache_dir / rel).resolve()
    return path.is_relative_to(cache_dir.resolve()) and path.is_file() and path.stat().st_size > 0


async def missing_visuals(conn, cache_dir, file_id):
    scenes = await (
        await conn.execute("SELECT id,rep_frame_path FROM scenes WHERE file_id=?", (file_id,))
    ).fetchall()
    broken = []
    for scene in scenes:
        frames = await (
            await conn.execute(
                "SELECT role,path FROM scene_frames WHERE scene_id=?", (scene["id"],)
            )
        ).fetchall()
        if (
            not any(f["role"] == "primary" for f in frames)
            or not artifact_exists(cache_dir, scene["rep_frame_path"])
            or any(not artifact_exists(cache_dir, f["path"]) for f in frames)
        ):
            broken.append(scene["id"])
    return broken
