# Independent audit and proposed remediation plan

Date: 2026-10-20. Reviewed checkout: `fc9938e` (`architectural review cleanup phase 1.`).
Status: **package 1, Docker/lifecycle foundation and the source/cache portion of package 2 implemented; remaining work is proposed**.

## Accepted decisions and package 1 handoff (2026-10-20)

The user approved chronological sequencing by default, with narrative order only
when explicitly requested in the brief. Requested duration remains authoritative;
disclose longer stills/freezes/other tradeoffs before rendering. Inference must be
local only. Complete foundational fixes before revisiting feature priorities.
Project removal archives knowledge by default, with separate purge and recovery.
The planner/provider changes needed to enforce these policies belong to later packages.

Package 1 now fails incomplete inventories before destructive reconciliation;
guards cancellation/root loss before reconciliation; archives project registry
metadata with explicit restore/purge APIs and UI; allocates UUID-suffixed immutable
storage directories for new projects; excludes queued/running work from archive/purge;
serializes submission with archive/restore/purge in the single application runner;
and makes developer Stop verify recorded process identity instead of killing port owners.
Existing project paths and source media are preserved. Previously orphaned directories
are not automatically imported or removed. See `status.md` for current verification.

## Approved operational foundation follow-up (2026-10-20)

The user reprioritized shutdown/recovery and Docker before source invalidation.
Web app, FFmpeg and SigLIP run in Docker; Unsloth stays external on Windows. Files
remain in host bind mounts. Compose ownership/readiness supersedes the interim PID
launcher described above. F03/F04 and subprocess portions of A01/A02 are addressed;
F10/F14/F15/F17 have partial improvements. Full frontend state recovery remains open.
See [status.md](status.md) for cross-platform verification and [docker.md](docker.md)
for operation/recovery. A real NVENC export exposed Docker libplacebo block artifacts; container rendering
now uses CPU zscale with NVENC encoding. Three corrected frames were clean of blocks,
but the more washed-out appearance needs broader HDR color acceptance. All other audit contracts remain in the backlog.

## Assessment

Scoped follow-up 2026-10-20: A03's HEIC initialization and A04's frame evidence
ordering are implemented and verified with fresh subprocesses and captured provider
inputs. Nullable timestamps preserve legacy rows; unknown chronology is disclosed.
Tracked descriptions selectively upgrade to contract v2, retaining history and
frame/embedding caches. Broader decoder/orientation normalization remains F07.
See [status.md](status.md) for 115-test cross-platform evidence and QA limits.

Implementation checkpoint 2026-10-20: source revisions, deep verification, stage/model
contracts, selective missing-artifact repair, retained history/generations and protected
legacy migration now address F01/A05. Referencing stale plans are blocked while prior
exports remain downloadable. See [cache-provenance.md](cache-provenance.md) and
[status.md](status.md) for limitations and 110-test cross-platform evidence. F07/A03/A04
(orientation, fresh-process HEIC and chronological frame evidence) remain next, along
with source/scene identity in the full versioned edit-plan contract. Actual weight
revision/checksum enforcement is still F17; contract bumps remain explicit.

Keep Vue, TypeScript, FastAPI, SQLite, FFmpeg, and the separation between analysis,
planning, and rendering. The main weaknesses are inconsistent contracts and missing
failure-path integration, rather than fundamentally unsuitable technologies.
Rewriting the application would preserve those problems unless the contracts change.

The most urgent risk is losing or silently reusing incorrect media knowledge.
The next is producing, or reporting, an output that does not satisfy the request.
Passing unit tests currently provides less assurance than the documentation implies:
the suite misses several faults at the boundaries between otherwise tested functions.

This plan covers source inventory, analysis, storage, model integration, planning,
rendering, API boundaries, frontend behavior, developer operations, dependencies,
unused code, and documentation. It assesses the original platform intent in
[`spec.txt`](../spec.txt), not just whether a short reel can be generated.

## What happened to the earlier audit?

[`architecture_review.md`](architecture_review.md) contains 21 findings, verification
results, a completed cleanup inventory, implementation sequencing, and discussion
questions. The current Git commit includes that report and the reported narrow code
removals. It is a coherent, committed audit deliverable, not merely an unfinished
outline. I cannot establish what its author intended to do after connectivity failed.

Most findings still apply. The cleanup did **not** implement the reliability fixes.
This review independently traced their current call paths and reproduced several
failures. Use the earlier F01–F21 identifiers below to avoid creating competing
backlogs; additional findings use A01–A10.

One earlier claim needs correction: F19 implies repeated provider construction in
descriptions. `get_provider()` is decorated with `lru_cache(maxsize=1)`, and a fresh
probe confirmed identity reuse. The unset `provider_holder["provider"]` is redundant
bookkeeping, not evidence that every file creates a new HTTP client. Missing lifespan
closure, shared settings ownership, and blocking model initialization remain valid.

## Original audit evidence and limits (before implementation)

Checks run in this review:

| Check | Current result |
|---|---|
| `cd backend; uv run pytest` | 53 passed; 1 slow test deselected; no fast tests skipped |
| `cd backend; uv run ruff check .` | Passed |
| `cd frontend; npm run build` | Passed, including TypeScript checks |
| `cd frontend; npm audit --json` | Completed, exit 1: 3 high-severity package entries representing 2 underlying advisories; see A10 |
| Changed-source probe, temporary SQLite database | New bytes/size retained `analyzed_at='old'`, `xxhash='oldhash'` |
| Missing-root scan probe, temporary database | Returned zero media and removed the previously indexed file |
| Fake planner, request 45s/model response 12s | Final plan target remained 12s |
| Real EXIF-6 synthetic JPEG | Decoder returned already-rotated pixels but `already_transposed=False` |
| Render builder using the handler's title argument | Raised `AttributeError: 'str' object has no attribute 'as_posix'` |
| Generated FFmpeg command | Contains no `-progress` option |
| Fresh-process HEIC metadata extraction | `UnidentifiedImageError` before frame processing |
| Provider identity probe | Repeated `get_provider()` calls return the same object |

Synthetic probes used disposable files and databases; no existing runtime database
or user media was repaired, rescanned, or migrated. Initial sandbox access to the uv
cache/Python runtime failed; the checks and probes subsequently ran with approved
access. The suite emitted a Starlette/httpx deprecation warning; npm emitted an
`msvs-version` configuration warning.

Not performed: slow real-model integration, live browser interaction, representative
real-media visual/audio QA, load benchmarks, crash/restart experiments against the
running application, exhaustive security testing, or Python dependency advisory
scanning. Code-traced findings below are not claims that every failure was reproduced.
The existing fast suite includes synthetic FFmpeg renders, which do not substitute
for viewing the user's actual media. No new tests or application code were added.

## Existing findings: disposition and priority

P1 means foundational correctness/data protection before expanding features.
P2 means the next reliability or usefulness iteration. P3 means maintenance.
These are implementation priorities, not CVSS security scores.

| Finding | Assessment against current source | Recommended destination |
|---|---|---|
| F01 — source/cache invalidation | Confirmed by probe. Stage presence is not freshness; missing artifact files also evade presence checks. | P1, work package 2 |
| F02 — incomplete inventory deletes knowledge | Implemented in package 1; missing-root, subtree, stat, late-root-loss, and cancellation regressions. | Complete for this slice |
| F03 — restart recovery | Confirmed by startup/worker inspection: queued handlers are memory-only; old active rows remain. | P1, package 3 |
| F04 — render lifecycle/output replacement | Confirmed by control flow. Preparation and missing-output failures bypass edit failure updates; `-y` overwrites an existing profile output. | P1, package 4 |
| F05 — request duration authority | Confirmed by fake-provider probe. Persisted request and normalized plan can disagree. | P1, package 5 |
| F06 — scene identity | Confirmed: first matching path supplies bounds although a video has several scene candidates. | P1, package 5 |
| F07 — orientation | Reproduced for EXIF 6. Mirrored orientations are not represented by degree-only metadata. Video decoder behavior also needs verification. | P1, package 2 |
| F08 — relevance omitted from shortlist | Confirmed: quality ranking chooses 60 before the brief reaches the planner. | P2, package 6 |
| F09 — fitting policy/chronology | Confirmed: filler follows sorting; freeze fallback and repeated fitting alter intent without explanations. | P2, package 5 |
| F10 — incomplete analysis reported as done | Confirmed: description failures can be omitted from summary; UI does not expose structured results; Analyze discards scan counts. | P1 for honest reporting, package 3 |
| F11 — conflicting UI job states | Confirmed by watchers/store inspection; no durable hydration and stale SSE takes precedence. | P1 for reliable recovery, package 3 |
| F12 — source preview | Confirmed: photos are assigned to a video element; Stop does not stop media; pending loads lack error/unmount handling. | P2, package 7 |
| F13 — final export unavailable in UI | Confirmed: UI always requests proxy; one edit render pointer hides artifact history. | P2, package 7 |
| F14 — capability probing | Confirmed: compiled NVENC presence is treated as availability; HDR probe does not exercise the generated media filter. | P2, package 4 |
| F15 — cancellation/process handling | Confirmed by control flow: cooperative flags do not interrupt model/render work; stderr is drained after wait. | P1, packages 3–4 |
| F16 — delete/recreate identity | Implemented in package 1 with archive/recovery, new storage identity, active-job exclusion, and guarded purge. | Complete for this slice |
| F17 — local/offline promises | Confirmed: configurable remote inference can receive images; weights may download at first use. | P1 policy boundary, package 8 |
| F18 — search/browsing limits | Confirmed: only embedded scenes; newest 4,000 before ranking; missing pagination/completeness messages. | P2, packages 6–7 |
| F19 — async/resource ownership | Partly corrected above; blocking model loading/tag inference and unclosed cached provider remain. | P2, packages 3 and 8 |
| F20 — scripts/test coverage | Process ownership protection implemented in package 1; readiness and browser suite remain open. | Remaining work in package 9 |
| F21 — unsupported schema/docs | Confirmed: music ignored, seed 0 replaced, no model seed passed, duplicated frontend contracts. | P2 contracts / P3 cleanup, packages 5 and 9 |

## Additional findings

### A01 · P1 · Titles fail at the render integration boundary

Evidence: `renderer/render.py::render_job_handler` passes
`title_textfile_abs.name`, a string, to `build_render_args`; the downstream
`filtergraph.py::_title_filters` calls `.as_posix()`. The direct probe reproduces the
exception. Existing title tests pass a `Path`, bypassing the faulty caller.
This also hits F04: the edit has already been marked rendering.

There is a second, code-traced issue: `_write_title_textfile` writes both title and
subtitle into the main text file, while `_title_filters` draws the subtitle separately.
Once the type mismatch is fixed, the subtitle is included twice in the graph's text
sources. A rendered frame must verify final placement and escaping.

Recommendation: one explicit title input contract, separate title/subtitle sources,
and a test through the actual render handler. Keep relative filenames with the
export working directory to preserve the existing Windows escaping protection.

### A02 · P2 · FFmpeg progress is never enabled

Evidence: `render.py::_pump_progress` expects `out_time_ms=` on stdout, but
`filtergraph.py::build_render_args` supplies no `-progress pipe:1`.
The command probe confirms the missing option. Job completion can still occur;
the promised incremental render percentage cannot come from this parser.

Recommendation: enable machine-readable progress and drain both process streams
concurrently. Verify intermediate events and final completion through the job API.
[FFmpeg's documented progress option](https://www.ffmpeg.org/ffmpeg.html)
supports the intended protocol.

### A03 · P1 · HEIC support depends on process initialization order

Evidence: `metadata.py::extract_metadata` opens photos with PIL before the fallback
decoder in `frames_cv.py` registers the HEIF opener. A fresh process with a synthetic
HEIC failed metadata extraction. `test_process_photo_heic` explicitly registers the
opener and calls frame processing directly, so it cannot detect this pipeline failure.

Recommendation: initialize supported decoders deterministically, and test HEIC from
scan through metadata and visuals in a fresh process. Audit the full advertised
extension list, including JXL/AVIF/TIFF and FFmpeg render support. Recognizing an
extension does not prove every stage can decode it.

### A04 · P2 · Vision receives frames in the wrong stated order

Evidence: `frames_cv.py::process_video` samples three times, sorts frames by sharpness,
then stores primary/secondary roles without timestamps. `describe.py` supplies primary
first and secondary frames afterward while explicitly claiming chronological order.
Its secondary query has no ordering guarantee.

Recommendation: persist each sampled frame's source timestamp and order the model's
inputs by time. Preserve the sharpest frame as the thumbnail independently. Verify
with a scene where the sharpest frame occurs last and a fake provider captures input
order. This matters for descriptions of actions and motion.

### A05 · P1 · Failed frame writes can become permanently cached success

Evidence: `_write_jpeg` ignores `cv2.imwrite`'s boolean return. If no video frames
decode, `process_video` still returns an entry; `_store_scene_visuals` can insert a
primary row with a null path. `_count_missing_visuals` counts absent rows, not absent
paths/files. The next analysis can therefore skip repair even without a usable image.
These are code-traced failure paths; disk-full behavior was not exercised.

Recommendation: validate decode and write success before publishing a stage result;
write artifacts atomically and check them on reuse. Preserve recoverable state and
report the precise failed stage. Test false write returns, no decoded frames, and
deleted files. Fold this into F01 rather than creating a second repair mechanism.

### A06 · P2 · The 500-file list cap silently removes preview segments

Evidence: `api/files.py::list_files` defaults to 500; `ProjectView.vue` requests only
that first page. `PlanPreview.vue` maps paths through that response and silently filters
out unmatched clips. Plans may use any analyzed source, so a valid clip beyond file
500 disappears from preview. This is more consequential than missing UI pagination.

Recommendation: return source identity/media URLs with plan detail, or fetch exactly
the referenced files. Paginate the library separately. Show an explicit error for
unresolvable clips. Test a 501-file collection with the plan referencing the last file.

### A07 · P2 · Admission and worker survival need stronger boundaries

Evidence: API routes check for an active job and then enqueue in separate awaited
operations. Two concurrent requests can both pass the check; the single worker
serializes execution but does not make admission atomic. Project name/slug checks
likewise precede insertion, so concurrent duplicates can produce an uncaught database
constraint error rather than a controlled conflict.

In `jobs/worker.py::_loop`, the initial running-state patch is outside the handler's
exception boundary. Failure to persist that transition can terminate the sole runner;
there is no supervision/readiness signal for that condition. These are inspection
findings; concurrency/database-fault injection remains to be performed.

Recommendation: atomic admission keyed by project/operation (with an explicit policy
for conflicts across scan/analyze/render), controlled uniqueness conflicts, and a
supervised runner that stops accepting work if durable state cannot be maintained.
Do not replace SQLite with a distributed queue to solve this.

### A08 · P2 · Expensive work and ranking are not consistently reusable

Evidence: `render_job_handler` recomputes photo focus on every render; detector
construction occurs per photo and no focus artifact is persisted. Rendering therefore
still performs inference despite the specification's AI-independent renderer goal.
Separately, `candidates.py::_percentile_rank` scans all blur values for every scene:
ranking is quadratic in collection size before the shortlist is bounded.

Recommendation: persist reusable face detections with source/decoder/model provenance
and derive profile-specific focus from them. Benchmark before building a broad model
service. Compute quality percentiles once per candidate set, and measure cold/warm
analysis, search, planning, and render time at realistic collection sizes. CPU SigLIP2
is not inherently a poor choice when it avoids competing with the local VLM for VRAM.

### A09 · P2 · Maintenance contracts need to support the proposed repairs

Evidence: migrations are additive column checks with no explicit schema version or
backup/restore workflow. Runtime data is outside Git. Changing only `data_root` leaves
`projects_root` at its independent default. README's Node “20+” requirement is broader
than the locked Vite/plugin engines (`^20.19.0 || >=22.12.0`).

Recommendation: introduce versioned transactional migrations before identity/schema
changes; test restoration of registry, project databases, and referenced artifacts.
Use SQLite-aware backups for open WAL databases, not blind copies of the main DB
file. Make storage-root precedence explicit and align setup with the locked engines.
See the [SQLite backup API](https://www.sqlite.org/backup.html) and
[Vite requirements](https://vite.dev/guide/).

### A10 · P2 · Frontend dependencies have reported advisories

The current `npm audit --json` reports three high-severity package entries:
`@vue/server-renderer`, its parent `vue`, and `source-map-js`. These represent two
underlying advisories, not three independently demonstrated application exploits.

- [Vue server-renderer advisory](https://github.com/advisories/GHSA-g2v6-rqmx-r4w6):
  affected versions include the locked 3.5.41; the stable fix is 3.5.42. The inspected
  application uses client-side Vue and has no SSR route, so this audit did not establish
  an executable application path to the vulnerable SSR behavior.
- [source-map-js advisory](https://github.com/advisories/GHSA-68fv-2mgg-jv7q):
  affected range reported as `>=1.0.0 <1.2.2`, with a fix available. Assess exposure to
  untrusted source maps in development/build tooling; no exploitation was attempted.

Recommendation: a narrow lockfile/dependency update with clean install, build, audit,
and browser smoke verification in package 9. Record reachability separately from
scanner severity, and add Python dependency advisory review before the next release.
No dependencies were upgraded during this planning pass.

## Unused code and misleading feature surface

Repository-wide reference searches support a small cleanup, not a large deletion:

| Item | Disposition |
|---|---|
| `pipeline/metadata.py::run_tool` | No repository consumers found; candidate for removal after confirming no supported external import contract. |
| `_clip_video_chain`'s `clip_index` argument | Passed but not read; remove the parameter and update its call site together. |
| `api/projects.py::_project_or_404` | Used forwarding wrapper, not dead code; simplify to shared helper when touching these routes. |
| `provider_holder["provider"]` lookup | Never populated by the production caller; simplify during provider lifecycle work. |
| `EditPlan.total_clip_seconds` | Used by tests; a derived domain convenience, not automatically safe to delete. |
| Music schema | Accepted/persisted but unexecuted. Exclude from new supported generation or clearly reject unsupported use; keep old-plan readers. |
| Seed metadata | Recorded but not supplied to inference; define reproducibility honestly and preserve explicit zero. |
| Final profile, standalone Scan API, migration fields, description metadata | Retain. Lack of a UI consumer does not establish that a persisted/public contract is unused. |
| Earlier report's six cleanup groups | The named removals are reflected in the current tree; do not repeat them as outstanding tasks. |

Ruff passing does not prove dead-code freedom: current lint configuration is narrow,
and unused functions/arguments are not all detected. Avoid deleting cached knowledge,
exports, or model data as “cleanup.” No unused dependency was established strongly
enough to recommend removing it in this audit.

## Technology decisions

| Area | Recommendation and rationale |
|---|---|
| Vue + Query + Pinia | Keep. Query owns fetched collections; one clearly defined job state owner merges snapshots/events. The problem is duplicate state ownership, not the presence of both libraries. |
| FastAPI + asyncio | Keep. Move blocking model/CPU operations off the event loop with bounded execution; threads alone do not provide hard cancellation. |
| SQLite + plain SQL | Keep for a local, mostly serialized workflow. Add migration/version/transaction discipline and recoverability; an ORM is optional, not the fix. |
| In-process worker + SSE | Keep initially. Add durable lifecycle reconciliation, atomic admission, queue bounds, active-job queries, and refresh after reconnect. Redis/Celery is not justified by measured demand here. |
| NumPy vector search | Keep while measuring. Remove silent truncation; batch over the whole eligible corpus or use a local index if measured latency/memory warrants it. The original sqlite-vec proposal is not a current dependency. |
| FFmpeg + OpenCV + Pillow/HEIF | Appropriate capabilities, but decoder orientation, supported formats, timestamps, and output validation need shared contracts. |
| H.264/AAC outputs | Reasonable compatibility choice despite the original AV1 proposal. Do not switch encoders merely to match an outdated design document. |
| OpenAI-compatible HTTP provider | Keep the narrow adapter. Use provider-neutral configuration aliases and explicit planning/vision selection; unknown preferred models should not silently select another model. |
| Local model choices | Evaluate with representative prompts/media before replacing them. A larger VLM cannot recover relevant scenes excluded by retrieval or fix incorrectly ordered inputs. |
| Bare-metal Windows development | Reasonable for native media tools/GPU integration. Fix ownership/readiness and dependency setup first. Packaging is later work; Docker is not required. |
| Test/tooling choices | Add focused API/handler/browser tests and backend type checking at boundaries. Lockfile-based clean installs and CI give more value than a generic architecture framework. |

## Proposed implementation sequence

Sizes are relative review scopes, not delivery commitments: S = narrow change,
M = several coordinated modules, L = schema/data/UI migration. Split L packages into
separately reviewable changes. Each package includes regression tests and documentation.

### 1. Protect existing knowledge and project ownership — M

Scope: F02, F16; immediate process-ownership protection from F20.

- Preserve unseen media on incomplete inventories; report unavailable scopes.
- Define archive/remove/purge semantics and exclude active work from destructive removal.
- Allocate immutable project storage identity; never reopen a retained directory just
  because its display name/slug is reused.
- Make Stop identify owned processes rather than kill arbitrary port listeners.

Acceptance: missing root, unreadable subtree, and stat failure preserve knowledge;
verified intentional deletions follow the chosen retention policy; delete/recreate
cannot inherit another collection; unrelated processes survive Stop.
Do not purge existing retained folders as part of migration.

### 2. Establish source, decoder, and stage contracts — L

Scope: F01, F07, A03–A05, A09. Establish migration/backup support before schema changes.

- Persist source revisions and stage provenance: input identity, stage/model revision,
  parameters, success status, and output artifact references.
- Define quick change detection and an explicit deep-verification option for changes
  that preserve size/mtime. Validate the source again before publishing analysis.
- Invalidate only descendants of a changed input/contract. Model changes must not
  force scene detection; missing thumbnails must not force every model call.
- Normalize image/video orientation once; initialize HEIF support before metadata.
- Preserve frame timestamps, validate writes, and replace derived artifacts safely.

Acceptance: unchanged rescan makes no inference calls; changing one source rebuilds
only its descendants; missing artifacts repair selectively; model/decoder upgrades
invalidate the right stages. Fresh-process HEIC and EXIF 1–8 fixtures pass.
Existing knowledge without enough provenance is marked legacy/unverified, not erased.

### 3. Unify durable job lifecycle and frontend state — L

Scope: F03, F10, F11, F15, relevant F19, A07.

- Reconcile orphaned queued/running jobs and associated edits at startup.
- Define complete, partial, failed, interrupted, and cancelled outcomes. Existing DB
  CHECK constraints and frontend unions need migrations/adapters if states are added.
- Persist retry inputs; retry explicitly and idempotently. Commit required domain
  updates before reporting completion.
- Enforce atomic admission; bound pending work and supervise the worker.
- Hydrate active jobs by project, not only the most recent global 50/200 records.
  Merge SSE/polling by monotonic revision and run one terminal refresh handler.
- Surface stage coverage, errors, and retries; implement cancellation latency contracts
  for model requests and subprocesses; expose Cancel in the UI.

Acceptance: restart, reload, disconnect, concurrent submissions, and cancellation
produce consistent job/edit/UI state. Failed planning remains visible. A persistence
failure does not silently strand all subsequent work.

### 4. Make rendering transactional and observable — M

Scope: F04, F14, remaining F15, A01, A02.

- Cover validation through output verification with one attempt lifecycle.
- Fix title argument/duplicate subtitle handling; preserve Windows path protections.
- Render to attempt-specific temporary files; probe duration, streams, and resolution;
  publish only validated outputs and preserve the last good artifact.
- Enable progress; drain bounded stderr concurrently; use preparation/encode timeouts;
  terminate and reap subprocesses and reader tasks on every exit.
- Exercise a representative encoder/HDR graph; retry only classified capability errors.

Acceptance: fault injection at every preparation/encode/publish phase terminates
cleanly without losing the last good export. A titled reel renders through the handler;
subtitle appears once; progress advances; unusable NVENC selects CPU; HDR fallback is
actually exercised. Validate output timing against a documented tolerance.

### 5. Version and enforce the edit-plan contract — L

Scope: F05, F06, F09, F21.

- Make request values authoritative before fitting; validate after all repairs.
- Introduce stable scene/source-revision references in plans; support legacy v1 readers.
  Do not guess the first scene for ambiguous old clips.
- Extract shared frame/timing arithmetic into a domain module independent of FFmpeg.
- Normalize selection, ordering, and timing in that order; disclose filler/freezes.
- Define unsupported fields, seed semantics, and UI/API duration limits consistently.

Acceptance: model target mismatch cannot override the request; a later scene from one
video survives; filler respects ordering policy; insufficient footage produces a
visible decision/warning; rendered duration agrees with request. Old plans remain
readable and stale-source plans get an explicit revalidation path.

### 6. Make the knowledge base useful and measurable — M

Scope: F08, F18, A04, A08.

- Combine semantic relevance, quality thresholds, diversity, and optional chronology
  when retrieving candidates. Record why candidates were selected.
- Separate browse-all from semantic search; show indexed/total/stale/failed coverage.
- Remove the pre-ranking corpus cap, optimize percentile calculation, debounce queries,
  and measure latency/memory before selecting a vector index.
- Cache face detections after the source/provenance foundation is in place.

Acceptance: relevant material beyond both the top-60 quality list and newest-4,000
search window is retrievable. Browse works without embeddings. Evaluate prompt
relevance and diversity on agreed collections; repeated render reuses face artifacts.

### 7. Deliver honest preview and final export — M

Scope: F12, F13, UI parts of F18, A06.

- Label source sequence preview accurately; use images for photos and normalized
  browser-compatible previews where required.
- Implement load errors, bounded waits, Stop, unmount cleanup, and plan changes.
- Resolve referenced sources independently of library pagination.
- Offer separate Preview and Export actions with visible quality/profile.
- Store artifacts by edit/profile/attempt, including measured output properties;
  keep download initiation distinct from completion and from a later replacement.

Acceptance: JPEG/HEIC/video/mixed sequences work; unsupported codecs explain recovery;
Stop stops playback; a source after file 500 previews; proxy and final outputs coexist.
Keyboard navigation and accessible labels/statuses are verified in the browser.

### 8. Enforce local operation and capability readiness — M

Scope: F17, remaining F19, A09.

- Default to strict local inference, consistent with the specification. Treat remote
  support as a separate explicit product decision before allowing media transmission.
- Define endpoint/redirect/proxy behavior, bind/host/origin boundaries, and artifact
  path containment consistently. Media/thumb endpoints already resolve containment;
  renderer/download paths deserve the same explicit policy.
- Pin/check model artifacts and provide preparation/offline readiness.
- Let application lifespan own cached HTTP/model resources and close them cleanly.
- Test actual model capability rather than calling listed models “loaded.”
- Make data-root overrides and environment configuration deterministic/testable.

Acceptance: remote endpoints are rejected in local mode; a prepared offline run needs
no downloads; missing tools/models are actionable; health/progress stays responsive
during cold model loading. This is targeted local-app hardening, not a claim of
comprehensive penetration testing or suitability for network exposure.

### 9. Consolidate maintenance and release evidence — S/M

Scope: F20, maintenance parts of F21, A09–A10 and the cleanup table.

- Add clean-install CI for fast backend checks/frontend build and focused browser
  regressions. Keep optional real-model checks separate.
- Align Node/tool prerequisites with lockfiles; use `npm ci` and frozen uv installs
  for repeatable validation. Review dependency warnings/advisories before changing
  versions; do not make blanket upgrades to silence warnings.
- Add readiness/build identity and owned-process tracking to launch scripts.
- Remove confirmed unused helpers/parameters; split ProjectView by concrete
  responsibilities after state ownership is stable.
- Correct README promises and status links; distinguish shipped behavior, historical
  observations, open defects, and original design proposals.

Acceptance: a clean Windows setup follows documented commands successfully, an
unrelated port conflict fails clearly, and release evidence records exactly which
checks and real-media scenarios passed.

## Compatibility and rollout rules

Before changing storage, take a restorable, consistent snapshot of registry/project
databases and their artifact references. Prove restore on disposable copies first.
Do not copy or modify original media as part of migrations. Keep old schema/plan
fixtures and API compatibility tests. Preserve expensive legacy analysis while marking
uncertain freshness explicitly; regenerate only when the new contract requires it.

Prefer additive schema changes with explicit version checks. Keep old render artifacts
reachable even if the current attempt fails. Maintain a source-revision check between
planning and rendering so a plan cannot silently render replaced footage. Apply
cleanup only after reference checks across stored plans, API callers, and artifacts.

For every package: run relevant regression tests, the backend pytest/Ruff baseline,
and frontend build when frontend/contracts change. Media changes also require a
small viewed/listened-to fixture collection: rotated/mirrored photos, HEIC, portrait
subjects, mixed photos/video, silent/video-audio clips, VFR/phone rotation, HDR/SDR,
titles, and Windows paths with spaces/punctuation. Record probe output properties,
visual/audio observations, selected capabilities, and degraded behavior.

## Lessons/documentation maintenance proposed with implementation

The original audit changed only this plan. Package 1 subsequently updated LL01,
LL06, and LL09 with implementation evidence and regressions. Carry the remaining
evidence-backed updates into `lessons_learned.md` as the relevant packages are implemented:

- LL05: add the fresh-process HEIC failure and require decoder initialization before
  metadata, plus real EXIF orientation tests.
- LL06: add missing-root deletion reproduction and unchecked/absent frame artifact
  paths; require validated artifact publication and safe reconciliation.
- LL07/LL11: add the title Path/string integration failure and missing progress flag;
  require testing the real handler-to-command boundary, not only graph helpers.
- Add a lesson on frame timestamp provenance: sharpness ranking must not silently
  reorder evidence presented as chronological.
- Correct the prior F19 provider-construction claim; keep lifecycle closure work.

Each update should include this date, evidence above, prevention rule, and the actual
regression added. Update `tech_stack.md` only as responsibilities/dependencies change;
do not describe this proposed architecture as already implemented.

## Decisions for our review

1. Keep chronological sequencing mandatory, or allow the brief to select narrative order?
2. For insufficient suitable footage, prefer shorter output, longer stills, or explicit
   permission for freezes/filler? Recommendation: disclose the tradeoff before rendering.
3. Keep strict local-only inference? Recommendation: yes, matching the original spec.
4. Prioritize a dependable reel workflow or deeper archive inspection next?
   Recommendation: foundational fixes first, then a complete preview/final-export path
   with relevance-aware retrieval.
5. Should project removal archive knowledge by default? Recommendation: yes, with a
   separately described purge action and explicit recovery.

Recommended first implementation slice: **safe inventory reconciliation (F02), with
missing-root/partial-walk regressions**. It directly protects accumulated analysis,
is relatively contained, and does not depend on creative-policy decisions. The
titled-render correction is a suitable small parallel backlog item, but should
include the real handler test and complete failure-state handling.
