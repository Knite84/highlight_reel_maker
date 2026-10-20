# ReelMaker — Local Media Understanding and Highlight Reels

Builds a reusable knowledge base from Windows photo/video folders, then plans and
renders highlight reels. The application runs in **Docker Desktop**; source media,
project knowledge, model caches, and finished videos remain in Windows folders.
Unsloth runs separately on Windows and is managed by you. SigLIP embeddings/tags
and FFmpeg processing run with the backend in Docker.

## Start and stop

Requirements: Docker Desktop with Linux containers/WSL 2, a compatible NVIDIA driver
for the default GPU configuration, and the existing `uv` installation for one-time
host-folder configuration. See the [Docker guide](docs/docker.md) for details.

Keep your provider URL/key/model in `backend/.env` (copy `backend/.env.example` on a
new installation). Start Unsloth and load your model before Analyze or Generate.
Nothing in Compose starts or containerizes Unsloth.

From the project root:

```powershell
# One-time setup: preserve current Windows storage and share existing collections
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -Backup

# Build, start, and wait for healthy services
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1

# Stop the application and free its port
powershell -ExecutionPolicy Bypass -File scripts\stop-dev.ps1
```

Open **http://localhost:5173**. In Docker Desktop, use the **reelmaker** group to
see logs or start/stop the application. Only the web port is published; backend
port 8000 is internal. From the `scripts` directory the shortcuts are simply
`.\start-dev.ps1` and `.\stop-dev.ps1`.

Equivalent commands from the repository root:

```powershell
docker compose up -d --build --wait
docker compose stop
docker compose ps
docker compose logs --tail 100 app web
```

Start rebuilds changed code; use `start-dev.ps1 -SkipBuild` when no rebuild is needed.
Stopping/removing containers preserves host files. Start/Stop no longer depend on
temporary PID records or kill arbitrary processes that own a port.

## Files and inference

| Resource | Location |
|---|---|
| Source photos/videos | Your Windows folders, shared read-only |
| Registry, analysis, thumbnails, plans, exports | Existing `%LOCALAPPDATA%\ReelMaker` / configured host roots |
| SigLIP/YuNet model caches | Host data folder, accessed by the container |
| Unsloth model/server | Windows, outside Docker |
| Downloaded MP4 copy | Browser-selected location, usually Windows Downloads |

The generated `compose.override.yaml` is machine-specific and gitignored. Existing
Windows paths are translated when accessing files; migration does not reanalyze media
or rewrite cached knowledge. The startup guard requires host mounts for persistent stores.

Share an additional source folder or choose a different host export folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -MediaRoot 'D:\Photos'
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -ExportRoot "$env:USERPROFILE\Videos\ReelMaker"
docker compose up -d --wait
```

**Browse** lists shared Windows folders. Finished videos default to each project's
existing `exports` folder; an optional export root receives project subfolders.
Download still saves a browser copy. See [storage and recovery details](docs/docker.md).

## Use

1. Create a project from a shared Windows folder.
2. **Analyze** builds reusable scenes, measurements, embeddings/tags, and descriptions.
   Missing/inaccessible folders fail inventory without deleting cached knowledge.
   Changed sources refresh their dependent analysis; unchanged files reuse it.
   **Verify file contents** also detects edits that preserve size/modification time.
   See [cache reuse and recovery](docs/cache-provenance.md) for legacy-data behavior.
   HEIC initializes automatically. New vision frame inputs follow recorded timestamps;
   older frames without timestamps use one representative for new descriptions.
   Photos now use one display-orientation decoder for thumbnails, face framing and
   rendering, including EXIF mirrors. The first Analyze refreshes affected legacy
   visual analysis while preserving scene IDs and historical knowledge.
3. **Generate plan** uses your prompt and requested duration; **Render** executes the plan.
   Your duration overrides the model's suggested target before fitting. This applies
   to newly generated plans; saved plans keep their existing timing. Sequencing defaults
   to chronology. Explicitly request “narrative order”, “reverse chronological order”,
   or flashbacks in the brief for another sequence; storytelling mood alone keeps chronology.
   Render opens a timing review showing long still holds, freezes, added filler and
   selection repairs before **Render this plan** starts encoding.
   New v2 plans bind clips to scene IDs and source hashes, so selecting a later
   scene from the same video retains that interval. Older v1 plans remain readable;
   ambiguous references require a new plan. See [media contracts](docs/media-contracts.md).
4. Download the resulting MP4. The current UI requests the 720p proxy profile; final-export
   UI and preview correctness remain audit follow-ups.

Missing Unsloth affects analysis/planning, with retry guidance. Browsing and rendering
existing plans remain usable. Archive retains knowledge/exports; Restore recovers the
same project, optionally renamed. Permanently purge is separate and checks source overlap.
Queued/running work blocks archive/purge. Interrupted jobs reconcile on startup, and a
failed render preserves the last successful MP4.
Plans referencing changed sources require Analyze and a new plan; existing exports
remain downloadable. Database migration creates an integrity-checked local snapshot.

Project job cards recover after reload/navigation and refresh on reconnect, with
polling as a fallback when live events are unavailable. Failed/cancelled operations
refresh results too. Cards show errors and offer Cancel for active work; cancellation
may wait for an in-progress model computation. Analyze reports stored scene coverage,
work completed in this run, and expandable issues. Retry Analyze after correcting
the issue; successful cached stages are reused. Older jobs keep their original summaries.
One operation per project may be queued/running. Other projects can queue up to 32
waiting jobs by default (`REELMAKER_JOB_QUEUE_LIMIT`). Busy projects return a conflict;
a full queue or unavailable worker returns retry guidance. Job-status write failures
pause new work and retry without rerunning handlers; Docker health reports worker
failure. Blocking thread cancellation waits for the current computation to return.
See [job and planning policy](docs/job-and-planning-policy.md) for API review tokens,
failure recovery, directive wording and limits.

## Contributor checks

```powershell
cd backend
uv run pytest
uv run ruff check .
cd ..\frontend
npm test
npm run build
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-docker-scripts.ps1
```

Linux checks and the disposable Docker lifecycle test are documented in
[docs/docker.md](docs/docker.md). Real-model integration is separate (`pytest -m slow`).
Frontend contributor tests use Node 22.18+ (Node 22 in Docker) and existing development
dependencies; the web image runs them before building. No new test dependency is required.
Rebuild/recreate the backend after Python changes; the packaged app has no hot reload.

Read [AGENTS.md](AGENTS.md), [lessons learned](docs/lessons_learned.md), and the
[stack inventory](docs/tech_stack.md) before making changes. Current handoff:
[status](docs/status.md). Audit and accepted work:
[remediation plan](docs/audit-remediation-plan-2026-10-20.md),
[architecture review](docs/architecture_review.md). Original intent:
[specification](spec.txt) and [original design](docs/implementation-plan.md).
