# Contributor instructions

Read `docs/lessons_learned.md` and `docs/tech_stack.md` before making project changes.
Use `spec.txt` for original intent, `docs/status.md` for dated handoff context, and
`docs/architecture_review.md` for the 2026-10-20 findings and acceptance suggestions.
Historical verification claims do not supersede current source or test evidence.

Maintain `docs/lessons_learned.md` whenever work reveals a reusable lesson, disproves
an existing lesson, or adds verification for a documented failure. Record evidence,
date, prevention rule, and verification; do not invent history or append empty entries.
Maintain `docs/tech_stack.md` when dependencies, runtime paths, providers, deployment,
or architectural responsibilities change. Keep README setup and status links consistent.

Keep expensive analysis reusable, with explicit invalidation when source inputs or
stage contracts change. Preserve source media and existing runtime knowledge during
cleanup. Check call sites and persisted/API compatibility before deleting code.

Run checks appropriate to the change. Backend baseline: `cd backend` then
`uv run pytest` and `uv run ruff check .`. Frontend baseline: `cd frontend` then
`npm run build`. Slow model tests are separate (`uv run pytest -m slow`). Report what
was actually run, including skipped checks and whether real-media visual QA occurred.
