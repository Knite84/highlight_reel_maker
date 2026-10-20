"""Isolated Compose lifecycle check. Never attaches the user's project/media stores."""

import hashlib
import json
import socket
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".docker-smoke"
PORT = 15173
CONFIG = WORK / "compose.yaml"


def docker(*args, capture=False):
    result = subprocess.run(
        ["docker", *args], cwd=ROOT, text=True, capture_output=capture, check=True
    )
    return result.stdout if capture else None


def compose(*args, capture=False):
    return docker("compose", "-p", "reelmaker-smoke", "-f", str(CONFIG), *args, capture=capture)


def wait_job(client, job_id, *, terminal=True, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = client.get(f"/api/jobs/{job_id}").raise_for_status().json()
        if terminal and row["status"] in ("done", "failed", "cancelled"):
            return row
        if not terminal and row["status"] == "running" and row["message"] == "Rendering":
            return row
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not reach expected state")


def seed_plan(project, *, long=False):
    plan = {
        "prompt": "Docker verification",
        "target_duration_sec": 1800 if long else 4,
        "clips": [
            {
                "rel_path": "source.mp4",
                "start_sec": 0,
                "end_sec": 4,
                "freeze_tail_sec": 1796 if long else 0,
            }
        ],
        "title": {"text": "Docker verification", "subtitle": "Host-backed output"},
    }
    db = f"/projects/{project['slug']}/cache/db.sqlite"
    code = (
        "import sqlite3,json;"
        f"c=sqlite3.connect({db!r});"
        f"p={json.dumps(plan)!r};"
        "c.execute(\"INSERT INTO edits(id,prompt,target_duration_sec,plan_json) VALUES(1,'smoke',4,?) "
        'ON CONFLICT(id) DO UPDATE SET plan_json=excluded.plan_json",(p,));c.commit();c.close()'
    )
    compose("exec", "-T", "app", "python", "-c", code)


def main():
    for directory in ("data", "projects", "media"):
        (WORK / directory).mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    mappings = {
        str(WORK / "data"): "/data",
        str(WORK / "projects"): "/projects",
        str(WORK / "media"): "/media/source-1",
    }
    binds = [
        {
            "type": "bind",
            "source": host.replace("\\", "/"),
            "target": target,
            "read_only": target.startswith("/media/"),
        }
        for host, target in mappings.items()
    ]
    config = {
        "name": "reelmaker-smoke",
        "services": {
            "app": {
                "image": "reelmaker-app",
                "init": True,
                "gpus": "all",
                "stop_grace_period": "45s",
                "volumes": binds,
                "environment": {
                    "REELMAKER_CONTAINER_MODE": "true",
                    "REELMAKER_DATA_ROOT": "/data",
                    "REELMAKER_PROJECTS_ROOT": "/projects",
                    "HF_HOME": "/data/hf",
                    "REELMAKER_UNSLOTH_BASE_URL": "http://host.docker.internal:65534",
                    "REELMAKER_PATH_MAPPINGS": json.dumps(mappings),
                    "REELMAKER_MEDIA_MOUNTS": json.dumps({str(WORK / "media"): "/media/source-1"}),
                    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility,video,graphics",
                },
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')",
                    ],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 20,
                },
            },
            "web": {
                "image": "reelmaker-web",
                "ports": [f"127.0.0.1:{PORT}:80"],
                "depends_on": {"app": {"condition": "service_healthy"}},
            },
        },
    }
    CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    Image.new("RGB", (320, 240), (40, 80, 180)).save(WORK / "media" / "photo.jpg")
    docker(
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={WORK / 'media'},target=/fixture",
        "reelmaker-app",
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=320x240:d=4:r=30",
        "-c:v",
        "libx264",
        "/fixture/source.mp4",
    )
    original_hash = hashlib.sha256((WORK / "media" / "source.mp4").read_bytes()).hexdigest()
    report = {}
    try:
        compose("up", "-d", "--wait", "--wait-timeout", "90")
        with httpx.Client(
            base_url=f"http://127.0.0.1:{PORT}", timeout=15, trust_env=False
        ) as client:
            assert client.get("/").status_code == 200
            roots = client.get("/api/system/folders").raise_for_status().json()
            assert roots["folders"][0]["path"] == str(WORK / "media")
            readonly = (
                "from pathlib import Path\n"
                "try: Path('/media/source-1/no-write-probe').write_text('must fail')\n"
                "except OSError: print('read-only enforced')\n"
                "else: raise RuntimeError('source mount is writable')"
            )
            assert "read-only enforced" in compose(
                "exec", "-T", "app", "python", "-c", readonly, capture=True
            )
            project = (
                client.post(
                    "/api/projects",
                    json={
                        "name": f"Docker smoke {time.time_ns()}",
                        "media_path": str(WORK / "media"),
                    },
                )
                .raise_for_status()
                .json()
            )
            assert wait_job(client, project["scan_job_id"])["status"] == "done"
            seed_plan(project)
            prefix = f"/api/projects/{project['id']}/plans/1"
            job_id = client.post(prefix + "/render", json={"profile": "proxy"}).json()["job_id"]
            job = wait_job(client, job_id)
            assert job["status"] == "done", job
            report["render"] = json.loads(job["result_json"])
            assert report["render"]["encoder"] == "h264_nvenc", report
            old = client.get(prefix + "/download").raise_for_status().content
            (WORK / "download.mp4").write_bytes(old)
            row = client.get(prefix).json()
            assert Path(row["render_path"]).is_file(), row
            report["host_export"] = row["render_path"]

            failed_plan = (
                client.post(
                    f"/api/projects/{project['id']}/plans",
                    json={"prompt": "Unsloth unavailable check", "target_duration_sec": 10},
                )
                .raise_for_status()
                .json()["job_id"]
            )
            failure = wait_job(client, failed_plan)
            assert failure["status"] == "failed" and "Unsloth" in failure["error"], failure
            report["unavailable_unsloth"] = "clear failed job; browsing/rendering remain available"

            seed_plan(project, long=True)
            running = client.post(prefix + "/render", json={"profile": "proxy"}).json()["job_id"]
            wait_job(client, running, terminal=False)
            compose("stop", "--timeout", "45")
            with socket.socket() as available:
                available.bind(("127.0.0.1", PORT))
            report["stop_releases_port"] = True
            compose("up", "-d", "--wait", "--wait-timeout", "90")
            assert wait_job(client, running)["status"] == "cancelled"
            assert client.get(prefix + "/download").raise_for_status().content == old
            report["graceful_restart"] = "cancelled attempt, previous MP4 retained"

            hard = client.post(prefix + "/render", json={"profile": "proxy"}).json()["job_id"]
            wait_job(client, hard, terminal=False)
            compose("kill", "-s", "SIGKILL", "app")
            compose("up", "-d", "--wait", "--wait-timeout", "90")
            recovered = wait_job(client, hard)
            assert recovered["status"] == "failed" and "Interrupted" in recovered["error"], (
                recovered
            )
            assert client.get(prefix + "/download").raise_for_status().content == old
            report["hard_restart"] = "orphan reconciled, previous MP4 retained"
            assert not list((WORK / "projects" / project["slug"] / "exports").glob("*.partial.mp4"))
            assert (
                hashlib.sha256((WORK / "media" / "source.mp4").read_bytes()).hexdigest()
                == original_hash
            )
            report["source_unchanged"] = True
        (WORK / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        compose("down")


if __name__ == "__main__":
    main()
