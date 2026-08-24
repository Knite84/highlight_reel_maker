# Project Status — Handoff Notes

*Last updated: 2025-08-24 session · Read this first in a new context window*

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
  (`tpad` clone + `apad`) guarantees the contract even with only bounded videos
- Transitions: crossfade is the planner *default* (esp. around photos); cuts reserved for action
- Color: HDR (HLG/PQ) sources tone-mapped via **libplacebo BT.2390** (Vulkan), zscale+hable
  fallback; output tagged BT.709/tv. Quality knobs: proxy 720p@8M, final 1080p@20M, mild unsharp
- UX: native Windows folder picker (tkinter endpoint), clickable project cards, collapsible media
  files, render progress inline on reel card, plan progress inline under Generate button

### Not started / deferred (rest of Phase 3 → Phase 4)

- Faces (YuNet + attributes), YAMNet audio event tagging + dialogue-aware music ducking
- Manual timeline editor, music library manager, duplicate detection
- Phase 4 backlog unchanged (Whisper, face clustering, TTS narration, maps, plugins…)

## Architecture map (where things live)

| Concern | Path |
|---|---|
| Pipeline passes | `backend/app/pipeline/` (scan, metadata, scenes, frames_cv, describe, analyze, search) |
| AI clients | `backend/app/ai/` (providers.py = OpenAI-compat client; embeddings.py = SigLIP2) |
| Planner | `backend/app/planner/` (candidates, generate, schemas) |
| Renderer | `backend/app/renderer/` (filtergraph.py is the core; encode.py; render.py job handler) |
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

## Verification state

- Backend: 34 fast tests green (`uv run pytest`), ruff clean; `-m slow` runs real-model E2E
- Frontend: `npm run build` type-checked green
- Live smoke history: analyze(71 files)→described=71; plans→renders with audio/photos/HDR OK

## Suggested next steps

1. User re-render check of exact-duration contract on 14ers project
2. Project-details page UX overhaul (user explicitly wants this next)
3. Then resume Phase 3: faces, audio tagging, manual editor, music library
