import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import repos
from app.core.config import Settings
from app.core.db import REGISTRY_SCHEMA, connect, migrate, migrate_project
from app.core.paths import UnsharedPath, host_path, local_path, source_path
from app.core.processes import ProcessResult, run_process
from app.jobs.bus import EventBus
from app.jobs.recovery import recover_interrupted
from app.jobs.worker import JobFailedWithResult, JobRunner
from app.renderer import render


def test_path_translation_preserves_windows_identity_and_rejects_escape(tmp_path):
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    settings = Settings(
        _env_file=None,
        container_mode=True,
        path_mappings={r"Z:\Media": str(mounted), str(tmp_path): str(tmp_path)},
        media_mounts={r"Z:\Media": str(mounted)},
    )
    assert local_path(r"z:\MEDIA\sub\photo.jpg", settings) == mounted / "sub" / "photo.jpg"
    assert host_path(mounted / "sub" / "photo.jpg", settings) == r"Z:\Media\sub\photo.jpg"
    assert source_path(r"Z:\Media", settings) == mounted.resolve()
    for value in (r"Z:\MediaElse\photo.jpg", r"Z:\Media\..\private", str(tmp_path / "private")):
        with pytest.raises(UnsharedPath):
            source_path(value, settings)


def test_subprocess_drains_large_stderr_without_deadlock():
    async def run():
        result = await run_process(
            [sys.executable, "-c", "import sys;sys.stderr.write('x'*2000000);print('finished')"],
            timeout=10,
        )
        assert result.returncode == 0 and "finished" in result.stdout
        assert len(result.stderr) == 64 * 1024

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "timeout"])
def test_subprocess_reaped_on_cancel_or_timeout(monkeypatch, action):
    async def run():
        original = asyncio.create_subprocess_exec
        started = asyncio.Event()
        children = []

        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            run_process(
                [sys.executable, "-c", "import time;time.sleep(60)"],
                timeout=0.2 if action == "timeout" else 10,
            )
        )
        await started.wait()
        if action == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if action == "cancel" else TimeoutError):
            await task
        assert children[0].returncode is not None

    asyncio.run(run())


def test_worker_shutdown_cancels_current_and_queued_and_can_restart(tmp_path):
    async def run():
        registry = await connect(tmp_path / "registry.db")
        await migrate(registry, REGISTRY_SCHEMA)
        runner = JobRunner(registry, EventBus())
        started = asyncio.Event()

        async def wait(ctx, payload):
            started.set()
            await asyncio.Future()

        runner.start()
        current = await runner.enqueue(project_id=None, kind="test", handler=wait)
        await started.wait()
        queued = await runner.enqueue(project_id=None, kind="test", handler=wait)
        await runner.stop()
        assert (await repos.get_job(registry, current))["status"] == "cancelled"
        assert (await repos.get_job(registry, queued))["status"] == "cancelled"
        restarted = JobRunner(registry, EventBus())
        restarted.start()

        async def complete(ctx, payload):
            return {"ready": True}

        fresh = await restarted.enqueue(project_id=None, kind="test", handler=complete)
        await restarted.wait_until_idle()
        assert (await repos.get_job(registry, fresh))["status"] == "done"
        await restarted.stop()
        await registry.close()

    asyncio.run(run())


def test_cancel_running_job_does_not_kill_worker(tmp_path):
    async def run():
        conn = await connect(tmp_path / "registry.db")
        await migrate(conn, REGISTRY_SCHEMA)
        runner = JobRunner(conn, EventBus())
        started = asyncio.Event()

        async def wait(ctx, payload):
            started.set()
            await asyncio.Future()

        runner.start()
        job = await runner.enqueue(project_id=None, kind="test", handler=wait)
        await started.wait()
        assert runner.cancel(job)
        await runner.wait_until_idle()
        assert (await repos.get_job(conn, job))["status"] == "cancelled"
        assert not runner._task.done()
        await runner.stop()
        await conn.close()

    asyncio.run(run())


def test_partial_analysis_result_is_retained_on_failed_job(tmp_path):
    async def run():
        conn = await connect(tmp_path / "registry.db")
        await migrate(conn, REGISTRY_SCHEMA)
        runner = JobRunner(conn, EventBus())

        async def partial(ctx, payload):
            raise JobFailedWithResult("Start Unsloth and retry; cached stages retained", {"visuals": 3})

        runner.start()
        job_id = await runner.enqueue(project_id=None, kind="analyze", handler=partial)
        await runner.wait_until_idle()
        job = await repos.get_job(conn, job_id)
        assert job["status"] == "failed" and "Unsloth" in job["error"]
        assert json.loads(job["result_json"])["visuals"] == 3
        await runner.stop()
        await conn.close()
    asyncio.run(run())


def test_recovery_reconciles_orphan_jobs_and_preserves_last_export(tmp_path):
    async def run():
        settings = Settings(_env_file=None, data_root=tmp_path, projects_root=tmp_path / "Projects")
        registry = await connect(settings.registry_db_path)
        await migrate(registry, REGISTRY_SCHEMA)
        await repos.create_project(registry, name="Trip", slug="trip", media_path=str(tmp_path))
        for status in ("queued", "running"):
            await registry.execute(
                "INSERT INTO jobs(project_id,kind,status) VALUES (1,'render',?)", (status,)
            )
        await registry.commit()
        db = settings.projects_root / "trip" / "cache" / "db.sqlite"
        conn = await connect(db)
        await migrate_project(conn)
        previous = tmp_path / "previous.mp4"
        previous.write_bytes(b"retained export")
        for path in (str(previous), None):
            await conn.execute(
                "INSERT INTO edits(prompt,target_duration_sec,status,plan_json,render_path) "
                "VALUES ('p',4,'rendering','{}',?)",
                (path,),
            )
        await conn.commit()
        await recover_interrupted(registry, settings)
        assert all(row["status"] == "failed" for row in await repos.list_jobs(registry))
        rows = await (await conn.execute("SELECT status,error FROM edits ORDER BY id")).fetchall()
        assert [r["status"] for r in rows] == ["rendered", "failed"]
        assert all("Interrupted" in r["error"] for r in rows)
        assert previous.read_bytes() == b"retained export"
        await conn.close()
        await registry.close()

    asyncio.run(run())


class Context:
    def __init__(self):
        self.updates = []

    def check_cancelled(self):
        pass

    async def progress(self, **kwargs):
        self.updates.append(kwargs)


async def seed_render(tmp_path, *, previous=True):
    db = tmp_path / "cache" / "db.sqlite"
    conn = await connect(db)
    await migrate_project(conn)
    old = tmp_path / "previous.mp4"
    if previous:
        old.write_bytes(b"last good export")
    plan = {
        "prompt": "test",
        "target_duration_sec": 4,
        "clips": [{"rel_path": "source.mp4", "start_sec": 0, "end_sec": 4}],
        "title": {"text": "Title", "subtitle": "Subtitle"},
    }
    await conn.execute(
        "INSERT INTO edits(prompt,target_duration_sec,status,plan_json,render_path) "
        "VALUES ('test',4,?,?,?)",
        ("rendered" if previous else "planned", json.dumps(plan), str(old) if previous else None),
    )
    await conn.commit()
    await conn.close()
    return {
        "db_path": str(db),
        "media_root": str(tmp_path),
        "export_dir": str(tmp_path / "exports"),
        "edit_id": 1,
        "profile": "proxy",
    }, old


@pytest.mark.parametrize(
    "failure", ["missing", "probe", "graph", "spawn", "encode", "verify", "cancel"]
)
def test_render_failure_boundary_preserves_last_good_export(tmp_path, monkeypatch, failure):
    async def run():
        payload, old = await seed_render(tmp_path)
        if failure != "missing":
            (tmp_path / "source.mp4").write_bytes(b"source")

        async def probe(path):
            if failure == "probe" or (
                failure == "verify" and path.suffix == ".mp4" and path.name != "source.mp4"
            ):
                raise RuntimeError("probe failed")
            return {
                "has_audio": True,
                "is_hdr": False,
                "width": 1280,
                "height": 720,
                "duration_sec": 4,
            }

        async def encoder(profile):
            return "libx264", []

        def graph(*args, **kwargs):
            if failure == "graph":
                raise ValueError("graph failed")
            title = tmp_path / "exports" / kwargs["title_textfile"]
            assert title.read_text() == "Title"
            return ["ffmpeg", str(kwargs["output_path"])], 4

        async def process(args, **kwargs):
            if failure == "spawn":
                raise OSError("spawn failed")
            if failure == "cancel":
                raise asyncio.CancelledError
            Path(args[-1]).write_bytes(b"new attempt")
            return ProcessResult(1 if failure == "encode" else 0, "", "encode failed")

        monkeypatch.setattr(render, "probe_media", probe)
        monkeypatch.setattr(render, "resolve_encoder", encoder)
        monkeypatch.setattr(render, "build_render_args", graph)
        monkeypatch.setattr(render, "run_process", process)
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else Exception):
            await render.render_job_handler(Context(), payload)
        conn = await connect(Path(payload["db_path"]))
        row = await (await conn.execute("SELECT * FROM edits WHERE id=1")).fetchone()
        await conn.close()
        assert row["status"] == "rendered" and row["error"]
        assert row["render_path"] == str(old)
        assert old.read_bytes() == b"last good export"
        assert not list((tmp_path / "exports").glob("*.partial.mp4"))
        assert not list((tmp_path / "exports").glob("*.title.txt"))

    asyncio.run(run())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_real_render_handler_validates_title_output_and_progress(tmp_path, monkeypatch):
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=4:r=30",
            "-c:v",
            "libx264",
            str(tmp_path / "source.mp4"),
        ],
        check=True,
        capture_output=True,
    )

    async def encoder(profile):
        return "libx264", ["-preset", "ultrafast", "-crf", "30"]

    monkeypatch.setattr(render, "resolve_encoder", encoder)

    async def run():
        payload, old = await seed_render(tmp_path)
        ctx = Context()
        result = await render.render_job_handler(ctx, payload)
        assert Path(result["output"]).is_file()
        assert result["duration_sec"] == pytest.approx(4, abs=0.15)
        assert old.read_bytes() == b"last good export"
        assert any(update.get("done", 0) > 0 for update in ctx.updates)
        conn = await connect(Path(payload["db_path"]))
        row = await (await conn.execute("SELECT * FROM edits WHERE id=1")).fetchone()
        assert row["status"] == "rendered" and row["error"] is None
        assert row["render_path"] == result["output"]
        await conn.close()

    asyncio.run(run())
