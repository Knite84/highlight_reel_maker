import asyncio
import threading

import aiosqlite
import pytest

from app.core import repos
from app.core.db import REGISTRY_SCHEMA, connect, migrate
from app.core.tasks import run_blocking
from app.jobs.bus import EventBus
from app.jobs.worker import JobRunner, ProjectUnavailable, QueueFull, RunnerUnavailable


async def registry_at(path):
    conn = await connect(path)
    await migrate(conn, REGISTRY_SCHEMA)
    return conn


def test_queued_event_is_published_before_running_with_increasing_revisions(tmp_path):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        bus = EventBus()
        events = bus.subscribe()
        runner = JobRunner(conn, bus)

        async def work(ctx, payload):
            return {"ok": True}

        runner.start()
        try:
            await runner.enqueue(project_id=None, kind="test", handler=work)
            await runner.wait_until_idle()
            rows = [events.get_nowait()["job"] for _ in range(events.qsize())]
            assert [row["status"] for row in rows] == ["queued", "running", "done"]
            assert [row["revision"] for row in rows] == [0, 1, 2]
        finally:
            await runner.stop()
            await conn.close()

    asyncio.run(run())


def test_cross_kind_admission_is_atomic(tmp_path):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        await repos.create_project(conn, name="P", slug="p", media_path=str(tmp_path))
        runner = JobRunner(conn, EventBus())
        gate = asyncio.Event()

        async def work(ctx, payload):
            await gate.wait()

        runner.start()
        try:
            results = await asyncio.gather(
                *[
                    runner.enqueue(project_id=1, kind=kind, handler=work)
                    for kind in ["scan", "analyze", "plan", "render"] * 5
                ],
                return_exceptions=True,
            )
            assert sum(isinstance(result, int) for result in results) == 1
            assert sum(isinstance(result, ProjectUnavailable) for result in results) == 19
            assert len(await repos.list_jobs(conn)) == 1
        finally:
            gate.set()
            await runner.stop()
            await conn.close()

    asyncio.run(run())


def test_queue_bound_rejects_without_creating_a_job(tmp_path):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        runner = JobRunner(conn, EventBus(), queue_limit=2)
        started = asyncio.Event()

        async def work(ctx, payload):
            started.set()
            await asyncio.Future()

        runner.start()
        try:
            await runner.enqueue(project_id=None, kind="test", handler=work)
            await started.wait()
            for _ in range(2):
                await runner.enqueue(project_id=None, kind="test", handler=work)
            with pytest.raises(QueueFull):
                await runner.enqueue(project_id=None, kind="test", handler=work)
            assert len(await repos.list_jobs(conn)) == 3
        finally:
            await runner.stop()
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["running", "done", "failed", "cancelled"])
def test_persistence_fault_pauses_then_recovers_without_replaying_handler(
    tmp_path, monkeypatch, phase
):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        runner = JobRunner(conn, EventBus())
        broken = True
        fault = asyncio.Event()
        calls = []
        original = repos.update_job

        async def update(conn, job_id, *, fields):
            if broken and fields.get("status") in (phase, "failed"):
                fault.set()
                raise aiosqlite.OperationalError("disk temporarily unavailable")
            return await original(conn, job_id, fields=fields)

        async def work(ctx, payload):
            calls.append(payload["name"])
            if phase == "failed" and payload["name"] == "first":
                raise ValueError("handler failure")
            if phase == "cancelled" and payload["name"] == "first":
                raise asyncio.CancelledError
            return {"published": True}

        monkeypatch.setattr(repos, "update_job", update)
        runner.start()
        try:
            first = await runner.enqueue(
                project_id=None, kind="test", handler=work, payload={"name": "first"}
            )
            second = await runner.enqueue(
                project_id=None, kind="test", handler=work, payload={"name": "second"}
            )
            await asyncio.wait_for(fault.wait(), 2)
            # Let the failure boundary install its deferred terminal write.
            await asyncio.sleep(0.02)
            assert not runner.readiness()["ok"]
            assert "second" not in calls
            with pytest.raises(RunnerUnavailable):
                await runner.enqueue(project_id=None, kind="test", handler=work)
            broken = False
            await runner.wait_until_idle(timeout=3)
            assert runner.readiness()["ok"]
            assert calls.count("first") == (0 if phase == "running" else 1)
            assert calls.count("second") == 1
            assert (await repos.get_job(conn, first))["status"] == (
                "failed" if phase == "running" else phase
            )
            assert (await repos.get_job(conn, second))["status"] == "done"
        finally:
            broken = False
            await runner.stop()
            await conn.close()

    asyncio.run(run())


def test_cancellation_retains_slot_until_thread_finishes(tmp_path):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        runner = JobRunner(conn, EventBus())
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocking():
            entered.set()
            release.wait(5)
            calls.append("thread finished")

        async def first(ctx, payload):
            await run_blocking(blocking)
            calls.append("must not publish after cancellation")

        async def second(ctx, payload):
            calls.append("second")

        runner.start()
        try:
            job = await runner.enqueue(project_id=None, kind="test", handler=first)
            while not entered.is_set():
                await asyncio.sleep(0.005)
            await runner.enqueue(project_id=None, kind="test", handler=second)
            assert runner.cancel(job)
            assert runner.cancel(job)  # Repeated requests must not cancel the thread barrier.
            await asyncio.sleep(0.02)
            assert calls == []
            assert (await repos.get_job(conn, job))["status"] == "running"
            release.set()
            await runner.wait_until_idle()
            assert calls == ["thread finished", "second"]
            assert (await repos.get_job(conn, job))["status"] == "cancelled"
        finally:
            release.set()
            await runner.stop()
            await conn.close()

    asyncio.run(run())


def test_health_and_admission_fail_closed_when_worker_stops(client):
    runner = client.app.state.runner
    client.portal.call(runner.stop)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["worker"]["ok"] is False
    response = client.post(
        "/api/projects",
        json={"name": "No worker", "media_path": str(client.app.state.settings.data_root)},
    )
    assert response.status_code == 503
    assert client.get("/api/projects").json() == []


def test_ambiguous_admission_write_failure_rejects_further_work_until_restart(
    tmp_path, monkeypatch
):
    async def run():
        conn = await registry_at(tmp_path / "registry.db")
        runner = JobRunner(conn, EventBus())

        async def broken_insert(*args, **kwargs):
            raise aiosqlite.OperationalError("cannot commit")

        async def never_run(ctx, payload):
            pytest.fail("unaccepted work must not execute")

        monkeypatch.setattr(repos, "insert_job", broken_insert)
        runner.start()
        try:
            with pytest.raises(RunnerUnavailable, match="persist job admission"):
                await runner.enqueue(project_id=None, kind="test", handler=never_run)
            assert not runner.readiness()["ok"]
            assert runner._queue.empty()
            with pytest.raises(RunnerUnavailable):
                await runner.enqueue(project_id=None, kind="test", handler=never_run)
        finally:
            await runner.stop()
            await conn.close()

    asyncio.run(run())


def test_provider_shutdown_failure_still_closes_registry(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    class Provider:
        async def close(self):
            raise RuntimeError("provider close failed")

    monkeypatch.setattr(main, "get_provider", Provider)
    app = main.create_app()
    with pytest.raises(RuntimeError, match="provider close failed"), TestClient(app):
        conn = app.state.registry

    async def query_closed():
        with pytest.raises(ValueError, match="no active connection"):
            await conn.execute("SELECT 1")

    asyncio.run(query_closed())


def test_concurrent_http_operations_share_the_same_admission_boundary(
    client, media_dir, monkeypatch
):
    import json
    from concurrent.futures import ThreadPoolExecutor

    from test_api import _wait_job

    from app.api import plans, projects
    from app.core.config import get_settings
    from app.core.db import project_db_path

    created = client.post(
        "/api/projects", json={"name": "Atomic", "media_path": str(media_dir)}
    ).json()
    assert _wait_job(client, created["scan_job_id"])["status"] == "done"

    async def seed():
        conn = await connect(project_db_path(get_settings().projects_root, created["slug"]))
        try:
            await conn.execute(
                "INSERT INTO edits(prompt,target_duration_sec,plan_json) VALUES('p',4,?)",
                (
                    json.dumps(
                        {
                            "prompt": "p",
                            "target_duration_sec": 4,
                            "clips": [
                                {"rel_path": "a.mp4", "start_sec": 0, "end_sec": 4},
                            ],
                        }
                    ),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(seed())

    async def wait(ctx, payload, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(projects, "scan_job_handler", wait)
    monkeypatch.setattr(projects, "analyze_job_handler", wait)
    monkeypatch.setattr(plans, "plan_job_handler", wait)
    monkeypatch.setattr(plans, "render_job_handler", wait)
    base = f"/api/projects/{created['id']}"
    requests = [
        (base + "/scan", {}),
        (base + "/analyze", {}),
        (base + "/plans", {"prompt": "p"}),
        (base + "/plans/1/render", {"profile": "proxy"}),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: client.post(item[0], json=item[1]), requests))
    assert sorted(response.status_code for response in results) == [202, 409, 409, 409]
    accepted = next(
        response.json()["job_id"] for response in results if response.status_code == 202
    )
    client.post(f"/api/jobs/{accepted}/cancel")
    assert _wait_job(client, accepted)["status"] == "cancelled"
