import asyncio
from pathlib import Path

from app.core import repos
from app.core.db import REGISTRY_SCHEMA, connect, migrate


def test_registry_upgrade_preserves_job_and_orders_updates(tmp_path):
    async def run():
        conn = await connect(tmp_path / "registry.db")
        try:
            # Existing registries predate revision, including retained terminal results.
            await migrate(conn, REGISTRY_SCHEMA.replace("    revision INTEGER NOT NULL DEFAULT 0,\n", ""))
            job_id = await repos.insert_job(conn, project_id=1, kind="analyze", payload_json="{}")
            await conn.execute(
                "UPDATE jobs SET status='failed',result_json=?,error='offline' WHERE id=?",
                ('{"metadata":2}', job_id),
            )
            await conn.commit()
            await migrate(conn, REGISTRY_SCHEMA)
            old = await repos.get_job(conn, job_id)
            assert old["revision"] == 0
            assert old["result_json"] == '{"metadata":2}'
            assert old["error"] == "offline"
            first = await repos.update_job(conn, job_id, fields={"message": "one"})
            second = await repos.update_job(conn, job_id, fields={"message": "two"})
            assert first["revision"] == 1
            assert second["revision"] == 2
            await migrate(conn, REGISTRY_SCHEMA)
            assert (await repos.get_job(conn, job_id))["revision"] == 2
        finally:
            await conn.close()

    asyncio.run(run())


def test_project_snapshot_includes_old_active_jobs_beyond_history_window(tmp_path):
    async def run():
        conn = await connect(tmp_path / "registry.db")
        try:
            await migrate(conn, REGISTRY_SCHEMA)
            active = await repos.insert_job(conn, project_id=7, kind="analyze", payload_json="{}")
            for i in range(205):
                job_id = await repos.insert_job(conn, project_id=7, kind="plan", payload_json="{}")
                await repos.update_job(conn, job_id, fields={"status": "done"})
            other = await repos.insert_job(conn, project_id=8, kind="render", payload_json="{}")
            rows = await repos.list_jobs(conn, limit=200, project_id=7)
            assert len(rows) == 201
            assert active in {job["id"] for job in rows}
            assert other not in {job["id"] for job in rows}
            await repos.update_job(conn, active, fields={"status": "failed"})
            rows = await repos.list_jobs(conn, limit=200, project_id=7)
            assert len(rows) == 200
            assert active not in {job["id"] for job in rows}
        finally:
            await conn.close()

    asyncio.run(run())


def test_jobs_api_filters_projects_and_returns_revision(client, media_dir):
    created = [client.post("/api/projects", json={
        "name": name, "media_path": str(media_dir),
    }).json() for name in ("One", "Two")]
    response = client.get(f"/api/jobs?project_id={created[0]['id']}&limit=200")
    assert response.status_code == 200
    assert response.json()
    assert all(job["project_id"] == created[0]["id"] for job in response.json())
    assert all(isinstance(job["revision"], int) for job in response.json())


def test_analyze_committed_inventory_updates_counts_even_on_later_failure(client, media_dir, monkeypatch):
    import time

    from app.api import projects
    from app.jobs.worker import JobFailedWithResult
    from app.pipeline.scan import scan_project

    async def failed_analysis(ctx, payload, *, on_inventory):
        conn = await connect(Path(payload["db_path"]))
        try:
            inventory = await scan_project(ctx, payload, conn, on_inventory=on_inventory)
            raise JobFailedWithResult("Model offline; retry Analyze", {"inventory": inventory})
        finally:
            await conn.close()

    monkeypatch.setattr(projects, "analyze_job_handler", failed_analysis)
    created = client.post("/api/projects", json={"name": "Counts", "media_path": str(media_dir)}).json()

    def wait(job_id):
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "failed", "cancelled"):
                return job
            time.sleep(0.01)
        raise AssertionError("job did not finish")

    assert wait(created["scan_job_id"])["status"] == "done"
    (media_dir / "new.jpg").write_bytes(b"test")
    accepted = client.post(f"/api/projects/{created['id']}/analyze").json()
    job = wait(accepted["job_id"])
    assert job["status"] == "failed"
    assert "inventory" in job["result_json"]
    detail = client.get(f"/api/projects/{created['id']}").json()
    assert detail["photo_count"] == 2
    assert detail["video_count"] == 2
