from ..core.tasks import run_blocking

"""Upgrade orientation without discarding reusable scene boundaries or source IDs."""


from .images import display_image_metadata_sync
from .metadata import probe_media_sync
from .provenance import CONTRACTS, archive_knowledge, check_source, record_stage


async def ensure_orientation(conn, file_id, path, kind, revision, stamp):
    stage = await (
        await conn.execute(
            "SELECT * FROM analysis_stages WHERE file_id=? AND stage='orientation'", (file_id,)
        )
    ).fetchone()
    if (
        stage
        and stage["contract_version"] == CONTRACTS["orientation"]
        and stage["input_revision"] == revision
    ):
        return
    previous = await (
        await conn.execute("SELECT rotation FROM files WHERE id=?", (file_id,))
    ).fetchone()
    if kind == "photo":
        meta = await run_blocking(display_image_metadata_sync, path)
        width, height = meta["size"]
        rotation = 0
        affected = (
            meta["orientation"] != 1
            or bool(previous[0])
            or path.suffix.lower() in {".heic", ".heif"}
        )
    else:
        meta = await run_blocking(probe_media_sync, path)
        width, height, rotation = meta["width"], meta["height"], meta["rotation"]
        affected = bool(rotation or previous[0])
    check_source(path, stamp)
    if affected:
        exists = await (
            await conn.execute(
                "SELECT 1 FROM scenes WHERE file_id=? LIMIT 1",
                (file_id,),
            )
        ).fetchone()
        if exists:
            await archive_knowledge(conn, file_id, "Display orientation contract upgraded")
        for table in ("scene_frames", "embeddings", "scene_tags", "scene_descriptions"):
            await conn.execute(
                f"DELETE FROM {table} WHERE scene_id IN (SELECT id FROM scenes WHERE file_id=?)",
                (file_id,),
            )
        await conn.execute("UPDATE scenes SET rep_frame_path=NULL WHERE file_id=?", (file_id,))
        for dependent in ("visuals", "embeddings", "descriptions"):
            await conn.execute(
                "DELETE FROM analysis_stages WHERE file_id=? AND stage=?", (file_id, dependent)
            )
    await conn.execute(
        "UPDATE files SET width=?,height=?,rotation=? WHERE id=?",
        (width, height, rotation, file_id),
    )
    await record_stage(conn, file_id, "orientation", revision)
    await conn.commit()
