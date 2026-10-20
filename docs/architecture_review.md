# Architecture and product effectiveness review

> Scoped follow-up 2026-10-20: HEIC initialization and frame evidence chronology
> (A03/A04 in the remediation plan) are implemented. F07 orientation normalization
> remains open. See [status.md](status.md) for current evidence and limitations.

> Source/cache checkpoint 2026-10-20: F01/A05 now have source revisions, stage/model
> contracts, selective artifact repair, history preservation and protected migration.
> Legacy provenance remains unverified; F07/A03/A04 and full scene/plan identity are
> still open. See [cache-provenance.md](cache-provenance.md) and [status.md](status.md).

> Remediation update 2026-10-20: F02/F16, F03/F04 restart/render safety and
> Docker are implemented. F20 uses Compose ownership/readiness. Subprocess portions
> of A01/A02 and parts of F10/F14/F15/F17 are addressed. Docker HDR block artifacts were removed via CPU zscale tone mapping;
> broader HDR color acceptance remains open. Findings below retain original evidence; [status.md](status.md) records
> actual current behavior and verification.

Reviewed: 2026-10-20. Scope: repository source, original `spec.txt`, setup scripts,
documentation, backend tests, and frontend build. This is a code-based assessment;
the current real-media collection and a live browser session were not exercised.
Existing local edits to README, status, notes, and the stack inventory were preserved.

## Assessment

The project has a sound basic decomposition, but several cross-layer contracts are
weaker than the successful happy path suggests. Keep the Vue/FastAPI/SQLite stack
and the separation between analysis, planning, and rendering. A rewrite or a larger
infrastructure stack would add cost without fixing the main problems.

The biggest problem is trust: the UI can say an operation completed while knowledge
is stale or incomplete, previews can fail for supported photos, and the requested
duration can be replaced by the model's duration. Much of the accumulated complexity
is in repairs and parallel representations of state, rather than in reusable media
understanding. Stabilize these contracts before adding more AI features.

The review uses the original intent: a reusable local media knowledge base whose first
consumer is a highlight-reel generator. Search and inspectability are therefore core
product capabilities, even though the current UI emphasizes generating reels.

## Evidence and verification

- Baseline: `cd backend; uv run --offline pytest`: **53 passed, 1 slow test deselected**.
- Baseline: `cd frontend; npm run build`: **passed**, including TypeScript checking.
- Three temporary synthetic probes confirmed defects beyond those tests:
  - Replacing a scanned source retained `analyzed_at='old'` and `xxhash='oldhash'`.
  - A fake provider returning 12 seconds for a 45-second request produced a plan
    whose target remained **12 seconds**.
  - An 80x40 JPEG with EXIF orientation 6 decoded through `_load_image_bgr` to
    height/width **80x40**, already rotated, while returning `already_transposed=False`.
- Other findings below are traced code paths or explicit design limitations, with
  suggested regression checks. Their failure frequency has not been measured.
- Passing tests establish the covered behavior; they do not establish real-media
  quality, model compatibility, restart recovery, or browser usability.
- After the cleanup, all **53 fast tests**, **Ruff**, and the **frontend production
  build** passed again. One slow test remained deselected. The checks emitted an
  existing Starlette/httpx deprecation warning and npm `msvs-version` configuration
  warning; neither failed the checks. No real-model or live visual QA was performed.

Priority: **P1** = fix before expanding features; **P2** = next reliability/product
iteration; **P3** = maintenance improvement. All findings remain open except the
specific dead-code removals listed at the end.

## Findings and suggested changes

### F01 · P1 · Source changes do not invalidate expensive cached knowledge

**Evidence:** `backend/app/pipeline/scan.py`, `UPSERT_FILE`, overwrites size/mtime
without clearing the old hash, metadata, or derived scenes. `pipeline/analyze.py`,
`analyze_job_handler`, skips metadata when `analyzed_at` and `rotation` are present;
later stages primarily check whether rows exist. Stage-version columns are written
but do not consistently decide whether cached work is current. Confirmed by probe.

**User effect:** replacing a file under the same name can leave descriptions,
thumbnails, embeddings, and clip bounds describing the previous content indefinitely.

**Suggestion:** compare source fingerprints before overwriting them; invalidate the
affected dependency chain transactionally. Record stage version, model revision,
parameters, and input identity separately. Reuse unchanged artifacts; do not rebuild
the whole collection. Check artifact files exist as well as database rows.

**Acceptance:** unchanged rescan causes no model calls; replacing one source rebuilds
only its descendants; deleting a thumbnail repairs it; changing a model/stage marks
only dependent outputs stale. Existing plans identify stale source revisions.

### F02 · P1 · A partial or failed folder walk can erase indexed knowledge

**Evidence:** `pipeline/scan.py`, `walk_media`, uses `os.walk` without `onerror`, skips
stat errors, then `_remove_missing` treats every unseen path as deleted. Foreign-key
cascades remove scenes, embeddings, and descriptions. Validation at API submission
does not protect against a drive disappearing while a job waits or runs.

**User effect:** an inaccessible subtree or disconnected drive can be interpreted as
intentional source deletion, destroying cached analysis and breaking existing plans.

**Suggestion:** distinguish a complete inventory from a partial one. On traversal
errors, preserve unseen records and report the inaccessible paths. Apply deletion
reconciliation only to a successfully inventoried scope; consider tombstones before
permanent cache removal.

**Acceptance:** simulate a disconnected root, denied subtree, and stat failure;
none removes existing knowledge. Actual deletion on a complete scan is reconciled.

### F03 · P1 · Persisted jobs have no restart recovery

**Evidence:** `jobs/worker.py` persists job rows but stores handlers and pending work
in memory. `main.py` starts a fresh runner without reconciling queued/running rows.
`repos.get_active_job_for` continues to regard those rows as active.

**User effect:** a crash or forced stop can strand jobs and block Analyze, Generate,
or Render with permanent conflicts. A single global worker also lets a long render
delay work in every other project; serialization itself is reasonable for local GPU use.

**Suggestion:** at startup mark orphaned work interrupted/failed and reconcile its
edit state. Offer explicit retry using persisted inputs; do not automatically replay
non-idempotent operations. Keep the single worker until measured demand justifies more.

**Acceptance:** interrupt queued and running work, restart, and verify actionable
terminal states and successful retries, including a reel interrupted while rendering.

### F04 · P1 · Render failure cleanup covers only part of the operation

**Evidence:** `renderer/render.py`, `render_job_handler`, marks an edit `rendering`
before validating its plan, checking sources, probing media, detecting faces, building
the graph, and starting FFmpeg. These steps sit outside the failure handler. The
missing-output check also sits outside it. Only a later process-wait block calls
`_mark_edit_failed`.

**User effect:** missing sources or preparation errors can leave an edit permanently
displayed as Rendering even though the job failed. Re-rendering also writes directly
over the previous output with `-y`, risking the last good export.

**Suggestion:** wrap the complete render lifecycle in one state transition boundary.
Render to a temporary output, validate it, then atomically replace the prior export.
Keep prior successful artifacts available when a new attempt fails.

**Acceptance:** inject failures at validation, probe, graph build, process start,
encode, and output verification. Every attempt reaches a terminal state, removes its
temporary files, and preserves the previous successful export.

### F05 · P1 · The model can override the user's requested duration

**Evidence:** `planner/generate.py`, `generate_plan`, copies the user's prompt and seed
back onto the model response but not `target_duration_sec`. `validate_and_fix_plan`
fits to the model's target. `api/plans.py` separately saves the requested target in
the edits row. Confirmed: a 45-second request became a 12-second plan.

**User effect:** the displayed request and the delivered reel can disagree while the
fitter still appears to satisfy its own duration contract.

**Suggestion:** make validated request values authoritative before all normalization
and fitting. Revalidate the final plan and verify the actual exported duration with
ffprobe; `rendered_duration_sec` currently records the graph's calculated duration.

**Acceptance:** fake model returns a different target; the normalized plan and probed
video still match the request within the documented frame/container tolerance.

### F06 · P1 · Scene identity is lost when a video has multiple candidates

**Evidence:** `planner/generate.py`, `validate_and_fix_plan` and `_clip_bounds`, locate
the first candidate with a matching `rel_path`. Multiple scenes share that path, but
the plan does not identify which scene the model selected.

**User effect:** a valid selection from a later scene may be discarded or fitted to
an unrelated earlier interval. Sorting clips by file capture time also fails to order
different scenes within that same file.

**Suggestion:** plan with stable scene IDs and source revisions. Resolve bounds by
scene ID, or use an unambiguous interval match during backward compatibility. Sort
within a source by scene/clip time.

**Acceptance:** a video with candidates at 0–8s and 40–48s can select and retain the
second interval; a plan containing both plays them in the intended order.

### F07 · P1 · Photo loading can apply EXIF orientation twice

**Evidence:** `pipeline/frames_cv.py`, `_load_image_bgr`, returns `False` for the
already-transposed flag after `cv2.imread(..., IMREAD_COLOR)`. The synthetic orientation-6
JPEG probe showed OpenCV had already rotated it. `process_photo` and the face-crop
consumer then apply stored rotation again. The historical note that OpenCV ignores
EXIF is not true for the exercised decoding path.

**User effect:** thumbnails, metrics, and face focus coordinates can be sideways or
misaligned even when some real-media examples happen to work.

**Suggestion:** define one image decoder contract: raw pixels plus explicit EXIF
transform, or normalized display pixels with no subsequent rotation. Reuse it for
analysis and crop detection; verify FFmpeg's orientation behavior separately.

**Acceptance:** asymmetric JPEG fixtures with real EXIF values 1–8, including mirrored
orientations, and HEIC fixtures produce consistent thumbnails, face coordinates,
and rendered framing. Existing synthetic rectangle rotation tests are insufficient.

### F08 · P2 · Prompt relevance is absent from candidate retrieval

**Evidence:** `planner/candidates.py` ranks blur, brightness, and stability. The top
60 candidates are loaded before the prompt reaches the model. Semantic search and
descriptions exist, but semantic relevance does not select that shortlist.

**User effect:** asking for dogs or waterfalls cannot select relevant material that
the quality-only shortlist excluded. Additional model prompting cannot recover it.

**Suggestion:** combine semantic retrieval with a minimum quality threshold,
per-source diversity, and chronology. Keep some general highlights as context and
record why each candidate was included. Evaluate against representative user prompts.

**Acceptance:** a relevant scene outside the original quality top 60 is retrieved
and eligible for selection; compare relevance and diversity across several collections.

### F09 · P2 · Duration repair silently changes creative intent and ordering

**Evidence:** `planner/generate.py` trims clips, expands bounds, appends filler,
fits one frame at a time, equalizes photos, then fits again. Filler is appended after
the chronological sort. If video cannot cover the target, `freeze_tail_sec` can fill
the deficit with a frozen frame. Duration arithmetic also overlaps renderer logic.

**User effect:** an exact-length reel can still include unrelated filler, jump back
in time, or end on a long freeze. Equal pacing and chronology override model choices
without a clear product policy. Up to 20,000 fitter iterations add complexity and cost.

**Suggestion:** keep exact-duration support, but express its policy explicitly:
selection, ordering, timing, then final validation. Put shared timing rules in a
small domain module independent of FFmpeg; the planner currently imports renderer
FPS. Sort the complete selection before fitting and return visible repair warnings.
Expose a meaningful choice when insufficient suitable footage requires a large freeze.

**Acceptance:** filler cannot violate chronology; freezes are disclosed; final
timing agrees with the renderer across mixed cuts/fades and varied frame lengths.

### F10 · P2 · Successful analysis can hide missing knowledge

**Evidence:** `pipeline/analyze.py` accumulates errors and disables embeddings or
descriptions after certain failures, then returns normally. `pipeline/describe.py`
counts individual failed descriptions, but its caller only uses `described`.
`ProjectView.vue` does not present the job's structured result summary. The Analyze
rescan also discards counts rather than updating registry counts as standalone Scan does.

**User effect:** Done can mean incomplete search or descriptions, and project cards
can show stale photo/video counts. The user cannot see which files need attention.

**Suggestion:** report per-stage completeness, failures, stale artifacts, and retry
actions. Distinguish complete success from completion with warnings. Update collection
counts through the same scan service used by both entry points.

**Acceptance:** missing ML dependencies, unavailable VLM, one invalid response, and
one damaged file all produce an accurate summary with a targeted retry path.

### F11 · P2 · Multiple job-state mechanisms can disagree

**Evidence:** `ProjectView.vue` combines SSE data, local active IDs, passive jobs,
polled jobs, several completion watchers, and `handledJobIds`. `displayedJob` prefers
an SSE row even when polling has a newer terminal row. The store does not load an
initial `/jobs` snapshot; SSE has no replay. Polling only follows locally started IDs.
The first completion watcher can mark a job handled without invalidating plans.
Planner failure text is inside a block that excludes failed planning jobs.

**User effect:** refresh/navigation can lose active work; a stale SSE row can keep a
finished operation looking active; failed planning may disappear without an explanation.

**Suggestion:** use one job collection updated by both snapshot polling and SSE,
with a single terminal-state handler. Hydrate on page load/reconnect, track revision
or update time, and derive activity by operation kind. Display all terminal errors.

**Acceptance:** complete/fail/cancel jobs with SSE disconnected, reload mid-job, switch
projects, and start a second operation. Progress, errors, and query refreshes agree.

### F12 · P2 · The instant preview does not support its own photo workflow

**Evidence:** `components/PlanPreview.vue` assigns every source URL to a `<video>`.
JPEG images never reach the expected video `loadeddata` path; the image extension
list also omits HEIC/TIFF/BMP. Stop only changes a boolean; it does not pause the
element or clear image timers. There is no unmount cleanup or media-error timeout.

**User effect:** photo or mixed plans can stall, unsupported browser codecs can
silently fail, and Stop can leave playback running. Source playback also omits final
crops, transitions, titles, freezes, and color handling.

**Suggestion:** explicitly label a source sequence preview, using normalized image
previews for photos and supported video proxies where needed. Implement stop/error/
unmount handling. Offer playback of a rendered proxy when visual fidelity matters.

**Acceptance:** JPEG/HEIC/mixed sequences advance correctly; Stop silences and halts
playback; changing plans or closing the preview releases timers and media resources.

### F13 · P2 · Final export exists in the backend but is inaccessible in the UI

**Evidence:** `ProjectView.vue`, `renderPlan`, always submits `{ profile: 'proxy' }`.
`renderer/encode.py` implements 720p proxy and 1080p final profiles. The edits table
keeps one render path, without recording multiple profile artifacts.

**User effect:** the normal download flow only delivers a preview-quality render,
with no visible quality choice or explanation.

**Suggestion:** separate Preview and Export actions, show resolution/quality, and
persist render artifacts by edit/profile. Keep proxy previews available after final
export. Use intent-oriented labels rather than exposing codec internals.

**Acceptance:** users can preview at 720p and export at 1080p, see which is being
downloaded, and retain both outputs without overwriting artifact history.

### F14 · P2 · Hardware checks do not prove the selected render path works

**Evidence:** `renderer/encode.py` selects NVENC from the presence of `_nvenc` in
FFmpeg's encoder list; it does not test GPU availability or retry x264 after an NVENC
failure. `renderer/render.py` constructs its HDR probe from arguments before `-y`;
that slice contains device setup but none of the generated source/filter graph.

**User effect:** systems with a compiled encoder but unusable GPU fail despite the
documented fallback. A successful Vulkan-device probe can still lead to a failed
libplacebo filter run without trying zscale.

**Suggestion:** test a tiny representative encode/filter chain or retry on classified
capability errors. Preserve source/data errors rather than masking them with retries.
Use the same capability result in status and rendering.

**Acceptance:** compiled NVENC without a working device uses x264; an unavailable HDR
filter uses the alternate engine; diagnostics record the selected path and reason.

### F15 · P2 · Cancellation and subprocess management are incomplete

**Evidence:** `jobs/worker.py`, `cancel`, sets a cooperative flag. Scan/analyze check
it, but plan/render do not actively interrupt their long work. A render can finish its
file and mark the edit rendered before the runner marks its job cancelled. Render
waits for process exit before draining stderr, allowing a full error pipe to stall it;
the timeout does not cover all preparation/probe steps.

**User effect:** Cancel may consume the full render time or yield contradictory
states; process errors can look like a 30-minute hang.

**Suggestion:** give jobs a cancellation contract that handlers honor during long
operations. Drain stdout/stderr concurrently with bounded diagnostics, scope timeouts
to preparation and encode, and reap subprocesses/tasks on every exit path.

**Acceptance:** cancel during model request, probe, and encode; verify bounded stop
latency, no orphan process/task, and consistent job/edit/output state. Test large stderr.

### F16 · P2 · Deleting a project can orphan data or interfere with active work

**Evidence:** `api/projects.py`, `delete_project`, removes the registry row without
checking active jobs. Default deletion retains the project directory. Slug uniqueness
checks the registry only, so recreating the same name can reopen the retained database.
The UI's generic Delete confirmation does not describe retained knowledge/exports.

**User effect:** a new project can unexpectedly inherit old files/plans, while queued
work may continue writing to a project the user believes was deleted.

**Suggestion:** use immutable storage IDs independent of display names. Define
archive/remove/purge semantics, protect active operations, and explain data retention.
Offer recovery for retained projects rather than accidental reuse.

**Acceptance:** delete during queued/running work and recreate the same display name
with another media folder; no cross-project data reuse or hidden writes occur.

### F17 · P2 · Local-processing promises exceed the enforced behavior

**Evidence:** README says media never leaves the machine. `ai/providers.py` accepts
an arbitrary configured base URL and sends image data to it. Embeddings can download
from Hugging Face; YuNet downloads from GitHub during rendering. Provider/model
capabilities are guessed from IDs, and Settings labels `/v1/models` results as loaded
even though the existing docs record that Unsloth can list merely downloaded models.

**User effect:** configuration can contradict the local-only promise; first use can
depend on network access; a green status can still mean the requested model cannot run.

**Suggestion:** either enforce loopback providers for the local-only product or make
remote processing an explicit opt-in with accurate wording. Add model preparation and
offline readiness checks, capability tests, and visible fallback warnings. Pin model
revisions/checksums. This is a product-contract review, not a full security audit.

**Acceptance:** local-only mode rejects remote inference endpoints; a prepared offline
installation runs without downloads; unavailable vision/encoder/crop capabilities are
explained before expensive work begins.

### F18 · P2 · Search and collection browsing conceal completeness limits

**Evidence:** `pipeline/search.py` retrieves at most 4,000 embedding rows before
semantic ranking and normally returns 40 results. Non-embedded scenes do not appear
even in unfiltered browsing. `ProjectView.vue` searches on each keystroke and treats
an empty filtered result as “No indexed scenes yet.” Collection files/plans have no
UI pagination; collapsed sections still start queries.

**User effect:** relevant older scenes silently vanish in larger collections, while
no matches, incomplete indexing, and no media look alike.

**Suggestion:** separate all-scene browsing from semantic search, expose indexed/total
coverage, debounce search, paginate with explicit totals, and remove the pre-ranking
truncation or disclose it. Measure before introducing a vector database.

**Acceptance:** a match outside the newest 4,000 scenes is findable; no-match and
not-indexed messages differ; collapsed libraries avoid unnecessary expensive work.

### F19 · P2 · The async API still performs blocking model work

**Evidence:** `pipeline/analyze.py` and `pipeline/search.py` call `Embedder.get()`
synchronously; initial model loading can be slow. Analysis calls `score_tags` on the
event loop; its first call builds text embeddings synchronously. Provider instances
created for descriptions are not stored in `provider_holder`, while the cached
planner provider is not closed by app lifespan.

**User effect:** first analysis/search can temporarily block health, progress, or
other requests. Repeated provider construction complicates resource ownership.

**Suggestion:** load models and perform inference off the event loop behind bounded
execution. Let app lifespan own model/provider services and close clients at shutdown.
Inject settings consistently instead of creating fresh `Settings()` in job handlers.

**Acceptance:** health and progress remain responsive during cold model load;
instrument concurrent requests and verify HTTP clients close cleanly.

### F20 · P2 · Operational scripts and test coverage favor the happy path

**Evidence:** `scripts/stop-dev.ps1` kills whichever process owns ports 8000/5173.
Start prints success without checking service readiness. Backend has 53 passing fast
tests but no restart/recovery matrix; frontend only has a build script, with no
behavior tests. API test collection emits a Starlette/httpx deprecation warning.

**User effect:** troubleshooting can stop an unrelated application, and a successful
build can coexist with broken preview and progress behavior.

**Suggestion:** track owned process identities, refuse unrelated port conflicts,
and check health before declaring readiness. Add focused regressions for F01–F07
and a small browser suite covering mixed-media preview and job terminal states.
Resolve the dependency warning through a compatible, tested dependency update.

**Acceptance:** an unrelated port owner survives Stop; failed startup is reported;
tests exercise changed media, incomplete scans, restarts, and mixed-media workflows.

### F21 · P3 · Public schemas and documentation imply unsupported capabilities

**Evidence:** `planner/schemas.py` accepts a `music` slot, but the renderer does not
consume it. Seed is recorded but not sent to the model; `payload.get('seed') or ...`
also replaces an explicit zero. `ProjectView.vue` is over 600 lines of orchestration
and presentation. Several documents mix historical confirmation with current claims.

**User effect:** model-generated music choices have no effect; replay metadata suggests
more reproducibility than exists; maintainers have difficulty identifying the contract.

**Suggestion:** mark unsupported fields explicitly and exclude them from generation
prompts until executable; version any schema removals for persisted plans. Define seed
as provider-dependent best effort, or remove the promise. Extract job synchronization,
reel actions, and scene browsing as responsibilities, without adding generic frameworks.
Use `tech_stack.md` for inventory, `lessons_learned.md` for durable lessons, and status
for dated verification. No public/persisted fields were deleted in this review.

**Acceptance:** generated plans contain only actionable supported features; a seed of
zero is preserved; the documentation distinguishes tested history from open defects.

## Safe cleanup completed in this effort

Repository-wide reference searches supported these narrow removals:

| Location | Removed | Why safe |
|---|---|---|
| `ai/providers.py` | `_JSON_OBJECT_RE` | Never referenced; the parser uses string operations. |
| `renderer/filtergraph.py` | `InputMaps.image_inputs` and its write | Never read; actual image handling uses `image_rels`. |
| `pipeline/describe.py` | `secondary_paths` aggregate | Result unused; the separate secondary-frame query remains. |
| `pipeline/search.py` | Temporary `_rank` field | Added and removed without ever being read. |
| `ProjectView.vue` | Constant `scanVersion` query-key element; unreachable waiting paragraph | Version never changed; the preceding condition already includes waiting. |
| `tests/conftest.py` | Unused `wait_for_job` and time import | No consumers; API tests use their own active helper. |

Retained deliberately: standalone Scan API, final rendering profile, migration columns,
and schema fields used by stored plans. A feature absent from the UI is not necessarily
dead code. No runtime database, source media, export, dependency, or legacy plan was removed.

## Suggested implementation sequence

1. **Restore trust in knowledge:** F01, F02, F07. Define source identity, decoder
   orientation, and safe inventory reconciliation before repairing existing caches.
2. **Restore trust in outputs:** F05, F06, F04. Make request values authoritative,
   preserve scene identity, and make render attempts transactional.
3. **Restore trust in operations:** F03, F10, F11, F15, F16, F19, F20. Establish one
   job lifecycle, explicit partial success, recovery, and safe resource/process ownership.
4. **Improve actual editing effectiveness:** F08, F09, F12, F13, F18. Retrieve what
   the prompt asks for, explain compromises, preview reliably, and expose final export.
5. **Consolidate contracts:** F14, F17, F21. Align capability checks, offline/local
   claims, supported plan fields, and maintenance documentation.

Each step should be a reviewable change with the regression cases listed above.
Retest a representative photo/video/HDR collection visually after contract changes.
Avoid adding facial recognition, audio AI, or a manual timeline until these foundations
are predictable. There is no evidence here that microservices, Docker, a distributed
queue, or replacing SQLite would improve the current user experience.

## Decisions for our review together

- Is strict chronological ordering always intended, or should the prompt control it?
- When suitable material is too short, should exact duration allow extended stills/
  freezes, or should the user choose a shorter reel?
- Should the local-only guarantee be enforced, or may users explicitly opt into a
  remote OpenAI-compatible provider?
- Is the next product milestone a dependable reel workflow or deeper knowledge-base
  inspection/search? The original specification supports both; this affects UI emphasis.

These decisions do not block fixing cache correctness, recovery, scene identity,
orientation, and request authority.
