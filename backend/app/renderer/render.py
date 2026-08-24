import asyncio
import json
from pathlib import Path
from typing import Any

from ..core.db import connect, migrate_project
from ..pipeline.metadata import probe_media_sync
from ..planner.schemas import EditPlan
from .encode import canvas_for, resolve_encoder
from .filtergraph import build_render_args


def _write_title_textfile(export_dir: Path, edit_id: int, plan: EditPlan) -> Path | None:
    if plan.title is None:
        return None
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"edit_{edit_id}_title.txt"
    lines = [plan.title.text]
    if plan.title.subtitle:
        lines.append(plan.title.subtitle)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def render_job_handler(ctx: Any, payload: dict) -> dict:
    db_path = Path(payload["db_path"])
    media_root = Path(payload["media_root"])
    export_dir = Path(payload["export_dir"])
    export_dir.mkdir(parents=True, exist_ok=True)
    profile = payload["profile"]
    edit_id = int(payload["edit_id"])

    conn = await connect(db_path)
    try:
        await migrate_project(conn)
        cur = await conn.execute("SELECT plan_json FROM edits WHERE id = ?", (edit_id,))
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError(f"edit {edit_id} not found")
        cur = await conn.execute("SELECT rel_path, kind FROM files WHERE kind = 'photo'")
        image_rels = frozenset(r["rel_path"] for r in await cur.fetchall())
        await conn.execute(
            "UPDATE edits SET status='rendering', error=NULL WHERE id = ?", (edit_id,)
        )
        await conn.commit()
        plan = EditPlan.model_validate(json.loads(row["plan_json"]))
    finally:
        await conn.close()

    missing = [
        clip.rel_path for clip in plan.clips if not (media_root / clip.rel_path).is_file()
    ]
    if missing:
        raise RuntimeError(f"source files missing: {sorted(set(missing))[:3]}")

    sources: dict[str, Path] = {}
    for rel_path in sorted({clip.rel_path for clip in plan.clips}):
        source_path = media_root / rel_path
        await asyncio.to_thread(probe_media_sync, source_path)
        sources[rel_path] = source_path

    encoder, flags = await resolve_encoder(profile)
    canvas_w, canvas_h = canvas_for(profile)
    title_textfile_abs = _write_title_textfile(export_dir, edit_id, plan)
    title_textfile = title_textfile_abs.name if title_textfile_abs else None
    output_path = export_dir / f"edit_{edit_id}_{profile}.mp4"
    args, total_seconds = build_render_args(
        plan,
        sources,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        encoder=encoder,
        encoder_flags=flags,
        output_path=output_path,
        title_textfile=title_textfile,
        image_rels=image_rels,
    )

    await ctx.progress(done=0, total=max(int(total_seconds), 1), message="Rendering", force=True)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(export_dir),
    )

    async def _pump_progress() -> None:
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if line.startswith("out_time_ms="):
                try:
                    micros = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                done = int(micros / 1_000_000)
                await ctx.progress(done=done, message=f"Rendering {done}s/{int(total_seconds)}s")

    stderr_tail = ""
    progress_task = asyncio.create_task(_pump_progress())
    try:
        return_code = await proc.wait()
        await progress_task
        if proc.stderr is not None:
            raw_stderr = await proc.stderr.read()
            stderr_tail = raw_stderr.decode(errors="replace")[-600:]
        if return_code != 0:
            raise RuntimeError(f"ffmpeg failed ({return_code}): {stderr_tail}")
    except asyncio.CancelledError:
        proc.kill()
        raise

    if not output_path.is_file():
        raise RuntimeError(f"render produced no output; ffmpeg said: {stderr_tail}")

    conn = await connect(db_path)
    try:
        await conn.execute(
            "UPDATE edits SET status='rendered', render_path=?, error=NULL, "
            "rendered_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (str(output_path), edit_id),
        )
        await conn.commit()
    finally:
        await conn.close()

    return {
        "edit_id": edit_id,
        "output": str(output_path),
        "duration_sec": total_seconds,
        "encoder": encoder,
        "profile": profile,
    }
