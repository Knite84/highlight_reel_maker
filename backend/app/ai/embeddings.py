import threading
from functools import lru_cache

import numpy as np

MODEL_ID = "google/siglip2-base-patch16-256"

TAG_PROMPTS = [
    "waterfall",
    "mountain peak",
    "snow covered landscape",
    "beach with sand and waves",
    "ocean or sea horizon",
    "lake shore",
    "river or stream",
    "dense forest",
    "desert dunes",
    "canyon cliffs",
    "open grassy field",
    "cave interior",
    "sunset sky",
    "sunrise sky",
    "night sky with stars",
    "dramatic clouds",
    "rainbow",
    "storm clouds and rain",
    "hiking on a trail",
    "swimming in water",
    "kayaking or canoeing",
    "fishing",
    "campfire at night",
    "tent camping",
    "skiing or snowboarding",
    "bicycle riding",
    "motorcycle riding",
    "car driving on a road",
    "boat sailing",
    "aerial drone view of landscape",
    "family group photo moment",
    "single person portrait",
    "children playing",
    "baby or toddler",
    "crowd of people",
    "dog",
    "cat",
    "birds flying or perched",
    "wildlife animal such as deer or elk",
    "fish or marine life underwater",
    "city skyline",
    "busy street traffic",
    "outdoor market stalls",
    "restaurant food on table",
    "hotel room interior",
    "airport terminal",
    "train station platform",
    "large bridge",
    "castle or ruins",
    "museum exhibits",
    "home kitchen interior",
    "bedroom interior",
    "living room interior",
    "signage with text",
    "fireworks in the sky",
    "concert or stage performance",
    "garden flowers close up",
    "autumn leaves colors",
    "spring blossoms on trees",
]

_MIN_TAG_SCORE = 0.18
_TOP_TAGS = 6
_FALLBACK_TAGS = 3


class Embedder:
    _lock = threading.Lock()
    _instance: "Embedder | None" = None

    def __init__(self) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        self._torch = torch
        self.processor = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModel.from_pretrained(MODEL_ID)
        self.model.eval()

    @classmethod
    def get(cls) -> "Embedder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = Embedder()
            return cls._instance

    def _encode(self, inputs) -> np.ndarray:
        torch = self._torch
        with torch.no_grad():
            output = self.model.get_image_features(**inputs)
        features = getattr(output, "pooler_output", None)
        if features is None:
            features = output[0] if not isinstance(output, torch.Tensor) else output
        normalized = features / torch.norm(features, dim=-1, keepdim=True)
        return normalized.numpy()

    def encode_images(self, paths: list[str], batch_size: int = 8) -> np.ndarray:
        from PIL import Image

        vectors: list[np.ndarray] = []
        for offset in range(0, len(paths), batch_size):
            batch = paths[offset : offset + batch_size]
            images = [Image.open(path).convert("RGB") for path in batch]
            inputs = self.processor(images=images, return_tensors="pt")
            vectors.append(self._encode(inputs))
        return np.concatenate(vectors, axis=0)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        vectors: list[np.ndarray] = []
        for offset in range(0, len(texts), 32):
            batch = texts[offset : offset + 32]
            inputs = self.processor(text=batch, return_tensors="pt", padding="max_length")
            with self._torch.no_grad():
                output = self.model.get_text_features(**inputs)
            features = getattr(output, "pooler_output", None)
            if features is None:
                features = output[0] if not isinstance(output, self._torch.Tensor) else output
            normalized = features / self._torch.norm(features, dim=-1, keepdim=True)
            vectors.append(normalized.numpy())
        return np.concatenate(vectors, axis=0)


@lru_cache(maxsize=1)
def tag_prompt_matrix() -> tuple[list[str], np.ndarray]:
    matrix = Embedder.get().encode_texts(list(TAG_PROMPTS))
    return TAG_PROMPTS, matrix


def score_tags(image_vector: np.ndarray) -> list[tuple[str, float]]:
    prompts, matrix = tag_prompt_matrix()
    similarities = matrix @ image_vector
    order = np.argsort(-similarities)[:_TOP_TAGS]
    scored = [
        (prompts[int(i)], round(float(similarities[int(i)]), 4))
        for i in order
        if similarities[int(i)] >= _MIN_TAG_SCORE
    ]
    if not scored:
        scored = [
            (prompts[int(i)], round(float(similarities[int(i)]), 4))
            for i in order[:_FALLBACK_TAGS]
        ]
    return scored


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
