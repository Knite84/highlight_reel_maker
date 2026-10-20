# Running ReelMaker with Docker Desktop

ReelMaker runs as the **reelmaker** Compose group: `web` serves the built Vue app
through Nginx, and `app` runs FastAPI, FFmpeg/ffprobe, exiftool, OpenCV, YuNet, and
CPU SigLIP embeddings/tags. **Unsloth is not containerized or started by ReelMaker.**
Start Unsloth and load a suitable vision/planning model yourself before analysis or
planning. Existing projects, search with cached embeddings, renders, and downloads
remain accessible without Unsloth. Unavailable inference produces an actionable job
error; successful cached stages are retained for retry.

## What remains on Windows

| Windows files | Container view | Access |
|---|---|---|
| Existing ReelMaker data root (normally `%LOCALAPPDATA%\ReelMaker`) | `/data` | Read/write; registry, HF/YuNet model caches |
| Existing projects root (normally its `Projects` subfolder) | `/projects` | Read/write; knowledge databases, frames, thumbnails, plans, exports |
| Explicitly shared media folders | `/media/source-1`, etc. | Read-only |
| Optional chosen export root | `/exports` | Read/write; one subfolder per project |

These are bind mounts of Windows folders, not copies inside image layers or Docker
named volumes. Stopping, rebuilding, or removing containers does not remove them.
Startup refuses unmounted persistent stores instead of creating disposable user data.
Project purge is still an explicit destructive application action; container removal
and project purge are different operations.

Existing Windows source/export paths are translated at file-I/O boundaries. Registry
paths, scene IDs, cached descriptions, and embedding records are not rewritten or
reanalyzed for migration. Original exports remain accessible, and failed re-render
attempts keep the last validated MP4. Temporary attempts are published only after
duration/stream/resolution checks; crash recovery removes only owned temporary names.

## Setup

Use Docker Desktop with Linux containers and its WSL 2 backend. The default Compose
configuration requests the NVIDIA GPU. This machine's RTX 4070 SUPER was validated
with a real NVENC encode inside the image. See Docker's
[Windows GPU prerequisites](https://docs.docker.com/desktop/features/gpu/).

Keep the local provider configuration in `backend/.env` (copy `.env.example` for a
new setup). The key is read at runtime, excluded from build context, and never copied
into the image or generated host-binding file. The configuration helper translates
the loopback provider address to `host.docker.internal`, retaining its port/path.
This still refers to the same Windows machine; external inference endpoints are
rejected. Local provider requests do not use environment HTTP proxies.

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -Backup
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
```

The one-time helper uses the existing host `uv`/Python installation to inspect the
registry and generate gitignored `compose.override.yaml`. Existing active and archived
collections are shared automatically. `-Backup` creates integrity-checked SQLite
snapshots under `.docker-backups/<UTC timestamp>/`; it does not copy source media,
model weights, or existing exports. Stop application work before a migration backup.
The Docker build installs locked dependencies, including the optional CPU ML group.
Images contain code/tools, not weights or user media. SigLIP loads its persistent host
cache; a first installation may download model weights there.

The app opens at **http://localhost:5173**. Only this web port is published, on loopback.
The backend's port 8000 stays inside the Docker network. API and SSE requests are
forwarded by Nginx. Unsloth remains independently managed on the host.

## Start, stop, restart, and inspect

Use the **reelmaker** group buttons in Docker Desktop, or:

```powershell
# From the repository root
docker compose up -d --build --wait
docker compose stop
docker compose logs --tail 100 app web
docker compose ps
```

The PowerShell shortcuts also work from the `scripts` directory:

```powershell
.\stop-dev.ps1
.\start-dev.ps1
```

Start rebuilds changed source and waits for health checks. `-SkipBuild` uses the existing
images. Stop allows 45 seconds for shutdown and releases published ports; state stays
in Windows folders. There are no PID files or native port-owner termination commands.
The group remains visible in Docker Desktop while stopped. `docker compose down`
also removes the containers/network while leaving bind-mounted files intact.

Use `REELMAKER_PORT` to choose another host port:

```powershell
$env:REELMAKER_PORT = '5180'
docker compose up -d --wait
```

The legacy native services were explicitly verified and stopped during cutover.
The new Stop command controls only this Compose group; it does not kill unrelated
local servers or manage Unsloth.

## Choose Windows folders

Add shared media roots without moving the originals:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -MediaRoot 'D:\Photos'
docker compose up -d --wait
```

Previously configured roots and existing collection paths are retained. The web
**Browse** button lists only shared Windows folders/subfolders; arbitrary Windows
folders are not exposed to the container. You can also type a path within a shared
root. Changing bindings requires Compose to recreate the backend. Missing bind sources
fail startup rather than silently becoming empty inventories; reconnect the source or
adjust the configuration before starting. An inventory access error preserves knowledge.

By default finished MP4s stay in each project's existing Windows `exports` folder.
To choose a separate host destination:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\configure-docker.ps1 -ExportRoot "$env:USERPROFILE\Videos\ReelMaker"
docker compose up -d --wait
```

New renders go into a project subfolder there. Existing renders are not relocated;
their stored paths remain downloadable. Changing the export root again leaves earlier
files intact; purge covers the project store and currently configured export root.
The browser's **Download** button still saves a copy according to browser settings,
normally Windows Downloads. No custom export location was forced during migration.

## Validation and recovery

Run backend checks in the built Linux image without attaching user data:

```powershell
docker run --rm reelmaker-app pytest
docker run --rm reelmaker-app ruff check .
```

Native contributor checks remain `cd backend; uv run pytest` / `uv run ruff check .`
and `cd frontend; npm run build`. Slow model integration is opt-in (`pytest -m slow`).
`scripts/test-docker-scripts.ps1` mocks wrapper commands; the isolated end-to-end
check is `uv run --project backend python backend/tools/docker_smoke.py`.
It uses `.docker-smoke`, a separate Compose group/port 15173, synthetic media,
NVENC, explicit provider-unavailable behavior, graceful stop, and forced-crash recovery.
It never attaches the real collection and removes its temporary containers afterward.

At startup, orphaned queued/running jobs become failed with retry guidance; they are
not automatically replayed. Interrupted renders retain the last good export if present,
otherwise become failed. Queued jobs are cancelled on normal shutdown. Retry through
Analyze, Generate, or Render after resolving the cause. Thread-based analysis is not
hard-cancellable mid-computation; Docker may force termination after the grace period,
with durable recovery on the next start.

The migration snapshots preserve pre-cutover database state. Restore only with
ReelMaker stopped, retaining current files first and handling SQLite WAL/SHM consistently;
restoring a snapshot rolls project history back to its snapshot time. No source/cache
deletion or cache rebuilding is part of the normal migration or recovery flow.

### HDR verification limitation

Real-media migration QA found silent block artifacts in Docker's Vulkan/libplacebo
output despite exit code zero. Container renders therefore use CPU zscale tone mapping
and retain NVENC for encoding. A rerender measured 35.0 seconds and three inspected
HDR frames were clean of blocks; the appearance is more washed out than the old
export, so broader HDR color acceptance remains pending. Native capability fallback
is unchanged. This is distinct from an encoding capability probe succeeding.
