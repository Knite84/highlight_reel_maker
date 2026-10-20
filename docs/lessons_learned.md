# Lessons learned

Last maintained: 2026-10-20.

This is the living record of durable engineering and product lessons for ReelMaker.
Every contributor, human or agent, should read it before changing the project and
update relevant entries when work adds evidence or changes a prior conclusion.
Do not add an entry just to record activity. Keep transient tasks in status/backlog
and current technologies in [tech_stack.md](tech_stack.md).

For each new lesson record the observed failure, the rule that prevents recurrence,
the evidence/date, and the verification that protects it. Correct earlier lessons
when evidence contradicts them; preserve useful context. A documented lesson is not
proof that the implementation follows it.

## LL01 · Check the running build before debugging a fix

**Observed:** historical notes report twice debugging stale backend code on 2026-08-25.
The development launcher does not enable backend auto-reload.

**Rule:** restart after Python changes and verify process ownership/start time before
concluding a change failed. A future launcher should report readiness and build identity.

**Evidence/protection, 2026-10-20:** the interim PID manifest was absent despite
live native servers, so Stop correctly refused to infer ownership from their ports.
Compose now owns the app group; Start builds and waits for health. The native
servers were identified before cutover. `scripts/test-docker-scripts.ps1` checks
wrapper arguments and working-directory preservation. Actual Stop released 5173
(verified by socket bind), then Start restored health. Rebuild after source edits;
Docker has no source auto-reload. Do not kill unrelated listeners by port number.

## LL02 · Graceful degradation must be visible

**Observed:** historical notes describe a failed YuNet download silently replacing
face-aware framing with a heuristic. Current analysis can also finish with skipped
embeddings/descriptions.

**Rule:** preserve useful fallbacks, but report degraded capability, affected scope,
and a recovery action in the operation result and UI.

**Evidence/protection added 2026-10-20 (F10):** injected missing ML dependencies,
offline provider, invalid provider response and damaged photo failures retain structured
coverage/errors and retry guidance. Coverage includes cached rows, separately from work
this run. Analyze publishes committed inventory counts before later model failures.
Vue template tests verify incomplete analysis and failed planning remain visible.
These are automated store/template checks, not browser or live-model acceptance;
capability preparation and broader F14/F17 fallback disclosure remain open.

## LL03 · Make timing policy explicit, and preserve user authority

**Observed:** historical photo reels accumulated 9–16-second finales. Uniform still
pacing improved that case. The 2026-10-20 probe also proved a model's 12-second target
can override a 45-second request.

**Rule:** the user request owns target duration. Normalize selection/order first,
apply a documented pacing policy, then verify final timing. Disclose filler/freezes.
Shared timing mathematics should have one owner independent of rendering syntax.

**Evidence/protection added 2026-10-20 (F05):** request duration, prompt and seed now
override model fields before schema validation and all fitting. Six fake-provider
regressions run the real planning handler and persistence path: shorter, longer,
zero, null, invalid and omitted model duration each retain the 45-second request.
They verify the saved column/JSON agree and the fitted timeline is within one frame
of the target. No real inference is required. Existing saved plans are unchanged.
At that checkpoint, chronology/disclosure/shared timing remained F09 work; the
follow-up below records its implementation. Neither test slice establishes creative
or real-media visual/audio quality.

**Evidence/protection added 2026-10-20 (F09):** explicit affirmative brief directives
now authorize narrative/reverse ordering; unspecified or negated requests stay
chronological. Server-derived selection repairs and current clip timings feed a
pre-render review (long stills/freezes/filler), with plan/profile tokens rechecked at
execution. One timing module supplies planner and renderer. Tests cover filler order,
model/request authority, sparse 1800-second stills, legacy read-only review and token
rejection; measured synthetic exports agree within two frames. Default still threshold
is disclosed as 4s, not a hard cap. Unknown directive phrasing falls back to chronology.

## LL04 · Detection cannot protect subjects it does not detect

**Observed:** earlier real-media testing found YuNet misses strongly posed/backlit
faces; raising resolution or changing thresholds did not recover those examples.

**Rule:** treat detection as fallible. State framing limits, keep a fallback, and
provide a path to inspect or override consequential crops.

**Protection:** synthetic crop geometry tests and representative real-photo overlays.
The historical user confirmation is evidence for those photos, not all future inputs.

## LL05 · Visual correctness needs real metadata and visible evidence

**Observed:** annotated crop overlays exposed issues synthetic rectangles missed.
On 2026-10-20 an actual EXIF-tagged JPEG showed that OpenCV had already applied
orientation while `_load_image_bgr` reported otherwise. The earlier note that
`cv2.imread` ignores EXIF must not be relied upon for the current decoding path.

**Rule:** specify whether each decoder returns raw or display-oriented pixels.
Apply orientation exactly once, including mirrored EXIF cases; use the same coordinate
system for thumbnails, detection, and render framing.

**Evidence/protection added 2026-10-20 (F07):** shared display-oriented RGB decoding
now owns photo transforms, including EXIF mirrors; rendering receives normalized
PNG pixels. OpenCV video auto-rotation is disabled before explicit rotation, and
FFprobe counterclockwise display matrices are converted to clockwise analysis angles.
`test_orientation.py` checks all eight JPEG EXIF values through metadata, thumbnails,
face-detector input and actual renders, HEIF header mirror/rotation and 90/180/270
phone-video matrices. The rendered contact sheet and one real-photo export were viewed.
Selective upgrade tests retain scene IDs, history and old files, including missing
thumbnail-pointer cases. Mirrored video matrices/HDR color/VFR precision are not covered.
HEIF fixtures must encode actual container transforms: supplying a Pillow EXIF object
to this plugin did not establish those transforms; EXIF bytes plus decoded color/dimension
assertions do. Do not treat a fixture's filename or intended flags as evidence.

**Evidence/protection added 2026-10-20 (A03):** metadata previously opened HEIC with
Pillow before the frame decoder registered its codec. Shared initialization now
registers HEIF before either path opens it. `test_heic_frame_order.py` creates a HEIC
fixture in the parent but starts a clean child for scan → metadata → visuals; both
`.heic` and uppercase `.HEIF` pass on Windows and Linux. A synthetic HEIC thumbnail
was viewed. That initial slice fixed process initialization; the F07 entry above
records the later orientation contract and its verification.

## LL06 · Cache reuse depends on identity, provenance, and artifact existence

**Observed:** replacing a source retained its old hash and analysis timestamp in a
2026-10-20 probe. Presence-only checks cannot establish that cached output is current.

**Rule:** record source identity and stage/model/parameter versions. Invalidate only
dependent work, transactionally. Verify referenced files still exist. Failed inventory
is not evidence of source deletion.

**Evidence added 2026-10-20:** a missing-root probe removed indexed files and their
dependent knowledge. Scanning now rejects missing/changed roots, directory traversal
errors, and media stat failures; cancellation is checked before reconciliation.

**Protection:** `test_scan.py` verifies full file/scene/embedding/description preservation
on incomplete inventories, late root loss, and cancellation, plus intentional deletion
on a successful scan. API coverage verifies failed Analyze retains counts and reports
the inventory error. The initially pending changed-source, missing-artifact and
model-version regressions are covered by the follow-up below. A successful inventory
alone is not proof of cache freshness.

**Evidence/protection added 2026-10-20:** source revisions now separate inventory
identity from analyzed identity. Hashing suspects avoids inference for timestamp-only
changes; explicit deep verification finds same-size edits with restored mtime.
Per-stage contracts isolate invalidation. Missing thumbnails repair visuals without
repeating model calls; complete caches avoid even provider model resolution.
Source changes during work fail before success, and render-time hashes reject
changed footage even without a rescan. Stale plans cannot overwrite prior exports.
`test_cache_provenance.py` covers those boundaries, legacy adoption, generation paths,
model/contract changes and retries. Legacy data is explicitly unverified, not erased;
invalidation snapshots SQL knowledge and preserves old artifact files. Pre-migration
SQLite backups are integrity checked; backup/restore is tested on disposable copies.
Model weights changed under an unchanged ID still need pinned revisions (F17).

## LL07 · A job and its domain artifact need one lifecycle

**Observed:** render cleanup does not cover preparation failures; persisted active
jobs have no startup reconciliation; cooperative cancel can disagree with edit status.

**Rule:** every accepted operation needs explicit completion, partial success,
failure, interruption, and cancellation behavior. Reconcile after restart. Preserve
the last good artifact until a replacement is validated.

**Evidence/protection, 2026-10-20:** `test_runtime_safety.py` covers preparation,
encode, validation and publication failures, bounded output, timeout/cancel reaping,
worker survival and persisted recovery. The isolated Docker smoke test verified
graceful stop and forced-crash recovery while preserving a prior MP4. Outputs use
unique filenames and probe validation before publication. Analysis threads are not
hard-cancellable mid-computation; startup terminalizes interrupted work without replay.
The subsequent F11 checkpoint adds frontend reconciliation; job ownership follows below.

**Evidence/protection added 2026-10-20 (A07/F15/F19):** cancelling a threaded await
must not release the job while its worker thread can still write artifacts or use
shared model state. `run_blocking` defers cancellation until return; the regression
blocks a thread, cancels twice and verifies no subsequent job starts or post-cancel
publication occurs. Atomic admission tests admit one of 20 conflicting operations,
including HTTP cross-kind races. Injected running/done/failed/cancelled persistence
failures pause and recover without replaying the handler. Queue overload creates no
extra row; provider-close failure still closes SQLite. Preserve the completed artifact
when only its job status could not be saved. Hard thread termination is not supported;
health reports unavailable workers and uncertain admission requires restart/reconciliation.

## LL08 · Realtime events complement durable state

**Observed:** SSE has no initial snapshot/replay, and the UI combines event rows,
poll results, and local IDs using precedence rules that can prefer stale data.

**Rule:** use a single job collection hydrated from durable state. Merge events and
polling into it; refresh on reconnect. Do not let a stale transport override newer state.

**Evidence/protection added 2026-10-20 (F11):** one revision-checked Pinia collection
now accepts SSE, entry/reconnect snapshots and polling. Registry updates/recovery bump
an additive monotonic revision. Per-project history limits do not exclude active jobs;
known active jobs that finish outside the window get detail refreshes. One terminal
observer handles done/failed/cancelled and batches project/query invalidation.
`frontend/tests/jobs.test.mjs` and `job-store.test.mjs` cover reload hydration,
disconnected polling, reconnect, late responses, second operations, project isolation,
errors and timer/stream cleanup. Template tests verify visible errors/cancellation.
EventSource reconnection alone still cannot recover missed events; snapshots provide
that recovery. Interactive browser acceptance remains unperformed.

## LL09 · Keep identity at the granularity the operation needs

**Observed:** candidates include scene IDs but plans resolve clip bounds by the first
matching file path. Deleted project slugs can also be reused with retained databases.

**Rule:** use immutable project storage IDs, source revisions, and scene IDs. A display
name or file path alone is insufficient when several entities share it over time.

**Evidence added 2026-10-20:** new projects receive UUID-suffixed storage slugs;
legacy paths remain intact. Removal retains a durable archive record and recovery
restores the original ID/path. Purge is separately described and refuses source overlap.

**Protection:** `test_project_lifecycle.py` covers archive/recreate/renamed restore,
restart recovery, legacy orphan-directory preservation, active work, stale submission,
and failed/unsafe purge. Preserve persisted plan compatibility when changing schemas.

**Evidence/protection added 2026-10-20 (F06):** v2 plans bind scene/file IDs, revision
and source hash from backend candidates; source bounds are resolved by scene throughout
fitting/filler. Unique interval matching supports missing model IDs without first-row
guessing. A lower-quality later blue scene now survives selection and renders blue;
tests reject changed/deleted IDs, hashes, paths, bounds and stale restored revisions.
Within-file ordering is checked after reversed selection and filler insertion. Legacy
v1 JSON remains unchanged; ambiguous references fail while previous downloads work.
Byte-identical verified inventory revisions remain compatible. Full pacing/narrative
policy/disclosure were subsequently implemented in the F09 entry above, with explicit
recognition and legacy-compatibility limits documented separately.

## LL10 · Measure usefulness before adding inference or abstractions

**Observed:** semantic embeddings exist but candidate selection is quality-only;
music can be described by a plan but is not rendered; the frontend always exports proxy.

**Rule:** trace each costly artifact/feature to a working user action. Improve prompt
relevance, preview fidelity, error visibility, and final export before adding models.
Keep module boundaries that help; extract concrete responsibilities as they emerge.

**Protection:** prompt-relevance evaluation and supported-feature acceptance checks
(F08/F12/F13/F21). A build passing is not evidence of useful user behavior.

## LL11 · Media tooling details are contracts worth testing

**Observed:** historical fixes addressed Windows drive-colon escaping, FFmpeg timebases,
fragile combined concat pad ordering, and shaking still-image animation.

**Rule:** keep subprocess arguments structured. Use relative title filenames with
the export working directory; verify filter quoting. Normalize timebases before
xfade. Keep explicit video/audio concat chains. Stills use a single source frame plus
`zoompan d=N`; verify full-span static stills as well as animated ones.

**Protection:** `tests/test_render.py` exercises graph generation and FFmpeg smoke
renders. Validate real photos/audio/HDR after changing graph policy.

**Evidence added 2026-10-20:** a string title path broke the graph's Path contract;
title rendering now has a real handler regression, with separate subtitle and progress
draining. NVENC capability must be an actual encode at a supported resolution: a
128x72 probe falsely rejected this GPU, while 320x240 succeeded. Docker smoke and
the real 35-second export used NVENC. Three inspected output frames exposed blocks
in the closing HDR clip absent from a source sample; successful encoding and duration
validation do not establish visual correctness. Comparing the previous export
confirmed a Docker regression. Container rendering now uses CPU zscale tone mapping
while NVENC encodes; rerendered frames at 25/29/33s were clean of blocks. The more
washed-out appearance still needs broader HDR color QA.

Docker builds from Windows also inherited executable flags on Python files; Linux
Ruff caught EXE002 failures missed on Windows. The image now normalizes source/test/
tool file modes and passes Linux Ruff; keep cross-platform checks for deployment changes.

**Evidence/protection added 2026-10-20:** ignoring `cv2.imwrite`'s Boolean result
could cache nonexistent frames as success. Writes now validate nonempty decodable
temporary JPEGs before atomic publication to unique generation paths. A failed-write
regression verifies no frame rows or downstream inference are published, and retry
recovers. Video scenes with no decodable candidates fail rather than cache a null frame.

## LL12 · Put state in the store that matches its scope

**Observed:** accordion preferences persist per project in localStorage; download
initiation persists in SQLite. Those choices made navigation less repetitive.

**Rule:** use local preferences for presentation and durable database records for
project/artifact history. Describe download tracking as initiation, not proof of a
completed save. Keep historical outputs independent of the current render attempt.

**Protection:** download initiation has an API regression test. Artifact versioning
and broader UI persistence checks remain follow-ups.

## LL13 · Quality rank does not establish chronology

**Observed, 2026-10-20 (A04):** samples were sorted by sharpness and persisted as
primary/secondary, then passed primary-first to a prompt claiming chronological order.

**Rule:** preserve sampled source timestamps independently of thumbnail selection.
Order temporal evidence by those timestamps and label estimates. Legacy quality
roles cannot reconstruct time; use a single representative and disclose uncertainty.

**Protection:** `test_heic_frame_order.py` makes the sharpest sample occur last,
persists the frame rows, and captures actual provider inputs: thumbnail stays last,
vision receives first/middle/last. It also verifies timestamp estimates and nullable
legacy migration without deleting artifacts. Description contract v2 invalidates
only tracked descriptions, preserving archived knowledge and reusable CV/embeddings.
