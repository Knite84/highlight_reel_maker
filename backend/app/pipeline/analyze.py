import asyncio
import json
from pathlib import Path
from typing import Any

from ..ai.embeddings import MODEL_ID, Embedder, score_tags, vector_to_blob
from ..ai.providers import LLMProvider, ProviderError, get_provider
from ..core.config import get_settings
from ..core.db import connect, migrate_project
from .describe import describe_pending_scenes
from .frames_cv import process_photo_async, process_video_async
from .metadata import checksum_file, extract_metadata
from .scan import scan_project
from .scenes import detect_scenes

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


async def _count_missing_visuals(conn: Any, file_id: int) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM scenes s "
        "LEFT JOIN scene_frames sf ON sf.scene_id = s.id AND sf.role = 'primary' "
        "WHERE s.file_id = ? AND sf.id IS NULL",
        (file_id,),
    )
    return int((await cur.fetchone())[0])


async def _store_scene_visuals(conn: Any, scene_id: int, entry: dict) -> None:
    metrics = entry["metrics"]
    await conn.execute("DELETE FROM scene_frames WHERE scene_id = ?", (scene_id,))
    await conn.execute(
        "INSERT INTO scene_frames(scene_id, role, path, blur, brightness, contrast, motion, "
        "stability, dominant_colors) VALUES (?, 'primary', ?, ?, ?, ?, ?, ?, ?)",
        (
            scene_id,
            entry["primary_rel"],
            metrics.get("blur"),
            metrics.get("brightness"),
            metrics.get("contrast"),
            metrics.get("motion"),
            metrics.get("stability"),
            json.dumps(metrics.get("dominant_colors", [])),
        ),
    )
    for secondary_rel in entry["secondary_rels"]:
        await conn.execute(
            "INSERT INTO scene_frames(scene_id, role, path) VALUES (?, 'secondary', ?)",
            (scene_id, secondary_rel),
        )
    await conn.execute(
        "UPDATE scenes SET rep_frame_path = ? WHERE id = ?", (entry["thumb_rel"], scene_id)
    )


async def analyze_job_handler(ctx: Any, payload: dict) -> dict:
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
        await scan_project(ctx, payload, conn)
        cur = await conn.execute(
            "SELECT id, rel_path, kind, size_bytes, mtime, xxhash, rotation, analyzed_at "
            "FROM files ORDER BY rel_path"
        )
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

                if row["analyzed_at"] is None or row["rotation"] is None:
                    checksum = row["xxhash"] or await checksum_file(abs_path)
                    meta = await extract_metadata(abs_path, kind)
                    await conn.execute(
                        "UPDATE files SET duration_sec=?, width=?, height=?, fps=?, codec=?, "
                        "camera_model=?, gps_lat=?, gps_lon=?, captured_at=?, rotation=?, "
                        f"xxhash=?, stage_version='meta.v1', analyzed_at={NOW_SQL}, error=NULL "
                        "WHERE id=?",
                        (
                            meta["duration_sec"], meta["width"], meta["height"], meta["fps"],
                            meta["codec"], meta["camera_model"], meta["gps_lat"], meta["gps_lon"],
                            meta["captured_at"], meta["rotation"], checksum, file_id,
                        ),
                    )
                    await conn.commit()
                    summary["metadata"] += 1

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
                        for start, end in spans:
                            await conn.execute(
                                "INSERT OR IGNORE INTO scenes(file_id, start_sec, end_sec, "
                                "stage_version) VALUES (?, ?, ?, 'scenes.v1')",
                                (file_id, start, end),
                            )
                        summary["scenes"] += len(spans)
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO scenes(file_id, start_sec, end_sec, stage_version)"
                        " VALUES (?, 0, 0, 'scenes.v1')",
                        (file_id,),
                    )
                await conn.commit()

                if await _count_missing_visuals(conn, file_id) > 0:
                    cur = await conn.execute(
                        "SELECT id, start_sec, end_sec FROM scenes WHERE file_id=? ORDER BY start_sec",
                        (file_id,),
                    )
                    scene_rows = await cur.fetchall()
                    rotation = meta_row["rotation"] or 0
                    if kind == "video":
                        spans = [(r["start_sec"], r["end_sec"]) for r in scene_rows]
                        entries = await process_video_async(abs_path, rotation, spans, cache_dir, file_id)
                    else:
                        entries = [await process_photo_async(abs_path, rotation, cache_dir, file_id)]
                    by_start = {r["start_sec"]: r["id"] for r in scene_rows}
                    for entry in entries:
                        await _store_scene_visuals(conn, by_start[entry["start"]], entry)
                        summary["visuals"] += 1
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

                if not descriptions_disabled:
                    try:
                        described = await _describe_file_scenes(
                            conn, cache_dir, file_id, provider_holder
                        )
                        summary["described"] += described
                    except ProviderError as exc:
                        descriptions_disabled = True
                        summary["errors"].append(
                            {
                                "file": "",
                                "error": f"scene descriptions skipped: {str(exc)[:160]}",
                            }
                        )
            except Exception as exc:
                await conn.execute(
                    "UPDATE files SET error=? WHERE id=?",
                    (f"{type(exc).__name__}: {exc}"[:500], file_id),
                )
                await conn.commit()
                summary["errors"].append({"file": rel_path, "error": str(exc)[:200]})
            await ctx.progress(
                done=index + 1, message=f"[{index + 1}/{total}] {rel_path}", force=True
            )
        return summary
    finally:
        await conn.close()


async def _describe_file_scenes(
    conn: Any, cache_dir: Path, file_id: int, holder: dict
) -> int:
    provider: LLMProvider = holder.get("provider") or get_provider()
    model: str | None = holder.get("vision_model")
    if not model:
        settings = get_settings()
        model = await provider.resolve_model(settings.planner_model_id, prefer_vision=True)
        holder["vision_model"] = model
    result = await describe_pending_scenes(conn, provider, model, cache_dir, file_id=file_id)
    return int(result["described"])


async def _embed_file_scenes(conn: Any, cache_dir: Path, file_id: int) -> tuple[int, bool]:
    try:
        embedder = Embedder.get()
    except ImportError:
        return 0, True

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

    paths = [str(cache_dir / row["path"]) for row in pending]
    vectors = await asyncio.to_thread(embedder.encode_images, paths)
    for row, vector in zip(pending, vectors):
        scene_id = row["id"]
        await conn.execute(
            "INSERT OR REPLACE INTO embeddings(scene_id, vector, model_id) VALUES (?, ?, ?)",
            (scene_id, vector_to_blob(vector), MODEL_ID),
        )
        for tag, tag_score in score_tags(vector):
            await conn.execute(
                "INSERT OR REPLACE INTO scene_tags(scene_id, tag, score) VALUES (?, ?, ?)",
                (scene_id, tag, tag_score),
            )
    await conn.commit()
    return len(pending), False
