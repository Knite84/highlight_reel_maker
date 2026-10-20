import json
from pathlib import Path
from typing import Any

from ..ai.embeddings import MODEL_ID, Embedder, score_tags, vector_to_blob
from ..ai.providers import LLMProvider, ProviderError, get_provider
from ..core.config import get_settings
from ..core.db import connect, migrate_project
from ..core.tasks import run_blocking
from ..jobs.worker import JobFailedWithResult
from .describe import describe_pending_scenes
from .frames_cv import process_photo_async, process_video_async
from .metadata import checksum_file, extract_metadata
from .orientation import ensure_orientation
from .provenance import (
    archive_knowledge,
    check_source,
    invalidate_contracts,
    mark_source_edits,
    missing_visuals,
    record_stage,
    source_stamp,
    stage_present,
)
from .scan import scan_project
from .scenes import detect_scenes

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


async def _store_scene_visuals(conn: Any, scene_id: int, entry: dict) -> None:
    metrics = entry["metrics"]
    samples = {sample["path"]: sample for sample in entry.get("frame_samples", [])}
    primary = samples.get(entry["primary_rel"], {})
    await conn.execute("DELETE FROM scene_frames WHERE scene_id = ?", (scene_id,))
    await conn.execute(
        "INSERT INTO scene_frames(scene_id, role, path, blur, brightness, contrast, motion, "
        "stability, dominant_colors, timestamp_sec, timestamp_source) "
        "VALUES (?, 'primary', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scene_id,
            entry["primary_rel"],
            metrics.get("blur"),
            metrics.get("brightness"),
            metrics.get("contrast"),
            metrics.get("motion"),
            metrics.get("stability"),
            json.dumps(metrics.get("dominant_colors", [])),
            primary.get("timestamp_sec"),
            primary.get("timestamp_source"),
        ),
    )
    for secondary_rel in entry["secondary_rels"]:
        sample = samples.get(secondary_rel, {})
        await conn.execute(
            "INSERT INTO scene_frames(scene_id, role, path, timestamp_sec, timestamp_source) "
            "VALUES (?, 'secondary', ?, ?, ?)",
            (scene_id, secondary_rel, sample.get("timestamp_sec"), sample.get("timestamp_source")),
        )
    await conn.execute(
        "UPDATE scenes SET rep_frame_path = ? WHERE id = ?", (entry["thumb_rel"], scene_id)
    )


async def analyze_job_handler(ctx: Any, payload: dict, *, on_inventory=None) -> dict:
    media_root = Path(payload["media_path"])
    cache_dir = Path(payload["db_path"]).parent
    conn = await connect(Path(payload["db_path"]))
    summary: dict = {
        "metadata": 0,
        "scenes": 0,
        "visuals": 0,
        "embedded": 0,
        "described": 0,
        "errors": [],
    }
    try:
        await migrate_project(conn)
        await ctx.progress(done=0, total=0, message="Scanning folder", force=True)
        summary["inventory"] = await scan_project(ctx, payload, conn, on_inventory=on_inventory)
        cur = await conn.execute("SELECT * FROM files ORDER BY rel_path")
        files = await cur.fetchall()
        total = len(files)
        await ctx.progress(done=0, total=total, message=f"Analyzing {total} files", force=True)
        embeddings_disabled = False
        descriptions_disabled = False
        provider_holder: dict = {}

        for index, row in enumerate(files):
            ctx.check_cancelled()
            file_id = row["id"]
            rel_path = row["rel_path"]
            abs_path = media_root / rel_path
            kind = row["kind"]
            try:
                if not abs_path.is_file():
                    raise FileNotFoundError("source file missing")
                stamp = source_stamp(abs_path)
                if stamp[0] != row["size_bytes"] or abs_path.stat().st_mtime != row["mtime"]:
                    raise RuntimeError("Source changed after inventory; retry Analyze")
                revision = row["source_revision"]
                changed = revision != row["analysis_revision"]
                checksum = row["xxhash"]
                work_before = sum(
                    summary[k] for k in ("metadata", "scenes", "visuals", "embedded", "described")
                )
                if changed or not checksum or row["error"] or payload.get("deep_verify", False):
                    checksum = await checksum_file(abs_path)
                    check_source(abs_path, stamp)
                    if row["xxhash"] and checksum != row["xxhash"] and not changed:
                        revision += 1
                        changed = True
                        await mark_source_edits(conn, rel_path)
                        await conn.execute(
                            "UPDATE files SET source_revision=? WHERE id=?", (revision, file_id)
                        )
                        await conn.commit()
                    elif changed and checksum == row["xxhash"] and row["analyzed_at"]:
                        # A timestamp/size signal is only a suspicion; identical bytes
                        # retain their knowledge and advance the inventory revision.
                        await conn.execute(
                            "UPDATE files SET analysis_revision=? WHERE id=?", (revision, file_id)
                        )
                        await conn.execute(
                            "UPDATE analysis_stages SET input_revision=? WHERE file_id=?",
                            (revision, file_id),
                        )
                        changed = False
                        await mark_source_edits(conn, rel_path, identical=True)
                prior_meta = await (
                    await conn.execute(
                        "SELECT input_revision FROM analysis_stages WHERE file_id=? AND stage='metadata'",
                        (file_id,),
                    )
                ).fetchone()
                replace_source = changed and (not prior_meta or prior_meta[0] != revision)
                await invalidate_contracts(
                    conn,
                    file_id,
                    embedding_model=MODEL_ID,
                    description_model=get_settings().planner_model_id,
                )
                current = await (
                    await conn.execute("SELECT analyzed_at FROM files WHERE id=?", (file_id,))
                ).fetchone()
                if replace_source or current["analyzed_at"] is None or row["rotation"] is None:
                    meta = await extract_metadata(abs_path, kind)
                    check_source(abs_path, stamp)
                    if replace_source:
                        await archive_knowledge(conn, file_id, "Source revision changed")
                        await conn.execute("DELETE FROM scenes WHERE file_id=?", (file_id,))
                        await conn.execute(
                            "DELETE FROM analysis_stages WHERE file_id=?", (file_id,)
                        )
                    await conn.execute(
                        "UPDATE files SET duration_sec=?, width=?, height=?, fps=?, codec=?, "
                        "camera_model=?, gps_lat=?, gps_lon=?, captured_at=?, rotation=?, "
                        f"xxhash=?, stage_version='meta.v1', analyzed_at={NOW_SQL}, error=NULL "
                        "WHERE id=?",
                        (
                            meta["duration_sec"],
                            meta["width"],
                            meta["height"],
                            meta["fps"],
                            meta["codec"],
                            meta["camera_model"],
                            meta["gps_lat"],
                            meta["gps_lon"],
                            meta["captured_at"],
                            meta["rotation"],
                            checksum,
                            file_id,
                        ),
                    )
                    await record_stage(conn, file_id, "metadata", revision)
                    await conn.commit()
                    summary["metadata"] += 1

                await ensure_orientation(conn, file_id, abs_path, kind, revision, stamp)
                cur = await conn.execute(
                    "SELECT duration_sec, fps, rotation FROM files WHERE id=?", (file_id,)
                )
                meta_row = await cur.fetchone()

                if kind == "video":
                    cur = await conn.execute(
                        "SELECT COUNT(*) FROM scenes WHERE file_id=?", (file_id,)
                    )
                    if int((await cur.fetchone())[0]) == 0:
                        spans = await detect_scenes(
                            abs_path, meta_row["duration_sec"], meta_row["fps"]
                        )
                        check_source(abs_path, stamp)
                        for start, end in spans:
                            await conn.execute(
                                "INSERT OR IGNORE INTO scenes(file_id, start_sec, end_sec, "
                                "stage_version) VALUES (?, ?, ?, 'scenes.v1')",
                                (file_id, start, end),
                            )
                        summary["scenes"] += len(spans)
                        await record_stage(conn, file_id, "scenes", revision)
                else:
                    inserted = await conn.execute(
                        "INSERT OR IGNORE INTO scenes(file_id, start_sec, end_sec, stage_version)"
                        " VALUES (?, 0, 0, 'scenes.v1')",
                        (file_id,),
                    )
                    if inserted.rowcount:
                        await record_stage(conn, file_id, "scenes", revision)
                await conn.commit()

                broken = await missing_visuals(conn, cache_dir, file_id)
                if broken:
                    cur = await conn.execute(
                        "SELECT id, start_sec, end_sec FROM scenes WHERE file_id=? ORDER BY start_sec",
                        (file_id,),
                    )
                    scene_rows = [r for r in await cur.fetchall() if r["id"] in broken]
                    rotation = meta_row["rotation"] or 0
                    if kind == "video":
                        spans = [(r["start_sec"], r["end_sec"]) for r in scene_rows]
                        entries = await process_video_async(
                            abs_path, rotation, spans, cache_dir, file_id
                        )
                    else:
                        entries = [
                            await process_photo_async(abs_path, rotation, cache_dir, file_id)
                        ]
                    check_source(abs_path, stamp)
                    by_start = {r["start_sec"]: r["id"] for r in scene_rows}
                    for entry in entries:
                        await _store_scene_visuals(conn, by_start[entry["start"]], entry)
                        summary["visuals"] += 1
                    await record_stage(conn, file_id, "visuals", revision)
                    await conn.commit()

                if not embeddings_disabled:
                    embedded, failed = await _embed_file_scenes(conn, cache_dir, file_id)
                    if failed:
                        embeddings_disabled = True
                        summary["errors"].append(
                            {"file": "", "error": "ml dependencies unavailable; embeddings skipped"}
                        )
                    else:
                        summary["embedded"] += embedded
                        if embedded:
                            await record_stage(conn, file_id, "embeddings", revision, MODEL_ID)

                if not descriptions_disabled:
                    try:
                        described = await _describe_file_scenes(
                            conn, cache_dir, file_id, provider_holder
                        )
                        summary["described"] += described
                        if described:
                            await record_stage(
                                conn,
                                file_id,
                                "descriptions",
                                revision,
                                get_settings().planner_model_id,
                            )
                    except ProviderError as exc:
                        descriptions_disabled = True
                        summary["errors"].append(
                            {
                                "file": "",
                                "error": f"scene descriptions skipped: {str(exc)[:160]}",
                            }
                        )
                check_source(abs_path, stamp)
                # Detect even a writer that restored size/mtime during the expensive stages.
                worked = (
                    sum(
                        summary[k]
                        for k in ("metadata", "scenes", "visuals", "embedded", "described")
                    )
                    > work_before
                )
                if (changed or worked) and await checksum_file(abs_path) != checksum:
                    raise RuntimeError("Source content changed during analysis; retry Analyze")
                await conn.execute(
                    f"UPDATE files SET analysis_revision=?,xxhash=?,verified_at={NOW_SQL},error=NULL WHERE id=?",
                    (revision, checksum, file_id),
                )
                for stage in ("metadata", "scenes", "visuals", "embeddings", "descriptions"):
                    exists = await (
                        await conn.execute(
                            "SELECT 1 FROM analysis_stages WHERE file_id=? AND stage=?",
                            (file_id, stage),
                        )
                    ).fetchone()
                    if not exists and await stage_present(conn, file_id, stage):
                        # Adoption records uncertainty; migration never certifies old evidence.
                        model = {
                            "embeddings": MODEL_ID,
                            "descriptions": get_settings().planner_model_id,
                        }.get(stage)
                        await record_stage(conn, file_id, stage, revision, model, legacy=True)
                await conn.commit()
            except Exception as exc:
                await conn.rollback()
                await conn.execute(
                    "UPDATE files SET error=? WHERE id=?",
                    (f"{type(exc).__name__}: {exc}"[:500], file_id),
                )
                await conn.commit()
                summary["errors"].append({"file": rel_path, "error": str(exc)[:200]})
            await ctx.progress(
                done=index + 1, message=f"[{index + 1}/{total}] {rel_path}", force=True
            )
        summary["coverage"] = await analysis_coverage(conn)
        if summary["errors"]:
            details = "; ".join(entry["error"] for entry in summary["errors"][:3])
            raise JobFailedWithResult(
                f"Analysis incomplete: {details}. Successful cached stages were retained; "
                "correct the problem and retry Analyze.",
                summary,
            )
        return summary
    finally:
        await conn.close()


async def analysis_coverage(conn: Any) -> dict:
    """Stored row coverage for current source revisions, including reused knowledge."""
    files = await (await conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(analysis_revision=source_revision "
        "AND xxhash IS NOT NULL),0) AS current, "
        "COALESCE(SUM(error IS NOT NULL),0) AS failed FROM files"
    )).fetchone()
    scenes = await (await conn.execute(
        "SELECT COUNT(*) AS total, COUNT(e.scene_id) AS embedded, "
        "COUNT(d.scene_id) AS described FROM scenes s JOIN files f ON f.id=s.file_id "
        "LEFT JOIN embeddings e ON e.scene_id=s.id "
        "LEFT JOIN scene_descriptions d ON d.scene_id=s.id "
        "WHERE f.analysis_revision=f.source_revision AND f.xxhash IS NOT NULL"
    )).fetchone()
    return {
        "files_total": files["total"], "files_current": files["current"],
        "files_failed": files["failed"], "scenes_current": scenes["total"],
        "scenes_embedded": scenes["embedded"], "scenes_described": scenes["described"],
    }


async def _describe_file_scenes(conn: Any, cache_dir: Path, file_id: int, holder: dict) -> int:
    pending = await (
        await conn.execute(
            "SELECT 1 FROM scenes s WHERE s.file_id=? AND NOT EXISTS "
            "(SELECT 1 FROM scene_descriptions d WHERE d.scene_id=s.id) LIMIT 1",
            (file_id,),
        )
    ).fetchone()
    if not pending:
        return 0
    provider: LLMProvider = holder.get("provider") or get_provider()
    model: str | None = holder.get("vision_model")
    if not model:
        settings = get_settings()
        model = await provider.resolve_model(settings.planner_model_id, prefer_vision=True)
        holder["vision_model"] = model
    result = await describe_pending_scenes(conn, provider, model, cache_dir, file_id=file_id)
    if result["failed"]:
        raise ProviderError(f"{result['failed']} scene descriptions failed; retry Analyze")
    return int(result["described"])


async def _embed_file_scenes(conn: Any, cache_dir: Path, file_id: int) -> tuple[int, bool]:
    cur = await conn.execute(
        "SELECT s.id, sf.path FROM scenes s "
        "JOIN scene_frames sf ON sf.scene_id = s.id AND sf.role = 'primary' "
        "WHERE s.file_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM embeddings e WHERE e.scene_id = s.id AND e.model_id = ?)",
        (file_id, MODEL_ID),
    )
    pending = await cur.fetchall()
    if not pending:
        return 0, False

    try:
        embedder = await run_blocking(Embedder.get)
    except ImportError:
        return 0, True

    paths = [str(cache_dir / row["path"]) for row in pending]
    vectors = await run_blocking(embedder.encode_images, paths)
    if len(vectors) != len(pending):
        raise RuntimeError("Embedding provider returned an incomplete batch")
    for row, vector in zip(pending, vectors):
        scene_id = row["id"]
        await conn.execute("DELETE FROM scene_tags WHERE scene_id=?", (scene_id,))
        await conn.execute(
            "INSERT OR REPLACE INTO embeddings(scene_id, vector, model_id) VALUES (?, ?, ?)",
            (scene_id, vector_to_blob(vector), MODEL_ID),
        )
        for tag, tag_score in await run_blocking(score_tags, vector):
            await conn.execute(
                "INSERT OR REPLACE INTO scene_tags(scene_id, tag, score) VALUES (?, ?, ?)",
                (scene_id, tag, tag_score),
            )
    await conn.commit()
    return len(pending), False
