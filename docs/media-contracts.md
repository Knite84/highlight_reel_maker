# Orientation and scene identity

Implemented 2026-10-20: F07 orientation and F06 scene references. This documents the
current boundary contracts. [job-and-planning-policy.md](job-and-planning-policy.md)
describes the subsequent F09 timing/ordering contract; preview work remains separate.

## Display orientation (F07)

Photos decode through shared Pillow/HEIF support into detached display-oriented RGB
pixels. JPEG EXIF 1–8, including mirrored cases, is applied once. HEIF container
transforms are handled by its decoder; removed EXIF must not be reapplied from the
plugin's `original_orientation` information. See the
[HEIF decoder's orientation policy](https://pillow-heif.readthedocs.io/en/latest/workaround-orientation.html)
and [Pillow's EXIF transform contract](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose).

Analysis and face detection use the same display pixels. Photo metadata dimensions
are display dimensions and `files.rotation` becomes zero. Legacy degree arguments
remain accepted by Python callers but no longer rotate decoded photos a second time.
Rendering prepares a unique temporary PNG with those same pixels, bypassing FFmpeg's
HEIC decoding and EXIF interpretation. The graph does not add another photo rotation.
Relative export paths resolve before invoking FFmpeg from its export directory.
Owned PNG inputs are cleaned on completion/failure and reconciled after interruption.

Video metadata stores clockwise rotation, converting FFprobe display-matrix angles
from counterclockwise. Coded video dimensions remain separate from that rotation.
OpenCV automatic orientation is disabled, then analysis applies one explicit transform.
Rendering lets FFmpeg honor the original display matrix. Quarter turns are covered;
unsupported arbitrary analysis angles fail explicitly rather than producing silently
misoriented frames. Mirrored video matrices and HDR color calibration are not newly
validated by these tests. Frame-accurate VFR seeking remains outside this slice.

## Cache upgrade

The additive `orientation=display.v1` stage is established on Analyze, not by
automatically reanalyzing the collection at startup. The first pass checks source
orientation. Legacy rotated/mirrored photos, HEIC/HEIF (conservatively), and rotated
videos refresh their visual/model descendants. Existing scene IDs/boundaries remain.
Untransformed JPEG/video caches are retained. Old SQL knowledge is archived before
invalidation, even if a thumbnail pointer was already missing; old files/exports stay.
Legacy analysis is still labeled unverified even when orientation itself is verified.

Load Unsloth before Analyze when affected descriptions need regeneration. This work
does not discard source media or rebuild unrelated scenes. A future orientation
contract change must invalidate the visual branch explicitly. See
[cache-provenance.md](cache-provenance.md) for snapshots and recovery.

## New edit plans (F06)

New plans use `schema_version: v2`. Every clip stores:

| Field | Meaning |
|---|---|
| `scene_id` | Immutable scene row ID in this project |
| `source_file_id` | Immutable indexed file ID |
| `source_revision` | Inventory revision observed during planning |
| `source_hash` | Indexed source content hash |
| `rel_path`, `start_sec`, `end_sec` | Source path and explicit clip interval |

The prompt lists actual scene IDs. The model selects a scene; the backend binds its
file/revision/hash from the candidate record, ignoring model guesses at those values.
An omitted scene ID can be inferred only from a unique path/interval match. Unknown,
mismatched or ambiguous choices fail with regeneration guidance. Analyze must establish
source hashes before generating v2 plans.

All fitting helpers and duration fillers use scene references. Growth stays within
the chosen scene; filler cannot silently reuse that same scene under another starting
offset. Ordering includes the clip's source time within a file, and runs after filler
selection before final fitting. The subsequent F09 implementation supports explicit
brief-directed narrative/reverse order and pre-render timing disclosures; see the policy
document for its conservative directive recognition and legacy-plan compatibility.

Rendering checks references at API admission, inside the job and before publication.
It verifies scene/file/path, analyzed source state, hash and source bounds. A later
verified inventory revision with identical bytes remains compatible; a reference to
a newer revision than the current restored database does not. Referenced source
bytes are checked separately before encoding/publication.

## Existing v1 plans

V1 JSON remains readable and is not rewritten automatically. Its explicit timestamps
remain authoritative; matching against indexed scenes must be unique and retains
the historical small boundary tolerance. Ambiguous/stale references require Analyze
and a new plan; the previous MP4 remains downloadable. Legacy unindexed plans can
retain explicit ranges, but do not gain v2 identity guarantees. Existing exports are
not deleted or migrated by this change.

## Verification

`test_orientation.py` covers all eight JPEG EXIF values through metadata, thumbnails,
face-detector input and actual render-handler output, real HEIF header mirror/rotation
fixtures, and phone display matrices at 90/180/270 degrees. The rendered EXIF contact
sheet was viewed. `test_cache_provenance.py` checks selective orientation invalidation
without changing scene IDs or deleting historical files.

`test_scene_identity.py` covers a lower-ranked later scene from the same file, interval
fallback, source/model field ownership, stale IDs/revisions/hash/bounds/path, v1
ambiguity, byte-identical revisions, missing identity, within-file ordering including
fillers, and an actual render of the later blue segment rather than the earlier red.
API tests verify rejection still leaves previous downloads available.

One existing EXIF-1 collection photo was rendered via NVENC in an isolated QA store,
using the cached face detector. Display source and rendered crop were viewed; output
measured 4.0 seconds and the source hash stayed unchanged. Rotated/mirrored real-world
coverage here comes from asymmetric encoded fixtures, not a new annotated phone
collection. No audio listening, browser QA or live Unsloth test occurred. Current
Windows/Linux check counts and activation instructions are in [status.md](status.md).
