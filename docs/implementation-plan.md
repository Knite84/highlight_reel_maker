# Implementation Plan — Local AI Media Understanding & Highlight Reel Generator

*Draft v1 · Aug 2026 · Windows-first, fully local*

## Locked Decisions

| Area | Decision |
|---|---|
| GPU target | RTX 4070 Super 12GB — stages run sequentially, never concurrently |
| LLM/VLM server | Unsloth Desktop, OpenAI-compatible endpoint (`sk-unsloth-…` key). App treats it as *any* OpenAI-compatible provider |
| Scene/planner model | Qwen3-VL 8B GGUF (Q4_K_XL) for both Pass 5 descriptions and planning |
| Embeddings | SigLIP2-base in-process (image embeds + zero-shot tags) — not via API server |
| Knowledge base | SQLite per project (`cache/db.sqlite`) + blob files (thumbs/frames) + sqlite-vec for vectors |
| Checksums | xxHash (fast path: size+mtime; deep hash only when mtime changes) |
| Video GPS/metadata | exiftool (FFmpeg cannot read GoPro GPMF/DJI tracks) |
| Encoding | NVENC AV1 (Ada) for proxies/final; 720p proxy profile for previews |
| Scope | Video-first through Phase 2; photos/faces/audio in Phase 3 |
| Collection size | Target: ≤2h footage (~150–500 scenes) per project |

## Repository Layout

```
backend/            # Python 3.12+, uv-managed
  app/
    api/            # FastAPI routers
    core/           # config (pydantic-settings), logging, db
    pipeline/       # passes 1-6, one module per pass
    ai/             # providers.py (OpenAI-compat client), vlms.py, embeddings.py
    planner/        # candidate selection, plan schemas, LLM planning
    renderer/       # deterministic filtergraph builder + encode profiles
    jobs/           # asyncio worker pool + SQLite-backed job records
frontend/           # Vue 3 + Vite + TS + Pinia + TanStack Query
docs/
samples/            # tiny committed test clips for CI-style tests
Projects/<name>/    # runtime data (gitignored): media refs, cache/db.sqlite,
                    #   thumbnails/, frames/, edits/, exports/, prompts/
```

Global registry: small SQLite in `%LOCALAPPDATA%/ReelMaker/` storing known project paths + app settings (Unsloth URL/key, model IDs). Per-project DB holds everything else — projects stay self-contained/portable.

---

## Phase 0 — Foundation (est. 1–2 weeks)

**Goal:** runnable skeleton with project lifecycle, job system, and dependency checks.

1. Scaffold backend (uv, FastAPI, ruff, pytest) and frontend (Vite template, Pinia, TanStack Query, router).
2. Config layer: paths, Unsloth base URL/API key/model ID via env + settings UI stub.
3. Startup dependency probes: `ffmpeg`, `ffprobe`, `exiftool` on PATH; GPU/NVENC check; Unsloth `/v1/models` health check with clear UI status indicators.
4. Schema v1: projects, files, scenes, jobs tables — **every artifact table gets `model_id` + `stage_version` columns from day one** (provenance rule below).
5. Job system: `jobs` table + asyncio worker (concurrency=1 for AI/GPU stages); progress events via SSE.
6. Project CRUD + folder import (recursive scan → `files` rows) + basic media grid UI.

**Exit criteria:** create project → point at folder → see scanned file list; job progress visible live; all dependency checks green.

## Phase 1 — Understanding Pipeline, Video (est. 3–5 weeks)

**Goal:** a 2h collection fully analyzed into a searchable knowledge base, unattended.

1. **Pass 1 metadata**: ffprobe JSON (duration/resolution/fps/codec/rotation), exiftool sidecar (GPS/camera/dates), xxHash checksums with mtime fast-path.
2. **Pass 2 scenes**: PySceneDetect ContentDetector, threshold tuned toward over-segmentation; merge step utility (min/max duration bounds).
3. **Pass 3 rep frames**: 1 primary + 2 secondary frames per scene (sharpness-ranked mid-scene candidates), stored as JPEG blobs.
4. **Pass 4 Tier 0 CV** (OpenCV, cheap): blur (Laplacian var), brightness, contrast, motion/stability (frame-diff energy), dominant colors.
5. **Embeddings + tags**: SigLIP2 embed each rep frame → sqlite-vec; zero-shot tag bank (terrain/weather/activity/water/etc.) with similarity scores stored as structured fields.
6. **Search API + UI**: hybrid search — tag filters + vector similarity ("waterfall", "kids laughing"); thumbnail grid with scene cards; analysis dashboard (per-pass progress, stats).
7. Resilience: per-file error capture (bad codecs skip + report), resumable passes (idempotent per file), cancellation support.

**Exit criteria:** overnight run processes 2h footage with zero manual intervention; search returns correct scenes for 10 test queries; killing mid-run and restarting loses no completed work.

## Phase 2 — Planner, Renderer, Instant Preview (est. 4–6 weeks)

**Goal:** prompt → edit plan → watchable reel, revisable without re-analysis.

1. **Pass 5 VLM descriptions**: Qwen3-VL per scene (primary frame + 2 context frames), strict JSON output: description, objects, actions, camera motion, people, emotion, scene type, importance/highlight scores. Validate → retry (≤2) → mark failed scenes. Record model ID per row. *(Week-1 spike: verify Unsloth honors `response_format`/JSON-schema constraints; build parse-retry fallback regardless.)*
2. **Candidate selection**: top-K by highlight score + diversity rules (time-of-day buckets, tag coverage, min gap between scenes) → compact scene summaries for planner context (keeps prompts well under context limits).
3. **Planner LLM**: prompt = project digest + candidates + user prompt/pacing/tone/duration → **Edit Plan JSON** (versioned schema: clips w/ file+in/out, transitions, Ken Burns params, title cards, music slot markers). Zod/Pydantic validation both sides; validation errors fed back for one repair round-trip.
4. **Instant preview**: plan → sequential playback of source segments via HTTP range streaming + `<video>` player (seconds, no render). This is the revision-loop backbone.
5. **Renderer**: pure function `(plan, files) → ffmpeg args`; golden-file unit tests for filtergraphs; xfade/zoompan/drawtext/audio-mix modules; NVENC 720p proxy + 1080p final profiles; loudness-normalized music bed w/ auto-fade under titles.
6. **UI**: prompt editor, generation history (plan versions + diffs), preview player, quick-render/export manager.

**Exit criteria:** fresh project → prompt → preview playing within seconds; "make it shorter, more sunset shots" revision re-plans without touching vision caches; exported 1080p reel looks correct (A/V sync, transitions).

## Phase 3 — Photos, Faces, Audio, Editor (est. 4–6 weeks)

1. **Photos**: single-"scene" items, EXIF orientation handling, date/timezone grouping, Ken Burns-native rendering, mixed photo/video timelines.
2. **Faces**: YuNet detection per rep frame → count/size/visibility; smile/eyes-open attribute model; planner prefers faces-visible moments for family edits.
3. **Audio**: per-scene audio extraction → YAMNet-class event tagging (speech/music/birds/applause/wind); speech-presence flag protects dialogue clips from music ducking.
4. **Manual editing layer**: drag trims/reorder in timeline UI → patches plan JSON → instant preview refresh.
5. Music library manager (local folder import, duration/BPM scan); duplicate photo/video detection via embedding similarity; GPS groundwork surfaces in inspector (map UI deferred).

## Phase 4 — Backlog (post-v1)

Whisper transcription → speaker ID; face clustering/recognition; narration TTS; travel journal export; map overlays/route viz; plugin hooks; multi-model A/B experiments; OCR; social-format exports.

---

## Cross-Cutting Standards

- **Provenance/cache keys**: every artifact keyed by `(file_xxhash, stage_name, stage_version, model_id, params_hash)`; swapping any model invalidates only its dependents' rows. No silent staleness.
- **Determinism**: seeded everywhere; renderer is a pure function; plans carry schema version + seed.
- **Testing**: committed `samples/` micro-collection drives pytest integration tests per pass; golden filtergraph snapshots; schema round-trip tests; one smoke E2E (scan→analyze tiny set→plan→proxy render) runnable locally.
- **Security**: servers bind 127.0.0.1 only; Unsloth key stored in user profile, never logged.
- **Windows hygiene**: long-path enablement documented; all path joins via pathlib; installer checklist (winget ffmpeg/exiftool).

## Top Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Unsloth drops JSON-schema constraint passthrough | Week-1 spike; parse-retry fallback designed-in |
| VLM descriptions too slow/vague at 8B Q4 | Tune frame count/quality per call; fall back to SigLIP-tag-driven plans that still work |
| FFmpeg filtergraph complexity explodes | Renderer stays pure-function + golden-tested; features added incrementally (xfade → zoompan → audio) |
| VRAM contention | Strict sequential stage scheduler; single shared VLM instance |
| Scope creep (spec's Pass 4 wishlist) | Tiered CV; anything not in a phase goal goes to Phase 4 backlog |
