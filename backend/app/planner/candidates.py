from typing import Any

CANDIDATE_SQL = """
SELECT s.id AS scene_id,
       s.file_id,
       s.start_sec,
       s.end_sec,
       f.rel_path,
       f.kind,
       sf.blur,
       sf.brightness,
       sf.stability
FROM scenes s
JOIN scene_frames sf ON sf.scene_id = s.id AND sf.role = 'primary'
JOIN files f ON f.id = s.file_id
WHERE f.error IS NULL
"""


def _brightness_fit(brightness: float | None) -> float:
    if brightness is None:
        return 0.5
    low, high = 80.0, 180.0
    if brightness < low:
        return max(0.0, 1.0 - (low - brightness) / low)
    if brightness > high:
        return max(0.0, 1.0 - (brightness - high) / (255.0 - high))
    return 1.0


def _percentile_rank(values: list[float], target: float | None) -> float:
    if target is None or not values:
        return 0.5
    below = sum(1 for value in values if value <= target)
    return below / len(values)


async def select_candidates(
    conn: Any,
    *,
    limit: int = 48,
    max_per_file: int = 6,
    min_gap_sec: float = 5.0,
) -> list[dict[str, Any]]:
    cur = await conn.execute(CANDIDATE_SQL)
    rows = await cur.fetchall()
    if not rows:
        return []

    blur_values = [row["blur"] for row in rows if row["blur"] is not None]
    scored: list[dict[str, Any]] = []
    for row in rows:
        blur_pct = _percentile_rank(blur_values, row["blur"])
        fit = _brightness_fit(row["brightness"])
        stability = row["stability"] if row["stability"] is not None else 0.5
        score = round(0.55 * blur_pct + 0.25 * fit + 0.20 * stability, 4)
        scored.append(
            {
                "scene_id": row["scene_id"],
                "file_id": row["file_id"],
                "rel_path": row["rel_path"],
                "kind": row["kind"],
                "start_sec": row["start_sec"],
                "end_sec": row["end_sec"],
                "score": score,
            }
        )

    scored.sort(key=lambda item: -item["score"])
    per_file_count: dict[int, int] = {}
    intervals_by_file: dict[int, list[tuple[float, float]]] = {}
    picked: list[dict[str, Any]] = []
    for candidate in scored:
        file_id = candidate["file_id"]
        if per_file_count.get(file_id, 0) >= max_per_file:
            continue
        if candidate["kind"] == "video":
            span = (candidate["start_sec"], candidate["end_sec"])
            existing = intervals_by_file.get(file_id, [])
            if any(
                not (span[1] + min_gap_sec <= other[0] or span[0] >= other[1] + min_gap_sec)
                for other in existing
            ):
                continue
            intervals_by_file.setdefault(file_id, []).append(span)
        picked.append(candidate)
        per_file_count[file_id] = per_file_count.get(file_id, 0) + 1
        if len(picked) >= limit:
            break
    return picked
