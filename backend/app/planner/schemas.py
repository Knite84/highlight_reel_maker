from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

KenBurnsDirection = Literal[
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"
]
TransitionType = Literal["cut", "crossfade", "fade_from_black", "fade_to_black"]


def _coerce_finite(value: Any) -> float | None:
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


class KenBurns(BaseModel):
    direction: KenBurnsDirection = "zoom_in"
    intensity: float = Field(default=0.08, ge=0.01, le=0.5)

    @field_validator("direction", mode="before")
    @classmethod
    def _normalize_direction(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
            known = {"zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"}
            if normalized in known:
                return normalized
            return "zoom_in"
        return value

    @field_validator("intensity", mode="before")
    @classmethod
    def _clamp_intensity(cls, value: Any) -> Any:
        number = _coerce_finite(value)
        if number is None:
            return 0.08
        return max(0.01, min(0.5, number))


class PlannedClip(BaseModel):
    rel_path: str
    start_sec: float = Field(ge=0)
    end_sec: float
    transition_in: TransitionType = "cut"
    transition_duration_sec: float = Field(default=0.5, gt=0, le=2.0)
    ken_burns: KenBurns | None = None
    reason: str = ""

    @field_validator("transition_in", mode="before")
    @classmethod
    def _normalize_transition(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            mapping = {
                "cut": "cut",
                "hard": "cut",
                "crossfade": "crossfade",
                "cross_fade": "crossfade",
                "fade": "crossfade",
                "fade_from_black": "fade_from_black",
                "fadein": "fade_from_black",
                "fade_to_black": "fade_to_black",
                "fadeout": "fade_to_black",
            }
            return mapping.get(normalized, "cut")
        return value

    @field_validator("transition_duration_sec", mode="before")
    @classmethod
    def _sanitize_transition_duration(cls, value: Any) -> Any:
        number = _coerce_finite(value)
        if number is None or number <= 0:
            return 0.5
        return min(number, 2.0)

    @model_validator(mode="after")
    def _check_span(self) -> "PlannedClip":
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self


class TitleCard(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=160)
    duration_sec: float = Field(default=2.5, gt=0, le=10)


class MusicSlot(BaseModel):
    title: str = ""
    start_offset_sec: float = Field(default=0.0, ge=0)


class EditPlan(BaseModel):
    schema_version: Literal["v1"] = "v1"
    prompt: str
    target_duration_sec: float = Field(gt=0, le=7200)
    seed: int = 0
    clips: list[PlannedClip] = Field(min_length=1, max_length=200)
    title: TitleCard | None = None
    music: MusicSlot | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_clips(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
            return data
        cleaned: list[dict] = []
        for clip in data["clips"]:
            if not isinstance(clip, dict) or not isinstance(clip.get("rel_path"), str):
                continue
            start = _coerce_finite(clip.get("start_sec"))
            end = _coerce_finite(clip.get("end_sec"))
            if start is None or end is None:
                continue
            if end < start:
                start, end = end, start
            if end - start < 0.3:
                continue
            cleaned.append({**clip, "start_sec": round(start, 3), "end_sec": round(end, 3)})
        return {**data, "clips": cleaned}

    @property
    def total_clip_seconds(self) -> float:
        return sum(clip.end_sec - clip.start_sec for clip in self.clips)


EDIT_PLAN_JSON_SCHEMA: dict[str, Any] = EditPlan.model_json_schema()
