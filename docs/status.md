# Project Status — Handoff Notes

*Last updated: 2025-08-24 session (2) · Read this first in a new context window*

Companion docs: [`implementation-plan.md`](implementation-plan.md) (original phased plan — treat
phases 0–2 as DONE, phase list below supersedes it) · [`../README.md`](../README.md) (run/setup) ·
[`../spec.txt`](../spec.txt) (original vision).

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
  Filler photos enter at `PHOTO_DEFAULT_SEC` (2.5s) and get raised to parity by the growth pass
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
8. Backend restart required after Python edits (no --reload): stop/start scripts.
9. **Photo render chain order**: `transpose` (EXIF) → `scale(cover, 4×)` → `crop=W:H:x='clip(cx*iw-ow/2,0,iw-ow)':y=…` → `zoompan`. Crop offsets use single-quoted `clip()` expressions so no pixel math against ffmpeg's scaler is needed; commas are safe *only because* they're quoted (gotcha 3). Rotation convention: files.rotation is degrees-CW-to-display; maps to `transpose=1|1,1|2` for 90/180/270.
10. **Face-detection coords must be in display orientation**: reuse `_load_image_bgr` + `_apply_rotation` from frames_cv (cv2.imread ignores EXIF; the HEIC/PIL path already transposes and reports it via its bool). YuNet runs on a ≤1024px downscale — normalized focus points need no coordinate remapping. Full-res re-detection does NOT recover pose-missed faces (tested). If face crops ever look wrong again: first check `%LOCALAPPDATA%\ReelMaker\models\` actually contains the ONNX — download failures are swallowed into silent heuristic fallback (backend log shows a warning only).

## Verification state

- Backend: 51 fast tests green (`uv run pytest`), ruff clean; `-m slow` runs real-model E2E
  (incl. portrait+rotation+focus ffmpeg render-parse test)
- Frontend: `npm run build` type-checked green
- Live smoke history: analyze(71 files)→described=71; plans→renders with audio/photos/HDR OK

## Suggested next steps

1. User live check on 14ers project: new page order + proxy render to eyeball face-anchored
   crops (first render downloads the YuNet ONNX once; offline machines just get the heuristic)
2. Then resume Phase 3: video-frame faces/attributes, YAMNet audio tagging, manual editor,
   music library
