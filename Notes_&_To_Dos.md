# Video Highlight Reel Maker
Dump a folder of videos and images and let AI make a video

## TO DOs

<!-- Completed 2026-08-25: face-aware cropping shipped & confirmed on real renders.
     See docs/status.md (shipped) and docs/tech_stack.md. Next: resume Phase 3
     (video-frame faces/attributes, YAMNet audio tagging, manual editor, music library). -->

## LESSONS LEARNED

The canonical living record is now [`docs/lessons_learned.md`](docs/lessons_learned.md).
The historical notes below are retained for context; current review findings and
corrections are in [`docs/architecture_review.md`](docs/architecture_review.md).

- **Restart the backend after Python edits — every time.** A day-old backend process shipped
  stale planner code and sent us debugging the "wrong" layer twice in one day. Check the
  port-8000 listener's process start time before concluding a fix doesn't work.
- **Silent fallbacks need loud breadcrumbs.** The YuNet model download failed once, and the
  graceful fallback quietly degraded every render to a heuristic crop for a day. When a
  "smart" feature silently degrades, surface it in the UI or log it loudly.
- **Make pacing arithmetic explicit, not emergent.** Exact-duration fitting used to dump all
  residual time onto the last clip (a 9–16s finale). Water-filled growth fixed most of it, but
  the durable fix was the user's suggestion: stills get `(target + crossfade overlap − video
  time) ÷ photo count` directly. Simple division beats clever iteration.
- **Detection is not perception.** YuNet misses strongly-posed faces (head thrown back, backlit)
  at any threshold or scale. Crop algorithms can only protect faces that are detected — know
  your detector's recall limits and document them.
- **Verify visual features on real media with annotated overlays.** Drawing the actual crop
  window onto the user's real photos caught what unit tests on synthetic rectangles could not.
- **UI state is cheap to make durable.** Accordion open/closed state persists per project via
  localStorage; read/unread reels persist in SQLite (`edits.downloaded_at`) — pick the store
  that matches the scope of the state.
