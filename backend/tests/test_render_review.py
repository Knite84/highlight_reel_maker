import asyncio
import json

from test_api import _wait_job
from test_runtime_safety import Context, seed_render

from app.api import plans
from app.core.config import get_settings
from app.core.db import connect, project_db_path
from app.planner.review import render_review
from app.renderer.render import render_job_handler


def test_render_api_requires_current_review_for_tradeoffs_and_keeps_prior_download(
    client, media_dir, monkeypatch
):
    created = client.post(
        "/api/projects", json={"name": "Review", "media_path": str(media_dir)}
    ).json()
    assert _wait_job(client, created["scan_job_id"])["status"] == "done"
    settings = get_settings()
    db_path = project_db_path(settings.projects_root, created["slug"])
    previous = db_path.parent / "keep.mp4"
    previous.write_bytes(b"last export")
    saved = {
        "prompt": "legacy",
        "target_duration_sec": 10,
        "clips": [{"rel_path": "a.mp4", "start_sec": 0, "end_sec": 4, "freeze_tail_sec": 6}],
    }

    async def seed():
        conn = await connect(db_path)
        try:
            await conn.execute(
                "INSERT INTO edits(prompt,target_duration_sec,plan_json,status,render_path) VALUES('legacy',10,?,'rendered',?)",
                (json.dumps(saved), str(previous)),
            )
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(seed())
    received = []

    async def fake_render(ctx, payload):
        received.append(payload)
        return {"rendered": True}

    monkeypatch.setattr(plans, "render_job_handler", fake_render)
    base = f"/api/projects/{created['id']}/plans/1"
    rejected = client.post(base + "/render", json={"profile": "proxy"})
    assert rejected.status_code == 409
    assert "tradeoffs" in rejected.json()["detail"]
    assert received == []
    assert client.get(base + "/download").content == b"last export"
    review = client.get(base + "/render-review").json()
    assert any("Frozen end frames add 6.0s" in warning for warning in review["warnings"])
    assert (
        client.post(
            base + "/render", json={"profile": "final", "review_token": review["token"]}
        ).status_code
        == 409
    )
    accepted = client.post(
        base + "/render", json={"profile": "proxy", "review_token": review["token"]}
    )
    assert accepted.status_code == 202
    assert _wait_job(client, accepted.json()["job_id"])["status"] == "done"
    assert received[0]["review_token"] == review["token"]
    assert client.get(base).json()["plan"] == saved  # Legacy JSON remains unchanged.


def test_render_rechecks_review_at_execution_before_reading_media(tmp_path, monkeypatch):
    async def run():
        payload, previous = await seed_render(tmp_path)
        conn = await connect(tmp_path / "cache" / "db.sqlite")
        row = await (await conn.execute("SELECT plan_json FROM edits WHERE id=1")).fetchone()
        payload["review_token"] = (await render_review(conn, row["plan_json"], "proxy"))["token"]
        plan = json.loads(row["plan_json"])
        plan["clips"][0]["freeze_tail_sec"] = 2
        await conn.execute("UPDATE edits SET plan_json=? WHERE id=1", (json.dumps(plan),))
        await conn.commit()
        await conn.close()
        import pytest

        with pytest.raises(RuntimeError, match="changed after duration review"):
            await render_job_handler(Context(), payload)
        assert previous.read_bytes() == b"last good export"
        conn = await connect(tmp_path / "cache" / "db.sqlite")
        try:
            row = await (await conn.execute("SELECT status,error FROM edits WHERE id=1")).fetchone()
            assert row["status"] == "rendered"
            assert "review" in row["error"]
        finally:
            await conn.close()

    asyncio.run(run())
