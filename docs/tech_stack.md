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
| Realtime | SSE (`/api/jobs/stream`) → Pinia jobs store | 3s polling follows locally started jobs; plan list polls at 4s while rows are rendering. SSE has no snapshot/replay; recovery remains incomplete |
| Backend | Python >=3.12 + FastAPI + uvicorn | Docker Linux Python 3.13.16; native checks on 3.13.6. Start via `scripts/start-dev.ps1` |
| DB | SQLite via aiosqlite | Registry (`%LOCALAPPDATA%\ReelMaker\registry.db`) + per-project `cache/db.sqlite`. Migrations via `CREATE TABLE IF NOT EXISTS` + `_ensure_column` |
| Jobs | asyncio queue (`app/jobs/worker.py`) | One global worker; callable handlers and queue are in memory, job rows in registry SQLite. Startup reconciles interrupted jobs/edits; shutdown cancels work. Encoder timeout, bounded output, kill/reap cleanup |
| LLM/VLM | Unsloth Desktop (OpenAI-compatible), Qwen3-VL GGUF | External Windows server; planner + scene descriptions. `/v1/models` lists *downloaded* ≠ *loaded* |
| Embeddings | SigLIP2 via transformers (CPU torch inside backend Docker) | Zero-shot tag vocabulary; vectors in `embeddings` table |
| Face detection | OpenCV `FaceDetectorYN` (YuNet) | Stills only, render-time crop anchoring. ONNX (~230KB) auto-downloaded from OpenCV Zoo to `%LOCALAPPDATA%\ReelMaker\models\`; graceful fallback to upper-biased heuristic |
| Media understanding | PySceneDetect, OpenCV frame metrics, pillow-heif, exiftool | Scenes → rep frames → Tier-0 CV → embeddings → VLM descriptions |
| Rendering | FFmpeg/ffprobe | Filtergraph builder (`renderer/filtergraph.py`): xfade/acrossfade, Ken Burns `zoompan` from 4× upscaled stills, `tpad`/`apad`, loudnorm, BT.709 output. NVENC requires an actual encode probe, otherwise x264. Docker uses CPU zscale tone mapping (libplacebo corrupted real HDR output); native capability errors retry with zscale. NVENC still encodes. HDR appearance needs broader QA |
| Duration policy | Planner fitter (`planner/generate.py`) | Frame-based grow/shrink, `_evenize_photo_spans`, freeze-tail fallback. Intended exact-duration behavior has request-authority and output-verification gaps (review F05/F09); output duration/streams/resolution are now probed before publication |
| EXIF/orientation | exiftool + OpenCV + PIL `exif_transpose` + ffmpeg `transpose` | Stored per-file (`files.rotation`, degrees CW). Current decode/rotation paths can double-apply EXIF (review F07); normalized decoder contract pending |
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
  the same scan service. This does not fix source/version invalidation (F01).
- `backend/app/pipeline/`: source inventory, analysis stages, and embedding search.
- `backend/app/ai/`: OpenAI-compatible HTTP client and lazy SigLIP2 singleton.
- `backend/app/planner/`: quality scoring, candidate summaries, model planning, schema
  validation and duration repairs. Candidate retrieval currently ignores prompt relevance.
- `backend/app/renderer/`: crop focus, profile/encoder selection, graph generation,
  FFmpeg execution and edit status updates. A plan is reusable without rerunning vision.
- `frontend/src/views/ProjectView.vue`: most project actions, job state, reels, and library UI.
- `frontend/src/components/PlanPreview.vue`: source sequence player; currently incomplete
  for images and not visually equivalent to the rendered output.

Storage uses plain SQL through aiosqlite, WAL journaling, and foreign keys. There is
no ORM, vector database, distributed job broker, or versioned migration framework.
Search computes NumPy similarity over up to 4,000 loaded embedding rows. Analysis
artifacts are presence-cached, with incomplete source/version invalidation today.

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

## Verification and maintenance

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
