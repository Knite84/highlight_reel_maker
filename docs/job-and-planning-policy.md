# Job admission, cancellation and planning policy

Implemented 2026-10-20: the A07/F15/F19 job-safety slice and F09 ordering/timing
disclosure. See [status.md](status.md) for actual checks and remaining limitations.

## Job ownership

ReelMaker runs one backend process and one worker. A single admission lock covers
the project/active-work check, durable job insertion and publication to the queue,
as well as project archive/purge and initial project registration/scan submission.
Scan, Analyze, Generate and Render conflict with one another within the same project.
Another request receives HTTP 409 instead of queueing a competing operation.
Different projects may queue work; this is not a multi-process/distributed scheduler.

`REELMAKER_JOB_QUEUE_LIMIT` defaults to 32 waiting jobs (allowed configuration 1–256),
plus one running job. A full queue returns HTTP 503 with retry guidance and no new
job row. Project creation checks capacity before creating storage. No source media
or existing knowledge is discarded when admission is rejected.

The running transition, handler, callback and terminal persistence have explicit
failure handling. Failed terminal writes retain their desired fields in memory and
retry every 0.5 seconds. Admission and execution pause until those writes succeed;
the handler is never replayed because it may already have published a plan/export.
If persisting or verifying a new admission fails, the outcome can be uncertain:
admission stays closed until restart and startup reconciliation. Unexpected worker
exit is logged and also closes admission. `/api/health` returns HTTP 503 with worker
details when unavailable, so Docker health reflects this condition.

The retry buffer is not a durable outbox. A forced crash still uses the existing
interrupted-job reconciliation on startup; an already published export remains
preserved. Shutdown closes registry storage even if provider cleanup fails.

## Cancellation

HTTP inference and encoder tasks receive cancellation; owned subprocesses retain
their kill/reap behavior. Blocking thread work cannot safely be force-killed. Analysis
decoding, scene detection, hashing, embedding/tag work and render photo preparation
therefore wait for their active thread to return before reporting cancellation and
releasing the worker slot. Repeated Cancel requests cannot bypass that barrier.
Cancellation may take time during a model computation or timed metadata subprocess.
Unsloth is external: cancelling a client request does not promise to stop computation
inside that independently managed service. Process isolation/hard model termination
has not been added.

## Sequencing and duration (F09)

New plans default to chronological capture order, with path and clip source time
as tie-breakers. The complete selection, including added duration filler, is sorted
before final fitting. Explicit reverse chronological order uses the reverse keys.
Explicit narrative order preserves the model's selected sequence; additional filler
is appended and disclosed. Narrative quality/relevance still depends on the model
and shortlist; this does not implement F08 semantic retrieval.

Brief directives are interpreted conservatively in English. Supported examples:

- `Use narrative order` or `Use non-chronological sequencing`.
- `Use flashbacks` or `Open with the finale then return to arrival`.
- `Use reverse chronological order` or `newest first`.
- `Use chronological order` explicitly restores the default.

Affirmative explicit directives are required; a general request to tell a story
does not change order. Negated directives are ignored; the last recognized affirmative
directive wins if the brief conflicts. This is a deterministic directive recognizer,
not unrestricted natural-language understanding. The UI shows example wording.
The backend derives `sequencing` and its quoted brief sentence; model guesses cannot
override them. Unsupported phrasing defaults to chronology.

The requested duration stays authoritative. Fitting can extend a selected video
within its scene, add unused candidates, lengthen still holds or freeze a video end
frame. A shared `planner/timing.py` owns FPS, cut runs, transition overlaps and final
duration for both planning and FFmpeg construction. New plans must fit within one
30-fps frame or fail explicitly instead of silently accepting another duration.
The existing fitter remains frame-based and bounded; it has not been replaced with
a new optimizer. Its long-still fallback covers sparse collections at the API maximum.

## Review before rendering

Clicking Render loads a review, showing the requested/estimated duration, sequencing
and directive, plus:

- Holds above the disclosed 4-second still threshold (a warning threshold, not a cap).
- Frozen-frame seconds, added filler and recorded selection/expansion repairs.
- A duration mismatch in an older saved plan, without rewriting its timing.

The user then selects **Render this plan**, or goes back to adjust the brief and
generate another plan. Exact duration is retained even when long holds/freezes are
needed; the review does not silently shorten the requested reel.

API clients read `GET /api/projects/{project_id}/plans/{edit_id}/render-review?profile=proxy`
and submit its `token` as `review_token` to Render. Plans with disclosed tradeoffs
reject missing/stale tokens with HTTP 409. Tokens bind the saved plan, profile and
disclosure policy; the handler checks again before reading/encoding media. Plans
without warnings retain compatibility with older direct API render requests.

Sequencing, timing-adjustment and clip filler fields are additive to v2 plans.
Existing v1/v2 JSON and exports are not migrated, sorted or rewritten. Old plans
without sequencing metadata display “Saved order (older plan)”; legacy filler reasons
are recognized in the review. Scene/source identity and hashing checks still apply.

Seed zero now survives the API planning handler. The provider does not yet receive
a sampling seed, so this is not a claim of deterministic model output (remaining F21).

## Verification limits

Regression tests exercise concurrent HTTP operations, queue limits, registry failure
injection/recovery without handler replay, thread cancellation ownership and shutdown
cleanup. Planning tests cover affirmative/negated directives, same-source filler order,
model/request ownership, sparse photos/video, read-only legacy review, token checks,
and real synthetic FFmpeg exports with mixed fractional spans/fades/freezes.
Frontend tests render the disclosure component and verify warnings precede its action.
These checks do not establish live-model creative quality, interactive browser behavior,
real-media visual quality or audio quality. Existing HDR limitations remain.
