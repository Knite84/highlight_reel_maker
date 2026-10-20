# Project Status — Handoff Notes

## 2026-10-20 — Request duration authority (F05) and documentation handoff

New planning requests now apply the validated request's duration before model-plan
validation and fitting. The model's shorter/longer/invalid/missing duration cannot
replace it. Prompt/seed retain request ownership as before. Saved plans, source media,
existing knowledge and exports are unchanged; no schema migration was added.

Six regressions call the real planning handler with a fake provider and disposable
SQLite store. A 45-second request remains 45 in both the edits column and plan JSON,
and the fitted timeline agrees within one 30-fps frame. The tests include model
targets 12, 90, zero, null, invalid text and an omitted target.

Verification: **121 fast pytest tests passed on Windows and Linux**, one slow test
deselected each; Ruff passed both; frontend type/build passed; backend image built.
No real provider calls, new real-media visual/audio QA or interactive browser QA
occurred. Known Starlette/httpx and npm environment warnings remain.

README, stack inventory, lessons and both audit documents reflect this checkpoint.
Running services were not recreated. Activate with `scripts/start-dev.ps1` or
`docker compose up -d --build --wait`. Changes are ready for review/commit.

Remaining work includes F07 orientation (EXIF/mirrored/video decoder verification),
F06 scene identity, F09 chronology and pacing disclosures, UI job reconciliation,
retrieval relevance, previews/final exports and remaining model/capability hardening.
The user-approved default remains chronological unless the brief explicitly requests
narrative order; duration-filling tradeoffs still need disclosure before rendering.
Historical checkpoints below describe their own verification, not newer behavior.

## 2026-10-20 — Scoped HEIC initialization and frame ordering (A03/A04)

- Shared lazy HEIF initialization runs before metadata opens HEIC/HEIF, including
  uppercase extensions, and before frame decoding. Metadata dimensions are read off
  the event loop. New fresh-process tests run scan, metadata and visuals without
  test-side registration in the child process.
- Sample timestamps and their provenance are persisted in nullable frame columns.
  Sharpness still selects the primary/thumbnail; vision inputs sort by source time.
  Decoder milliseconds are used when available; fallback frame-index/FPS estimates
  are explicitly labeled. This is not a frame-accurate VFR seeking overhaul.
- Existing frame rows/files remain intact. For new descriptions whose legacy frames
  lack timestamps, send one representative frame and explicitly state that temporal
  order is unknown. Never guess chronology from primary/secondary roles.
- Description contract `describe.v2-chronological` invalidates the description branch
  of previously tracked v1 analysis on the next Analyze. Prior SQL knowledge is
  archived by the existing invalidation mechanism; scene detection, frames and
  embeddings are reused. Untracked legacy descriptions stay retained/unverified.
  Re-describing requires the external Unsloth model as usual.

Verification: **115 fast pytest tests passed on Windows and Linux**, one slow test
deselected each; Ruff passed both, frontend type/build passed, backend image built.
Five new tests cover fresh HEIC/HEIF processes, sharpest-last chronology, estimated
timestamp labeling and legacy migration/fallback. Viewed a synthetic HEIC thumbnail;
no new real-media/audio, interactive browser or live-model QA occurred. Source media
and real project databases were not changed; running services were not recreated.
Run `scripts/start-dev.ps1` to activate the built changes. Broader orientation
normalization and its EXIF 1–8/video verification remain F07, outside this slice.

## 2026-10-20 — Source/cache foundation (package 2, F01/A05)

Implemented the source revision, stage contract and artifact repair portion of
package 2. See [cache-provenance.md](cache-provenance.md) for behavior and recovery.
F07 orientation, A03 HEIC initialization and A04 frame timestamp ordering are next;
this is not completion of the entire original package 2.

- Inventory changes mark a source revision stale instead of silently reusing its
  knowledge. Analyze hashes suspect files, reuses byte-identical content and rebuilds
  only changed descendants. **Verify file contents** detects same-size edits with
  restored mtime. Stale sources are excluded from candidates and semantic search.
- Stage contract/model changes invalidate their own dependency branch. Missing/empty
  visual artifacts repair only affected scenes without repeating existing model calls.
  Complete caches avoid model loading and provider resolution. Partial failures retry
  successful stages; individual description/batch failures no longer imply completion.
- Invalidation/removal snapshots SQL knowledge (including vectors) into
  `knowledge_history`; generation paths retain prior artifact files. No automatic
  history cleanup is implemented. Legacy analysis is retained and explicitly unverified.
- Before migrating legacy project schemas, startup creates an integrity-checked SQLite
  snapshot beside the database. New columns/tables are additive. Disposable database
  tests verify backup creation, integrity, restoration and repeat migration.
- Saved plans referencing replaced/removed sources remain stored but cannot rerender;
  Analyze then Generate a new plan. Old exports remain downloadable. Rendering compares
  known source hashes before encoding and publication, even without a preceding scan.
  Stable scene references and complete plan-version enforcement remain package 5 work.

Verification: **110 fast pytest tests passed on Windows and Linux**, with one slow
test deselected per run; Ruff passed both; frontend type/build passed; both Docker
images built. Nineteen cache regressions plus API tests cover the new behavior.
Viewed a synthetic four-quadrant thumbnail; existing synthetic FFmpeg regression
tests passed. No new real-media visual/audio QA, interactive browser QA or live-model
test was performed. No real collection was migrated or reanalyzed in this slice.
Known Starlette/httpx and npm environment warnings remain.

The running containers were left on their previous build; images/source are updated.
Run `scripts/start-dev.ps1` (or `docker compose up -d --build --wait`) to activate.
That startup performs the protected migration. Preserve retained cache generations
and backups until explicit recovery/cleanup decisions are made.

## 2026-10-20 — Docker and lifecycle foundation

This supersedes the native-launcher and pending restart-recovery notes below.
The user approved Docker and lifecycle safety before continuing the other audit work.
SigLIP runs with the backend; Unsloth stays manually managed on Windows.

- Compose group `reelmaker` is healthy at http://localhost:5173. Nginx serves the
  frontend and proxies the backend; only loopback 5173 is published. Verified native
  services were stopped without touching Unsloth. Start can be rerun safely and waits
  for health. A real Stop from the scripts directory released 5173 (socket bind
  verified); Start then rebuilt and restored health.
- Persistent data, models, caches and exports use Windows bind mounts; source media
  is read-only. Paths/IDs remain unchanged. See [docker.md](docker.md) for shared
  folders, optional export destinations, backup/restore and Docker Desktop operation.
  Browser downloads still follow browser settings. No custom export root was forced.
- Integrity-checked final database backups are in ignored
  `.docker-backups/20261020T142500087060Z`. Record comparisons preserved files (71),
  scenes (71), frames (101), tags (213), embeddings (71), descriptions (71).
  No source media was moved or reanalyzed.
- F03/F04 and subprocess portions of A01/A02: startup reconciles interrupted jobs
  and rendering edits; shutdown/cancel kill and reap encoder processes; subprocess
  output is bounded. Preparation, encode, probe and publication share cleanup.
  Validated unique outputs preserve prior exports; streams/resolution/duration are
  checked before success. Hard cancellation of analysis threads remains limited.
- Local provider URL restrictions, actual NVENC capability checks/x264 fallback,
  portable container HDR tone mapping, native capability fallback, and failed analysis summaries with retained
  partial results improve parts of F10/F14/F15/F17, without closing their full scope.

Verification: **90 fast pytest tests passed on Windows and Linux**, one slow test
excluded each; Ruff passed both; frontend build/type checking and PowerShell wrapper
checks passed. Isolated Compose tests passed synthetic/title NVENC rendering,
read-only sources, host exports/range download, unavailable provider behavior,
graceful Stop/restart and SIGKILL/restart while retaining the previous MP4.
Ignored `.docker-smoke/` contains reports. SigLIP loaded the existing host cache
offline and produced a 768-dimensional vector; nonfatal token-ID warnings remain.

Real-media QA: cached project 2/edit 14 rendered with NVENC, measured **35.0 seconds**,
and passed range download while preserving its prior MP4. Initial frame inspection found block artifacts in the Docker libplacebo path, absent
from both the source and previous export. Container rendering now uses CPU zscale
tone mapping with NVENC encoding. Job 49 rerendered 35.0 seconds; frames at 25/29/33s
were clean of those blocks. Its more washed-out tone mapping remains a quality
limitation; broad HDR color/visual acceptance is still pending. Earlier exports remain.
Audio was not listened to and no interactive browser QA occurred. Unsloth was
unreachable both natively and through Docker; live-model generation and the slow
suite were not run. Start/load Unsloth manually before Analyze/Generate.

Next: resume source/stage invalidation and
decoder contracts (F01/F07). Duration authority, chronology, scene identity, retrieval,
UI job reconciliation and preview findings remain open.

## 2026-10-20 — Remediation package 1 (historical)

Approved decisions and the current backlog are in
[`audit-remediation-plan-2026-10-20.md`](audit-remediation-plan-2026-10-20.md).
Chronological order is the default unless the brief explicitly asks for narrative
order; requested duration is authoritative with tradeoffs disclosed before rendering;
inference is local only; foundation work comes before further feature selection.
Planner/provider enforcement belongs to later packages.

Implemented F02/F16 and the process-ownership portion of F20:

- Incomplete inventories (missing root, traversal/stat errors, changed/disappeared
  root) fail with a retry message and preserve indexed descendants. Cancellation is
  checked before deletion reconciliation; complete scans still reconcile real deletion.
- Default project removal archives metadata, knowledge, plans, exports, and history.
  Archived projects can be restored, optionally under another display name. Restore
  keeps the original project ID/storage path and works with offline source media.
- New projects use UUID-suffixed storage slugs. Existing paths and pre-existing
  orphan directories remain intact; a same-name project cannot inherit those caches.
- Queued/running jobs block archive/purge. Submission and project lifecycle mutations
  share a lock in the single application runner; stale submissions are rejected.
- Explicit purge checks containment and registered source overlap, deletes storage
  and job history, and preserves its archive record if file removal fails. A partial
  purge may have removed some files; missing knowledge databases cannot be restored.
- Start records per-checkout process identity; Stop verifies PID, creation time,
  executable and command line. Unrecorded services/port owners are left untouched;
  startup readiness remains follow-up work.

Verification: `cd backend; uv run pytest` passed **71 tests**, with **1 slow test
deselected** and no skipped fast tests; `uv run ruff check .` passed. `cd frontend;
npm run build` passed. `powershell -NoProfile -ExecutionPolicy Bypass -File
scripts/test-dev-processes.ps1` passed mocked ownership and syntax checks. The
ownership reader was also verified against the command's own real PowerShell
process without terminating it. No real-model, live-browser, or real-media visual/audio QA
was performed; no existing runtime database or source collection was migrated/scanned.
Known Starlette/httpx and npm `msvs-version` warnings remain. Restart the backend to
load these source changes; this task did not restart the user's running services.

Next package: source revisions, stage invalidation, decoder contracts, and selective
artifact repair (F01/F07 and related findings). Job restart recovery (F03) is still open;
persisted active jobs must become terminal before archive can proceed.

## Earlier handoff context

> 2026-10-20 review: see [`architecture_review.md`](architecture_review.md) for current
> defects and priorities, and [`lessons_learned.md`](lessons_learned.md) for the living
> maintenance record. The August verification below is historical; the review found
> gaps in duration authority, EXIF handling, cache invalidation, and job recovery.

*Last updated: 2026-08-25 session · Read this first in a new context window*

Companion docs: [`implementation-plan.md`](implementation-plan.md) (original phased plan — treat
phases 0–2 as DONE, phase list below supersedes it) · [`tech_stack.md`](tech_stack.md) (what runs
where) · [`../README.md`](../README.md) (run/setup) ·
[`../Notes_&_To_Dos.md`](../Notes_&_To_Dos.md) (lessons learned) · [`../spec.txt`](../spec.txt)
(original vision).

## Where we are

Original Phase 0/1/2 are complete and battle-tested on real footage (14ers hiking project).
We are effectively **mid-Phase 3** of the original plan. Docker was considered and **dropped**
(app runs bare-metal via `scripts/start-dev.ps1`).

### Shipped (all verified on live renders)

- Full understanding pipeline: scan → scenes → frames → Tier-0 CV → SigLIP2 embeddings +
  zero-shot tags → **VLM scene descriptions** (Pass 5, via Unsloth)
- Planner: candidate shortlist → LLM Edit Plan JSON → validation/salvage → persistence
- Renderer: deterministic filtergraph builder, NVENC→x264 fallback, titles, Ken Burns,
  crossfades, loudnorm audio, instant-preview player, proxy/final profiles
- Photos fully supported (HEIC via pillow-heif, EXIF orientation, Ken Burns stills, mixed timelines)
- Chronological ordering enforced (EXIF → filename timestamp → mtime fallback)
- **Exact-duration contract**: requested length = delivered length, frame-exact (±1 frame).
  Fitter passes: stretch → filler clips → shrink; photos unbounded; last-resort `freeze_tail_sec`
  (`tpad` clone + `apad`) guarantees the contract even with only bounded videos.
  **Frame growth/shrink is water-filled** (`_grow_one_frame` grows the shortest clip with
  headroom, `_shrink_one_frame` trims the longest first) — residual time spreads evenly across
  clips instead of piling onto the last one (was: 14-photo reel → 13×~3s + one 9.1s finale).
  Filler photos enter at `PHOTO_DEFAULT_SEC` (2.5s) and get raised to parity by the growth pass.
  **`_evenize_photo_spans`** runs after the fitter: stills are set directly to
  (target + crossfade overlap − video spans) ÷ n_photos, frame-quantized, then a second fitter
  pass nails the exact contract — so uneven LLM-chosen still spans (e.g. a 15.9s finale) can
  never survive, even when the plan already lands on target. Videos keep their natural spans
- Transitions: crossfade is the planner *default* (esp. around photos); cuts reserved for action
- Color: HDR (HLG/PQ) sources tone-mapped via **libplacebo BT.2390** (Vulkan), zscale+hable
  fallback; output tagged BT.709/tv. Quality knobs: proxy 720p@8M, final 1080p@20M, mild unsharp
- UX: native Windows folder picker (tkinter endpoint), clickable project cards, collapsible media
  files, render progress inline on reel card, plan progress inline under Generate button.
  **Single "Analyze" button** on the project page — analyze always runs a folder scan first
  (scan is cheap/idempotent; also purges deleted files). `/scan` API + `scan_project` remain
  standalone backend capabilities
- **Reels grid** (ProjectView): columns Reel # (expand chevron → plan preview + clip list
  subrow), render button (label encodes state: Render / Rendering… / Retry render / Re-render,
  slim inline progress bar), Length (delivered `rendered_duration_sec`, falls back to target),
  created date/time, line-clamped description, model, download icon
- **Download tracking**: `edits.downloaded_at` stamped by the download endpoint when the MP4 is
  served (semantic = "download initiated"); unread rows show accent border + bold description,
  read rows muted. Both new columns via `_ensure_column` migration
  (`edits.downloaded_at`, `edits.rendered_duration_sec`)
- **Project page reflow** (task order): compact inline status dots → Generate reel form → Reels
  list → collapsed "Scene library" accordion (search + tags + thumbs) → collapsed Media files;
  both accordions persist open/closed state in localStorage per project
- **Face-aware photo cropping**: YuNet (cv2.FaceDetectorYN) at render prep on each unique photo
  → face boxes expanded into **head boxes** (relative padding: +60% height above for hair, +30%
  below for chin, +35% sides) → crop window placed to contain the largest detected head fully
  (plus a 5%-of-window safety margin for Ken Burns zoom headroom) while its center still pulls
  toward the area-weighted centroid of all heads; falls back to upper-biased heuristic (cy≈0.40)
  when no faces/model unavailable — render never fails on it. Model auto-downloaded once
  (~230KB ONNX) to `%LOCALAPPDATA%\ReelMaker\models\`. Verified visually on real 14ers/Brumleys
  portraits via annotated-overlay debug renders. Known limitation: strongly posed faces (head
  thrown back, profile in dark backlight) can go undetected at any score threshold — undetected
  people get no crop protection; zero-faces photos use the heuristic.
- **EXIF orientation now applied at render time** (`transpose` per stored files.rotation) — was a
  latent bug: thumbnails were upright but rendered output could be sideways

### Not started / deferred (rest of Phase 3 → Phase 4)

- Faces: YuNet is now wired for **still-photo crop anchoring only**; scene-level face
  attributes/scoring (per-frame video faces, count/size visibility metrics) still open. YAMNet
  audio event tagging + dialogue-aware music ducking
- Manual timeline editor, music library manager, duplicate detection
- Phase 4 backlog unchanged (Whisper, face clustering, TTS narration, maps, plugins…)

## Architecture map (where things live)

| Concern | Path |
|---|---|
| Pipeline passes | `backend/app/pipeline/` (scan, metadata, scenes, frames_cv, describe, analyze, search) |
| AI clients | `backend/app/ai/` (providers.py = OpenAI-compat client; embeddings.py = SigLIP2) |
| Planner | `backend/app/planner/` (candidates, generate, schemas) |
| Renderer | `backend/app/renderer/` (filtergraph.py is the core; encode.py; photo_crop.py YuNet focus points; render.py job handler) |
| APIs | `backend/app/api/` (projects, files, scenes, media, plans, jobs SSE, system) |
| Job system | `backend/app/jobs/worker.py` (asyncio queue) + bus.py (SSE events) |
| UI | `frontend/src/views/ProjectView.vue` (main surface), components/PlanPreview.vue |
| Runtime data | `%LOCALAPPDATA%\ReelMaker\` (registry.db, projects/<slug>/cache+exports, hf cache) |

## Hard-won gotchas (do not relearn these)

1. **Unsloth `/v1/models` lists every *downloaded* model**, not loaded ones. 404 "downloaded but
   not loaded" = load it into a slot in Desktop. Probe script pattern exists in chat history.
2. **transformers v5**: `get_image/text_features` returns model outputs — use `.pooler_output`.
3. **ffmpeg filtergraph**: never mix `'` quoting with `\` escapes; Windows drive-colon paths break
   filters → run ffmpeg with `cwd=exports_dir` and use bare filenames for drawtext textfiles.
   `xfade` requires matching timebases → every clip chain ends `settb=AVTB`. Combined
   video+audio `concat` is pad-order fragile → emit separate `v=1:a=0` and `v=0:a=1` concats.
   Still-image Ken Burns must be single-frame input + `zoompan d=N` from a 4× upscale
   (`-loop 1` + per-frame zoompan shakes); KB-less stills still need `zoompan d=N` identity or
   they collapse to one frame.
4. **Chronological sort must precede duration fitting** — reordering after fitting invalidates it.
5. **Render failures must update BOTH** the jobs row and the `edits.status` row (was stuck-
   `rendering` bug). 30-min ffmpeg timeout guard exists.
6. **UI + vue-query**: destructure `{ data }` from `useQuery` or access `.data.value` in script;
   templates auto-unwrap only top-level refs. SSE can silently die across backend restarts →
   polling fallbacks are load-bearing.
7. **Vite has `strictPort: true`** (5173) — port conflict fails loudly instead of drifting.
8. Backend restart required after Python edits (no --reload): stop/start scripts. **This has
   bitten twice (2026-08-25)**: a fitter fix looked broken because the backend process was a
   day old. Before debugging "the fix didn't work", check the port-8000 listener's process
   start time (`Get-NetTCPConnection -LocalPort 8000`) against when the change was made.
9. **Photo render chain order**: `transpose` (EXIF) → `scale(cover, 4×)` → `crop=W:H:x='clip(cx*iw-ow/2,0,iw-ow)':y=…` → `zoompan`. Crop offsets use single-quoted `clip()` expressions so no pixel math against ffmpeg's scaler is needed; commas are safe *only because* they're quoted (gotcha 3). Rotation convention: files.rotation is degrees-CW-to-display; maps to `transpose=1|1,1|2` for 90/180/270.
10. **Face-detection coords must be in display orientation**: reuse `_load_image_bgr` + `_apply_rotation` from frames_cv (cv2.imread ignores EXIF; the HEIC/PIL path already transposes and reports it via its bool). YuNet runs on a ≤1024px downscale — normalized focus points need no coordinate remapping. Full-res re-detection does NOT recover pose-missed faces (tested). If face crops ever look wrong again: first check `%LOCALAPPDATA%\ReelMaker\models\` actually contains the ONNX — download failures are swallowed into silent heuristic fallback (backend log shows a warning only).

## Verification state

- Backend: 53 fast tests green (`uv run pytest`), ruff clean; `-m slow` runs real-model E2E
  (incl. portrait+rotation+focus ffmpeg render-parse test)
- Frontend: `npm run build` type-checked green
- Live smoke history: analyze(71 files)→described=71; plans→renders with audio/photos/HDR OK

## Suggested next steps

1. ~~User live check of new page order + face-anchored crops~~ **Confirmed working by user
   (2026-08-25)**, including even still pacing after the `_evenize_photo_spans` pass
2. Then resume Phase 3: video-frame faces/attributes, YAMNet audio tagging, manual editor,
   music library
