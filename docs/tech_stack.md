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
| Backend | Python >=3.12 + FastAPI + uvicorn | Bare-metal (Dropped Docker). Review tests ran on Python 3.13.6. Run via `scripts/start-dev.ps1` |
| DB | SQLite via aiosqlite | Registry (`%LOCALAPPDATA%\ReelMaker\registry.db`) + per-project `cache/db.sqlite`. Migrations via `CREATE TABLE IF NOT EXISTS` + `_ensure_column` |
| Jobs | asyncio queue (`app/jobs/worker.py`) | One global worker; callable handlers and queue are in memory, job rows in registry SQLite. No restart recovery. 30-min timeout covers the main encode wait |
| LLM/VLM | Unsloth Desktop (OpenAI-compatible), Qwen3-VL GGUF | Planner + scene descriptions. `/v1/models` lists *downloaded* ≠ *loaded* |
| Embeddings | SigLIP2 via transformers (CPU torch) | Zero-shot tag vocabulary; vectors in `embeddings` table |
| Face detection | OpenCV `FaceDetectorYN` (YuNet) | Stills only, render-time crop anchoring. ONNX (~230KB) auto-downloaded from OpenCV Zoo to `%LOCALAPPDATA%\ReelMaker\models\`; graceful fallback to upper-biased heuristic |
| Media understanding | PySceneDetect, OpenCV frame metrics, pillow-heif, exiftool | Scenes → rep frames → Tier-0 CV → embeddings → VLM descriptions |
| Rendering | FFmpeg/ffprobe | Filtergraph builder (`renderer/filtergraph.py`): xfade/acrossfade, Ken Burns `zoompan` from 4× upscaled stills, `tpad`/`apad`, loudnorm, BT.709 output. NVENC selection uses encoder listing, with x264 chosen when NVENC is absent; runtime failure retry is not implemented. HDR has libplacebo/zscale preparation attempts with incomplete probing |
| Duration policy | Planner fitter (`planner/generate.py`) | Frame-based grow/shrink, `_evenize_photo_spans`, freeze-tail fallback. Intended exact-duration behavior has request-authority and output-verification gaps (review F05/F09); stored rendered duration is calculated, not probed |
| EXIF/orientation | exiftool + OpenCV + PIL `exif_transpose` + ffmpeg `transpose` | Stored per-file (`files.rotation`, degrees CW). Current decode/rotation paths can double-apply EXIF (review F07); normalized decoder contract pending |
| Config | pydantic-settings, `REELMAKER_*` env + `backend\.env` | `data_root`, `projects_root`, Unsloth URL/key, planner model id |
| Dev tooling | pytest (fast suite; `-m slow` = real-model E2E), ruff, vue-tsc | Backend has **no auto-reload** — restart via scripts after Python edits |

## Dependencies and version ownership

`backend/pyproject.toml` and `frontend/package.json` declare supported dependency
ranges. `backend/uv.lock` and `frontend/package-lock.json` contain resolved versions;
use those as the authoritative detailed inventory rather than manually updating
every transitive package here. No dependencies were changed in this review.

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
FFmpeg, ffprobe, exiftool, Node.js/npm, uv, and the inference server are external tools;
their installed versions are not pinned by the application lockfiles.

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
| `Projects/<slug>/exports` | Rendered MP4s + title textfiles |
| `models\` | YuNet face-detection ONNX (auto-download) |
| `hf\` | HuggingFace cache (SigLIP2) |

## Configuration, hosting, and boundaries

- Defaults derive from `%LOCALAPPDATA%` (home directory fallback). `REELMAKER_DATA_ROOT`
  and `REELMAKER_PROJECTS_ROOT` are independent settings; changing data root alone
  does not relocate the project root. Original media remains at the imported folder.
- Registry stores project names/slugs, source paths, counts, and job history. Project
  databases store files, scenes, frame metrics, tags, embedding blobs, descriptions,
  and edit plans/render state. Deleting registry entries retains runtime files unless
  purge is requested; storage reuse needs attention (review F16).
- API runs on loopback port 8000; Vite development server uses strict port 5173 and
  proxies `/api`. FastAPI CORS allows the localhost/127.0.0.1 development origins.
  The scripts launch development services; a packaged desktop installer or production
  static frontend hosting is not implemented here.
- `.env` is read relative to the backend working directory. Provider URL/key and model
  ID are environment settings; Settings UI is read-only. Both planner and vision use
  `planner_model_id`; a separate explicit vision-model setting is absent.
- Default inference URL is loopback. Arbitrary configured URLs are accepted, so
  local-only inference is a deployment assumption rather than an enforced guarantee.
  SigLIP2 weights and YuNet may download on first use. Offline readiness is not verified.
- Profiles: proxy 1280x720 at 8M, final 1920x1080 at 20M, 30 fps, H.264 video/AAC audio.
  Frontend currently requests proxy only. Music schema exists, but music mixing is
  not implemented. Face detection only informs still-photo framing, not recognition.

## Verification and maintenance

Run backend checks from `backend`: `uv run pytest` and `uv run ruff check .`.
Slow real-model integration is opt-in: `uv run pytest -m slow`. Run the frontend
type-check/build from `frontend`: `npm run build`. The reviewed baseline passed
53 fast backend tests and the frontend build; real-model tests and live visual QA
were not run for this review. No automated browser test suite is configured.

When changing the stack, update the manifests and lockfiles, this inventory, relevant
setup instructions, and any lessons invalidated by the change. Record actual checks,
not inherited historical claims. Review findings describe remediation work, not a
replacement architecture already implemented.
