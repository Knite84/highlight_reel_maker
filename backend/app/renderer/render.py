import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import get_settings
from ..core.db import connect, migrate_project
from ..core.paths import host_path, local_path
from ..core.processes import run_process
from ..pipeline.metadata import checksum_file, parse_probe_data
from ..pipeline.provenance import mark_source_edits
from ..planner.schemas import EditPlan
from .encode import canvas_for, resolve_encoder
from .filtergraph import build_render_args
from .photo_crop import compute_focus_points

logger = logging.getLogger(__name__)


async def probe_media(path: Path) -> dict:
    result = await run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-1000:]}")
    return parse_probe_data(json.loads(result.stdout))


async def mark_edit_failed(db_path: Path, edit_id: int, message: str) -> None:
    conn = await connect(db_path)
    try:
        row = await (
            await conn.execute("SELECT render_path FROM edits WHERE id=?", (edit_id,))
        ).fetchone()
        previous_exists = False
        if row and row["render_path"]:
            try:
                previous_exists = local_path(row["render_path"]).is_file()
            except ValueError:
                pass
        await conn.execute(
            "UPDATE edits SET status=?,error=? WHERE id=?",
            ("rendered" if previous_exists else "failed", message[:1000], edit_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def render_job_handler(ctx: Any, payload: dict) -> dict:
    db_path = local_path(payload["db_path"])
    edit_id = int(payload["edit_id"])
    temporary_files: list[Path] = []
    try:
        return await _render_attempt(ctx, payload, db_path, edit_id, temporary_files)
    except BaseException as exc:
        message = (
            "Render interrupted/cancelled; retry when ready"
            if isinstance(exc, asyncio.CancelledError)
            else str(exc)
        )
        await mark_edit_failed(db_path, edit_id, message)
        raise
    finally:
        for path in temporary_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary render artifact %s", path)


async def _render_attempt(ctx, payload, db_path, edit_id, temporary_files):
    media_root = local_path(payload["media_root"]).resolve()
    export_dir = local_path(payload["export_dir"])
    export_dir.mkdir(parents=True, exist_ok=True)
    profile = payload["profile"]
    attempt = uuid4().hex
    output_path = export_dir / f"edit_{edit_id}_{profile}_{attempt}.mp4"
    temporary_output = export_dir / f".{attempt}.partial.mp4"
    temporary_files.append(temporary_output)
    conn = await connect(db_path)
    try:
        await migrate_project(conn)
        row = await (
            await conn.execute("SELECT plan_json,stale_sources FROM edits WHERE id=?", (edit_id,))
        ).fetchone()
        if row is None:
            raise RuntimeError(f"edit {edit_id} not found")
        if json.loads(row["stale_sources"]):
            raise RuntimeError(
                "Plan source changed or was removed; Analyze then Generate a new plan. Previous exports remain downloadable."
            )
        await conn.execute("UPDATE edits SET status='rendering',error=NULL WHERE id=?", (edit_id,))
        await conn.commit()
        plan = EditPlan.model_validate(json.loads(row["plan_json"]))
        photos = await (
            await conn.execute(
                "SELECT rel_path,COALESCE(rotation,0) AS rotation FROM files WHERE kind='photo'"
            )
        ).fetchall()
        image_rels = frozenset(row["rel_path"] for row in photos)
        rotations = {row["rel_path"]: int(row["rotation"]) for row in photos}
        known_sources = {
            row["rel_path"]: row["xxhash"]
            for row in await (await conn.execute("SELECT rel_path,xxhash FROM files")).fetchall()
            if row["xxhash"]
        }
    finally:
        await conn.close()

    ctx.check_cancelled()
    sources = {}
    no_audio_rels = set(image_rels)
    hdr_rels = set()
    for rel in sorted({clip.rel_path for clip in plan.clips}):
        source = (media_root / rel).resolve()
        if not source.is_relative_to(media_root) or not source.is_file():
            raise RuntimeError(f"Source missing or outside media folder: {rel}")
        meta = await probe_media(source)
        if not meta["has_audio"]:
            no_audio_rels.add(rel)
        if meta["is_hdr"]:
            hdr_rels.add(rel)
        sources[rel] = source
        ctx.check_cancelled()
    focus = {}
    photo_sources = {rel: path for rel, path in sources.items() if rel in image_rels}
    if photo_sources:
        focus = await asyncio.to_thread(compute_focus_points, photo_sources, rotations)
    await _verify_sources(db_path, sources, known_sources)
    ctx.check_cancelled()
    encoder, flags = await resolve_encoder(profile)
    width, height = canvas_for(profile)
    title_file = None
    if plan.title:
        title_file = export_dir / f".{attempt}.title.txt"
        temporary_files.append(title_file)
        title_file.write_text(plan.title.text, encoding="utf-8")
    total_seconds = 0.0

    async def progress(line):
        if line.startswith("out_time_ms="):
            try:
                done = int(float(line.split("=", 1)[1]) / 1_000_000)
            except ValueError:
                return
            await ctx.progress(done=min(done, int(total_seconds)), message=f"Rendering {done}s")

    # Docker Vulkan/libplacebo produced silent block corruption on a real HDR
    # source despite a successful exit. Use the portable CPU tone mapper there;
    # NVENC still performs the final encode. Keep native capability fallback.
    engines = (
        ["libplacebo", "zscale"] if hdr_rels and not get_settings().container_mode else ["zscale"]
    )
    for engine in engines:
        args, total_seconds = build_render_args(
            plan,
            sources,
            canvas_w=width,
            canvas_h=height,
            encoder=encoder,
            encoder_flags=flags,
            output_path=temporary_output,
            title_textfile=Path(title_file.name) if title_file else None,
            image_rels=image_rels,
            no_audio_rels=no_audio_rels,
            hdr_rels=hdr_rels,
            hdr_engine=engine,
            photo_rotations=rotations,
            photo_focus=focus,
        )
        await ctx.progress(
            done=0, total=max(int(total_seconds), 1), message="Rendering", force=True
        )
        result = await run_process(args, cwd=export_dir, timeout=1800, on_line=progress)
        if result.returncode == 0:
            break
        capability_error = any(
            term in result.stderr.lower()
            for term in (
                "vulkan",
                "libplacebo",
                "device creation",
                "no such filter",
                "not implemented",
            )
        )
        if engine == "libplacebo" and hdr_rels and capability_error:
            logger.warning("HDR libplacebo unavailable; retrying zscale: %s", result.stderr[-1000:])
            continue
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    else:
        raise RuntimeError("No usable HDR rendering path")

    ctx.check_cancelled()
    await _verify_sources(db_path, sources, known_sources)
    if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
        raise RuntimeError("Render produced no nonempty output")
    measured = await probe_media(temporary_output)
    duration = measured["duration_sec"]
    if duration is None or abs(duration - total_seconds) > 0.15:
        raise RuntimeError(
            f"Output duration verification failed: expected {total_seconds}s, got {duration}s"
        )
    if measured["width"] != width or measured["height"] != height or not measured["has_audio"]:
        raise RuntimeError("Output stream/resolution verification failed")
    ctx.check_cancelled()
    # Each attempt gets a fresh final name: an interrupted replacement never truncates the last MP4.
    temporary_output.replace(output_path)
    conn = await connect(db_path)
    try:
        await conn.execute(
            "UPDATE edits SET status='rendered',render_path=?,error=NULL,rendered_duration_sec=?,"
            "downloaded_at=NULL,rendered_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (host_path(output_path, get_settings()), duration, edit_id),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {
        "edit_id": edit_id,
        "output": str(output_path),
        "duration_sec": duration,
        "encoder": encoder,
        "profile": profile,
    }


async def _verify_sources(db_path, sources, known_sources):
    for rel, source in sources.items():
        expected = known_sources.get(rel)
        if expected and await checksum_file(source) != expected:
            conn = await connect(db_path)
            try:
                await mark_source_edits(conn, rel)
                await conn.execute(
                    "UPDATE files SET source_revision=source_revision+1, "
                    "error='Source changed; retry Analyze' WHERE rel_path=? "
                    "AND source_revision=analysis_revision",
                    (rel,),
                )
                await conn.commit()
            finally:
                await conn.close()
            raise RuntimeError(
                "Plan source content changed; Analyze then Generate a new plan. Previous exports remain downloadable."
            )
