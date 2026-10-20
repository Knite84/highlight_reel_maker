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

**Protection:** stop/start and health checks; automation is still a follow-up (F20).

## LL02 · Graceful degradation must be visible

**Observed:** historical notes describe a failed YuNet download silently replacing
face-aware framing with a heuristic. Current analysis can also finish with skipped
embeddings/descriptions.

**Rule:** preserve useful fallbacks, but report degraded capability, affected scope,
and a recovery action in the operation result and UI.

**Protection:** inject missing model/dependency/provider failures. UI coverage is
pending; see review F10, F14, F17.

## LL03 · Make timing policy explicit, and preserve user authority

**Observed:** historical photo reels accumulated 9–16-second finales. Uniform still
pacing improved that case. The 2026-10-20 probe also proved a model's 12-second target
can override a 45-second request.

**Rule:** the user request owns target duration. Normalize selection/order first,
apply a documented pacing policy, then verify final timing. Disclose filler/freezes.
Shared timing mathematics should have one owner independent of rendering syntax.

**Protection:** existing planner timing tests plus pending model-target mismatch,
filler chronology, and actual-output checks (F05/F09). Existing tests do not cover all.

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

**Protection:** pending EXIF 1–8 and HEIC fixtures and visual render comparisons (F07).
The current suite passing does not establish this contract.

## LL06 · Cache reuse depends on identity, provenance, and artifact existence

**Observed:** replacing a source retained its old hash and analysis timestamp in a
2026-10-20 probe. Presence-only checks cannot establish that cached output is current.

**Rule:** record source identity and stage/model/parameter versions. Invalidate only
dependent work, transactionally. Verify referenced files still exist. Failed inventory
is not evidence of source deletion.

**Protection:** pending unchanged/changed-source, partial-walk, missing-artifact, and
model-version regressions (F01/F02). Preserve expensive knowledge during recovery.

## LL07 · A job and its domain artifact need one lifecycle

**Observed:** render cleanup does not cover preparation failures; persisted active
jobs have no startup reconciliation; cooperative cancel can disagree with edit status.

**Rule:** every accepted operation needs explicit completion, partial success,
failure, interruption, and cancellation behavior. Reconcile after restart. Preserve
the last good artifact until a replacement is validated.

**Protection:** pending failure injection and restart/cancellation matrix (F03/F04/F15).
No distributed queue is required to establish these guarantees for a local app.

## LL08 · Realtime events complement durable state

**Observed:** SSE has no initial snapshot/replay, and the UI combines event rows,
poll results, and local IDs using precedence rules that can prefer stale data.

**Rule:** use a single job collection hydrated from durable state. Merge events and
polling into it; refresh on reconnect. Do not let a stale transport override newer state.

**Protection:** pending reload, disconnected-SSE, failed-job, and query-refresh tests
(F11). EventSource reconnection alone cannot recover missed events.

## LL09 · Keep identity at the granularity the operation needs

**Observed:** candidates include scene IDs but plans resolve clip bounds by the first
matching file path. Deleted project slugs can also be reused with retained databases.

**Rule:** use immutable project storage IDs, source revisions, and scene IDs. A display
name or file path alone is insufficient when several entities share it over time.

**Protection:** pending multiple-scenes-per-file and delete/recreate regressions
(F06/F16). Account for persisted plan compatibility when changing schemas.

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

## LL12 · Put state in the store that matches its scope

**Observed:** accordion preferences persist per project in localStorage; download
initiation persists in SQLite. Those choices made navigation less repetitive.

**Rule:** use local preferences for presentation and durable database records for
project/artifact history. Describe download tracking as initiation, not proof of a
completed save. Keep historical outputs independent of the current render attempt.

**Protection:** download initiation has an API regression test. Artifact versioning
and broader UI persistence checks remain follow-ups.
