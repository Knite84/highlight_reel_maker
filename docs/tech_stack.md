# Tech Stack

Verified against repository source and lockfiles: 2026-10-20. Maintain this inventory
when technologies, configuration, paths, or module responsibilities change. This
describes the implemented stack, not a guarantee that every feature is correct; see
[`architecture_review.md`](architecture_review.md) for known limitations and
[`lessons_learned.md`](lessons_learned.md) for maintenance rules and evidence.

Companion to [`status.md`](status.md) (current state) and [`implementation-plan.md`](implementation-plan.md)
(original design). This is the "what runs where" inventory.

| Layer | Tech | Notes |
|---|---|---|
| UI | Vue 3 + TypeScript + Vite + Pinia + TanStack Query | No component library — hand-rolled cards/grids. Vite `strictPort: true` on 5173 |
| UI views | `ProjectsView`, `ProjectView`, `SettingsView` | ProjectView is the main surface: Analyze → Generate → Reels grid → collapsed Scene library / Media files accordions |
| Realtime | SSE (`/api/jobs/stream`) + scoped HTTP snapshots → one Pinia jobs store | Hydrates on project entry and SSE open/reconnect; polls 3s after each completed refresh. Monotonic registry job revisions reject stale responses. Snapshots include recent 200 plus all active project jobs; known active jobs outside that window get detail refreshes. Plan list also polls rendering rows at 4s |
| Backend | Python >=3.12 + FastAPI + uvicorn | Docker Linux Python 3.13.16; native checks on 3.13.6. Start via `scripts/start-dev.ps1` |
| DB | SQLite via aiosqlite | Registry (`%LOCALAPPDATA%\ReelMaker\registry.db`) + per-project `cache/db.sqlite`. Migrations via `CREATE TABLE IF NOT EXISTS` + `_ensure_column` |
| Jobs | Bounded asyncio queue (`app/jobs/worker.py`) | One process/worker; atomic cross-kind admission per project, default 32 waiting jobs. Handlers/queue/terminal retry buffer are in memory, rows in registry SQLite. Persistence faults pause admission/execution; health reports worker failure. Startup reconciles interruptions; shutdown cancels owned work |
| LLM/VLM | Unsloth Desktop (OpenAI-compatible), Qwen3-VL GGUF | External Windows server; planner + scene descriptions. `/v1/models` lists *downloaded* ≠ *loaded* |
| Embeddings | SigLIP2 via transformers (CPU torch inside backend Docker) | Zero-shot tag vocabulary; vectors in `embeddings` table |
| Face detection | OpenCV `FaceDetectorYN` (YuNet) | Stills only, render-time crop anchoring. ONNX (~230KB) auto-downloaded from OpenCV Zoo to `%LOCALAPPDATA%\ReelMaker\models\`; graceful fallback to upper-biased heuristic |
| Media understanding | PySceneDetect, OpenCV frame metrics, pillow-heif, exiftool | Scenes → rep frames → Tier-0 CV → embeddings → VLM descriptions |
| Image initialization | Shared `pipeline/images.py` | Lazy HEIF registration before metadata/decoding; metadata image I/O runs off the event loop |
| Frame evidence | Nullable `scene_frames.timestamp_sec` / `timestamp_source` | Sharpest primary remains independent of chronological vision inputs. Decoder milliseconds or labeled index/FPS estimates; legacy unknown timestamps use one representative frame |
| Rendering | FFmpeg/ffprobe | Filtergraph builder (`renderer/filtergraph.py`): xfade/acrossfade, Ken Burns `zoompan` from 4× upscaled stills, `tpad`/`apad`, loudnorm, BT.709 output. NVENC requires an actual encode probe, otherwise x264. Docker uses CPU zscale tone mapping (libplacebo corrupted real HDR output); native capability errors retry with zscale. NVENC still encodes. HDR appearance needs broader QA |
| Duration policy | Planner fitter (`planner/generate.py`) | Request duration overrides model output before validation/fitting (F05). Frame-based grow/shrink, `_evenize_photo_spans`, freeze-tail fallback remain; chronology, pacing disclosures and shared timing ownership are open (F09). Output duration/streams/resolution are probed before publication |
| EXIF/orientation | Shared Pillow/HEIF display decoder, explicit OpenCV video rotation, FFmpeg display metadata | Photos normalize once, including EXIF mirrors, and render from temporary PNGs. Photo dimensions are display dimensions/rotation zero; video dimensions stay coded with separate clockwise rotation. `orientation=display.v1` upgrades only affected visual descendants |
| Plan identity | Edit-plan JSON v2 / legacy v1 readers | Scene ID, source file ID/revision/hash bind new clips; fitters resolve scenes, render checks freshness/bounds, v1 ambiguity requires replanning. Source-time ordering runs after fillers. No new database schema required |
| Config | pydantic-settings, `REELMAKER_*` env + `backend\.env` | `data_root`, `projects_root`, Unsloth URL/key, planner model id |
| Dev tooling | pytest (fast suite; `-m slow` = real-model E2E), ruff, vue-tsc | Backend has **no auto-reload** — restart via scripts after Python edits |

## Dependencies and version ownership

`backend/pyproject.toml` and `frontend/package.json` declare supported dependency
ranges. `backend/uv.lock` and `frontend/package-lock.json` contain resolved versions;
use those as the authoritative detailed inventory rather than manually updating
every transitive package here. Application lockfiles are unchanged; Docker adds the Linux environment.

Selected lockfile versions at review time:

| Area | Resolved versions |
|---|---|
| Backend web/data | FastAPI 0.141.1, uvicorn 0.52.4, Pydantic 2.13.4, aiosqlite 0.22.1, httpx 0.28.1 |
| Media/numerics | OpenCV headless 5.0.0.93, PySceneDetect 0.7.1, Pillow 12.3.0, pillow-heif 1.5.0, NumPy 2.5.2, xxhash 4.0.1 |
| Optional local ML | transformers 5.15.1; torch 2.13.0 / 2.13.0+cpu platform entries, configured CPU index |
| Frontend | Vue 3.5.41, Vue Router 4.6.4, Pinia 3.0.4, Vite 7.3.6, TypeScript 5.9.3, vue-tsc 3.3.11 |
| Backend checks | pytest 9.1.1, ruff 0.16.4 |

Backend dependencies are divided into runtime, `dev`, and optional `ml` groups.
`uv sync --group ml` enables local embedding support. The VLM/planner is a separately
running OpenAI-compatible HTTP service; its model weights are not Python dependencies.
Docker supplies FFmpeg, ffprobe, exiftool, fonts and Vulkan; Node builds the UI and Nginx serves it. Unsloth stays external. OS packages/base tags are not pinned by application lockfiles.

## Responsibilities and data flow

```text
Vue views / TanStack Query / Pinia
    -> FastAPI routes -> registry DB + in-memory job runner
    -> scan -> metadata -> scenes -> representative frames/CV
    -> SigLIP2 embeddings/tags + VLM scene descriptions -> project SQLite/cache
    -> quality-based candidate shortlist -> LLM JSON plan -> normalization/fitting
    -> persisted edits -> render preparation -> FFmpeg -> MP4 export
```

- `backend/app/api/`: request validation and orchestration; also currently owns the
  planning job handler in `plans.py`.
- `backend/app/core/`: configuration, connections, additive migrations, registry repositories.
- Project lifecycle: registry `archived_projects` stores removal snapshots; restore keeps
  original IDs/storage paths. Archive/restore use dedicated connections for atomic
  registry transactions. The single runner serializes submissions against lifecycle
  mutations; queued/running work blocks removal. New storage slugs include a UUID.
- Inventory: traversal/stat/root errors fail the scan before deletion reconciliation;
  a complete inventory still reconciles intentional source deletions. Analyze uses
  the same scan service. Source/analysis revisions prevent stale reuse; optional deep
  hashing detects changes that preserve inventory size/mtime. Invalidations and source
  removals preserve SQL knowledge snapshots and previous frame generations.
- `pipeline/provenance.py`: stage contracts, dependency invalidation, artifact checks,
  history snapshots and stale-plan markers. Per-stage provenance is recorded in
  `analysis_stages`; legacy adoption stays explicitly unverified. Legacy databases get
  an integrity-checked SQLite snapshot before additive migration. See
  [cache-provenance.md](cache-provenance.md) for limits and recovery.
- `backend/app/pipeline/`: source inventory, analysis stages, and embedding search.
- `backend/app/ai/`: OpenAI-compatible HTTP client and lazy SigLIP2 singleton.
- `backend/app/planner/`: quality scoring, candidate summaries, model planning, schema
  validation and duration repairs. Candidate retrieval currently ignores prompt relevance.
- `backend/app/renderer/`: crop focus, profile/encoder selection, graph generation,
  FFmpeg execution and edit status updates. A plan is reusable without rerunning vision.
- `pipeline/images.py` / `pipeline/orientation.py`: display decoding and selective
  legacy visual-cache upgrade, retaining scene IDs/history. `planner/identity.py`
  validates v2 references and unambiguous v1 intervals before rendering/publication.
  See [media-contracts.md](media-contracts.md) for supported transforms and limitations.
- `frontend/src/views/ProjectView.vue`: most project actions, job state, reels, and library UI.
- `frontend/src/stores/jobState.ts` owns revision merging and per-view terminal
  notification; `stores/jobs.ts` owns scoped SSE/polling, timeout/reconnect and cancellation.
  ProjectView has one batched query refresh path for done/failed/cancelled jobs and
  shows analysis row coverage, per-run work, retained errors and retry guidance.
- `frontend/src/components/PlanPreview.vue`: source sequence player; currently incomplete
  for images and not visually equivalent to the rendered output.
- `core/tasks.py`: shields blocking job threads through cancellation until return,
  retaining ownership before cleanup/next job. Subprocesses keep their separate kill/reap path.
- `planner/ordering.py`, `timing.py`, `review.py`: conservative explicit brief
  sequencing, shared planner/renderer timeline math, and server-derived duration
  disclosure tokens. `RenderReview.vue` presents review before the render action.

Storage uses plain SQL through aiosqlite, WAL journaling, and foreign keys. There is
no ORM, vector database, distributed job broker, or versioned migration framework.
Search computes NumPy similarity over up to 4,000 loaded embedding rows. Source
revisions and explicit stage/model contracts govern cache reuse. Missing visual
artifacts repair selectively; unchanged complete caches avoid inference/provider calls.
Contracts require explicit version bumps; weight changes under the same model ID
still require pinned revisions/checksums. Orientation/HEIF now use the documented
display contract; see media-contract limits for HDR, mirrored video and VFR coverage.

## Runtime data layout (`%LOCALAPPDATA%\ReelMaker\`)

| Path | Contents |
|---|---|
| `registry.db` | Projects + global jobs |
| `Projects/<slug>/cache/db.sqlite` | Per-project files/scenes/tags/embeddings/edits |
| `Projects/<slug>/cache/frames|thumbs` | Rep frames + scene thumbnails |
| `Projects/<slug>/exports` | Versioned MP4s; title textfiles are temporary |
| `models\` | YuNet face-detection ONNX (auto-download) |
| `hf\` | HuggingFace cache (SigLIP2) |

`registry.db` also stores archived project metadata. `<slug>` includes an immutable
UUID suffix for new projects; existing legacy paths are unchanged. Archiving does
not move files. Restore may supply a new display name when the old one is in use.

## Configuration, hosting, and boundaries

- Defaults derive from `%LOCALAPPDATA%` (home directory fallback). `REELMAKER_DATA_ROOT`
  and `REELMAKER_PROJECTS_ROOT` are independent settings; changing data root alone
  does not relocate the project root. Original media remains at the imported folder.
- Registry stores project names/slugs, source paths, counts, and job history. Project
  databases store files, scenes, frame metrics, tags, embedding blobs, descriptions,
  and edit plans/render state. DELETE archives projects by default. Explicit purge
  removes archived storage and job history after containment/source-overlap checks;
  original media is preserved. Failed purges retain the archive record for diagnosis/retry.
- Compose group `reelmaker`: FastAPI/FFmpeg/SigLIP backend and Nginx static Vue/API
  proxy. Only loopback 5173 is published. Node 22 builds the UI; Python 3.13 and
  uv 0.12.17 build the backend with frozen dependencies and CPU ML support.
- Start builds/reconciles the group and waits for health. Stop releases its port;
  Docker Desktop can manage both services. PID manifests are superseded.
- Generated ignored `compose.override.yaml` mounts Windows data `/data`, projects
  `/projects`, read-only media `/media/source-N`, optional host exports `/exports`.
  Persistent roots must be mounts. `core/paths.py` translates paths without database
  rewrites. Shared-folder browsing replaces native dialogs. See [docker.md](docker.md).
- `.env` is read relative to the backend working directory. Provider URL/key and model
  ID are environment settings; Settings UI is read-only. Both planner and vision use
  `planner_model_id`; a separate explicit vision-model setting is absent.
- Default inference URL is loopback. Provider URLs are restricted to loopback and host.docker.internal HTTP(S).
  SigLIP2 weights and YuNet may download on first use. Existing SigLIP cache loaded offline in Docker; model checksum enforcement remains pending.
- Profiles: proxy 1280x720 at 8M, final 1920x1080 at 20M, 30 fps, H.264 video/AAC audio.
  Frontend currently requests proxy only. Music schema exists, but music mixing is
  not implemented. Face detection only informs still-photo framing, not recognition.
- `REELMAKER_JOB_QUEUE_LIMIT` defaults to 32 waiting jobs (1–256), plus one active.
  Admission conflicts return 409; full/unavailable worker returns 503. Terminal writes
  retry at 0.5s without handler replay; ambiguous admission writes require restart.
  No new dependency or inference/provider deployment was introduced in this slice.
  V2 sequencing/adjustment/filler JSON fields are additive; old plans are read-only
  during review. See [job-and-planning-policy.md](job-and-planning-policy.md).

## Verification and maintenance

Latest A07/F15/F19 + F09 slice (2026-10-20): **202 fast backend tests passed on Windows
and Linux**, one slow test deselected each; Ruff passed both. **16 frontend tests
passed on Windows and Linux/Node 22**, frontend type/build and both images built.
Synthetic mixed-transition/freeze FFmpeg exports matched shared timing within two
frames. No new real-media visual/audio, interactive browser or live Unsloth QA.
Thread hard termination and model output determinism are not claimed.

F10/F11 slice (2026-10-20): **169 fast backend tests passed on Windows and Linux**,
one slow test deselected each. Ruff passed Windows and Linux.
**15 frontend tests passed on Windows and Linux/Node 22**, including actual Pinia
transport/timer tests and server-rendered Vue project templates; frontend build and
both Docker image builds passed. The web image now runs `npm test` before build.
Contributor tests require Node 22.18+ and existing Vite/Vue dependencies; no runtime
dependency was added. Registry migration adds `jobs.revision` with zero default;
existing job results/history and project knowledge remain intact. Analyze's committed
inventory callback updates counts even if later analysis fails. Stored coverage is
row presence for current source revisions, not certification of legacy models/artifacts.
No new real-media, audio, browser-interaction or live Unsloth QA occurred.

Latest F07/F06 slice (2026-10-20): **160 fast tests passed on Windows and Linux**,
one slow test deselected each; Ruff passed both, frontend build passed, backend image
built. Viewed all rendered EXIF fixtures and one real-photo NVENC render using cached
face weights in an isolated QA store. Original source hash unchanged. No audio
listening, browser or live Unsloth QA. Earlier checkpoint evidence follows.

Latest duration-authority slice (2026-10-20): **121 fast tests passed on Windows and
Linux**, one slow test deselected each; Ruff passed both; frontend build passed;
backend image built. Six fake-provider cases verify request authority through the
planning handler, fitting and SQLite persistence. No new real-media, audio, browser
or live-model QA occurred. No runtime paths, providers, dependencies or schema changed.

Earlier scoped HEIC/ordering slice (2026-10-20): **115 fast tests passed on Windows
and Linux**, one slow test deselected each; Ruff passed both, frontend build passed,
backend image built. Fresh-process HEIC/HEIF and captured vision ordering were tested;
a synthetic HEIC thumbnail was viewed. No new real-media/audio/browser/live-model QA.
Description contract v2 selectively refreshes tracked v1 descriptions on Analyze;
frame/embedding caches remain reusable. Orientation normalization was addressed by
the later F07/F06 checkpoint above.

Earlier source/cache slice (2026-10-20): **110 fast tests passed on Windows and Linux**,
one slow test deselected each; Ruff passed both and frontend type/build passed.
Both images built without recreating live services. Synthetic thumbnail visual QA
occurred; no new real-media, live-model, audio or browser QA occurred in this slice.
The Docker migration evidence below belongs to the earlier checkpoint.

Run backend checks from `backend`: `uv run pytest` and `uv run ruff check .`.
Slow real-model integration is opt-in: `uv run pytest -m slow`. Run the frontend
type-check/build from `frontend`: `npm run build`. Final checks on 2026-10-20: 90 fast tests passed on Windows and Linux, one slow
excluded each; Ruff passed both, frontend build passed. Docker lifecycle smoke,
read-only media, host output/downloads, offline SigLIP and actual port release passed.
A real cached plan rendered 35.0 seconds with NVENC. Real-frame inspection found and removed Docker HDR blocks by choosing zscale;
three corrected HDR frames were inspected. Tone mapping appears more washed out;
broader HDR color acceptance remains open. No audio listening, interactive browser QA or
live Unsloth tests occurred. See [status.md](status.md) for evidence and limitations.

When changing the stack, update the manifests and lockfiles, this inventory, relevant
setup instructions, and any lessons invalidated by the change. Record actual checks,
not inherited historical claims. Review findings describe remediation work, not a
replacement architecture already implemented.
