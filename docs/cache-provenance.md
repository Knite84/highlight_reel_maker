# Source revisions and reusable analysis

Implemented 2026-10-20 for the source/cache portion of audit package 2 (F01/A05).
HEIC initialization and frame timestamp ordering were subsequently implemented as
the scoped A03/A04 slice below. F07 orientation and F06 scene identity were then
implemented; see [media-contracts.md](media-contracts.md) for the current contracts.

HEIF registration is shared by metadata and decoding. New frame samples retain
timestamps and decoder/estimate provenance; thumbnail selection remains quality-based
while vision inputs sort by time. Legacy samples with unknown timestamps use one
representative frame for new descriptions and disclose unknown order. Existing frame
rows/files are retained. Description contract `describe.v2-chronological` refreshes
previously tracked v1 descriptions on the next Analyze, using the existing archived
invalidation path; it does not force CV/embedding recomputation. Untracked legacy
descriptions remain unverified. Live Unsloth is required for description refresh.
Frame-index/FPS fallback is labeled estimated; frame-accurate VFR seeking is not claimed.

## What Analyze reuses

A complete inventory compares file type, size and modification time. A difference
advances `files.source_revision`; knowledge remains in place but is excluded from
search/planning until refreshed. Analyze hashes suspect sources before deciding
whether their bytes changed. A timestamp-only change with identical bytes retains
the analysis. Unchanged files do not load the embedding model or contact Unsloth
when all their stages are cached.

Select **Verify file contents** beside Analyze to hash every source, including files
whose size and modification time were preserved. This reads the media and may take
time; it does not force inference on unchanged content. The API equivalent is
`POST /api/projects/{id}/analyze?deep_verify=true`. Quick inventory intentionally
cannot detect a same-size edit whose modification time was restored.

Source stat checks surround work, and a final content hash checks files whose
analysis did work. A source changed during analysis fails with retry guidance.
Rendering checks available indexed hashes before encoding and before publication;
legacy files without a hash remain unverified. Referencing plans are marked stale
after detected source changes/removal. Analyze then Generate a new plan to render
the changed footage. Prior exports remain downloadable. Byte-identical timestamp
changes can clear only the corresponding suspicion, not an earlier content change.
New plans use v2 scene/file/revision/hash references with validation at render admission
and publication. V1 remains readable without automatic rewriting; ambiguous references
require a new plan. Broader timing policy and disclosures remain package 5 work.

## Stage contracts and selective repair

`analysis_stages` tracks file input revision, stage contract, requested model ID,
completion time and `success`/`legacy` provenance. Artifact paths remain in the
scene/frame tables. `pipeline/provenance.py` owns contract versions and the dependency
rules. Bump a contract when its algorithm, prompt, sampling, tag vocabulary or
parameters change; changing code alone does not automatically fingerprint it.
The additive orientation stage selectively refreshes affected visual/model descendants
while preserving scene IDs and archiving old SQL knowledge/files. Orientation verification
does not certify legacy model provenance. Changing weights under the same model ID is not detected: pinned model revisions
and checksums remain part of F17.

- Source or metadata/scene contract changes rebuild their dependent work.
- Visual contract changes rebuild frames and downstream model results.
- Embedding or description contract/model changes rebuild that branch only.
- Missing/empty primary, secondary or thumbnail files regenerate only affected
  scenes' visuals. With the same source and visual contract, existing expensive
  embeddings/descriptions remain reusable.

Frame writes must succeed, be nonempty and decode before atomic replacement. New
generations get unique paths, so rebuilding does not overwrite retained artifacts.
No-decodable-frame and failed-write cases fail the file and remain retryable.
Description failures and incomplete embedding batches are not reported as complete.

## Legacy data, backups and history

Before migrating a database lacking source revision columns, the application uses
SQLite's backup API and checks integrity. The snapshot is beside its project database:
`cache/db.before-source-provenance-<unique-id>.sqlite`. Startup performs the additive
migration for active and archived projects. No source media is modified.

Existing rows are retained. Legacy analysis is adopted lazily as `legacy`, not
certified as freshly computed. The media file list displays unverified legacy or
stale-source messages. `tracked` indicates recorded provenance exists; it does not
promise every optional model stage succeeded. Job errors and per-stage records
remain necessary to assess completeness.

Before source replacement, intentional inventory removal or contract invalidation,
`knowledge_history` stores affected SQL rows, including vectors encoded as base64
and artifact references. Historical frame files are retained. These are diagnostic
snapshots, not a user-facing per-file Restore action. They grow until explicit project
purge; automatic history/artifact garbage collection is not implemented.

For full rollback, stop ReelMaker and preserve the current project directory first.
Restore a consistent database snapshot using SQLite's backup API into a disposable
copy, check integrity, then restore it with the matching cache files. Avoid copying
only a live SQLite main file while WAL/SHM files exist. The regression suite proves
backup/restore on disposable databases; this implementation did not migrate or
reanalyze the real collection. Project archive/restore remains the normal UI recovery.

## Evidence

`tests/test_cache_provenance.py` covers unchanged/touched/replaced sources, explicit
deep verification, selective artifact repair, stage/model invalidation, legacy
adoption, failed writes, mutation during decode, retries, stale-plan persistence,
render-time hash checks, additive migration backups and disposable backup restoration.
API regressions cover passing the verification option and retaining downloads while
rejecting rerenders of stale plans. See [status.md](status.md) for final run counts.
