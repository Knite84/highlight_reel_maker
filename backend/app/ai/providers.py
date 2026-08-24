import base64
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from ..core.config import get_settings


class ProviderError(Exception):
    pass


class SchemaUnsupportedError(ProviderError):
    pass


class PlannerOutputError(ProviderError):
    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


_VISION_TOKENS = ("vl", "vision", "llava", "pixtral", "minicpm")


def _looks_vision(model_id: str) -> bool:
    tokens = re.split(r"[^a-z0-9]+", model_id.lower())
    return any(
        token in _VISION_TOKENS or token.startswith("vl") and len(token) <= 4
        for token in tokens
    )


_JSON_OBJECT_RE = re.compile(r"[{]", re.ASCII)


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    if start == -1:
        raise ValueError("no JSON object found in model output")

    working = cleaned[start:].rstrip()
    for _ in range(64):
        depth_objects = max(working.count("{") - working.count("}"), 0)
        depth_arrays = max(working.count("[") - working.count("]"), 0)
        attempt = working.rstrip().rstrip(",") + "]" * depth_arrays + "}" * depth_objects
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
        cut = max(working.rfind(","), working.rfind("{"), working.rfind("["))
        if cut <= 0:
            break
        working = working[:cut]
    raise ValueError("no parsable JSON object found in model output")


class LLMProvider:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        settings = get_settings()
        raw_url = (base_url or settings.unsloth_base_url).strip().rstrip("/")
        raw_url = raw_url.removesuffix("/v1")
        self.base_url = raw_url
        self.api_key = api_key if api_key is not None else settings.unsloth_api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url, headers=headers, timeout=self._timeout
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> list[str]:
        client = await self._get_client()
        try:
            response = await client.get("/v1/models")
            response.raise_for_status()
            data = response.json().get("data", [])
            return [item["id"] for item in data if item.get("id")]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"failed to list models: {exc}") from exc

    async def resolve_model(self, preferred: str | None, *, prefer_vision: bool = False) -> str:
        models = await self.list_models()
        if not models:
            raise ProviderError("no models are loaded in the provider")
        if preferred:
            for model_id in models:
                if model_id == preferred or preferred in model_id:
                    return model_id
        if prefer_vision:
            for model_id in models:
                if _looks_vision(model_id):
                    return model_id
        return models[0]

    @staticmethod
    def text_part(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    @staticmethod
    def image_part(path: Path) -> dict[str, Any]:
        encoded = base64.b64encode(Path(path).read_bytes()).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
        }

    @staticmethod
    def build_vision_prompt(text: str, image_paths: list[Path]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = [LLMProvider.text_part(text)]
        for path in image_paths:
            parts.append(LLMProvider.image_part(path))
        return parts

    async def chat_json(
        self,
        *,
        system: str,
        user_parts: list[dict[str, Any]] | str,
        model: str,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        user_content = (
            [{"type": "text", "text": user_parts}] if isinstance(user_parts, str) else user_parts
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        base_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            base_payload["max_tokens"] = max_tokens

        schema_dropped = False
        correction = ""
        raw_output = ""
        attempt = 0
        while attempt <= retries:
            allow_schema = schema is not None and not schema_dropped and attempt < retries
            body = dict(base_payload)
            if correction:
                body["messages"] = [
                    *base_payload["messages"],
                    {"role": "user", "content": correction},
                ]
            if allow_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": True},
                }
            try:
                raw_output = await self._complete(body)
                return extract_json(raw_output)
            except SchemaUnsupportedError:
                schema_dropped = True
                continue
            except ValueError:
                correction = (
                    "Your previous reply was not parseable as a single JSON object. "
                    "Respond with ONLY valid JSON conforming to the requested structure."
                )
                attempt += 1
        raise PlannerOutputError(
            f"model failed to produce valid JSON after {retries + 1} attempts", raw=raw_output
        )

    async def _complete(self, body: dict[str, Any]) -> str:
        client = await self._get_client()
        try:
            response = await client.post("/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            if "not loaded" in detail.lower() and "downloaded" in detail.lower():
                requested = body.get("model", "requested model")
                raise ProviderError(
                    f"model '{requested}' is downloaded but not loaded in Unsloth. "
                    "Load it in the Unsloth Desktop app (or pick another loaded model), then retry."
                )
            if (
                "response_format" in body
                and response.status_code in (400, 404, 422)
                and any(
                    marker in (detail + str(response.headers.get("content-type", ""))).lower()
                    for marker in ("json_schema", "response_format", "grammar", "unknown field")
                )
            ):
                raise SchemaUnsupportedError(detail)
            raise ProviderError(f"provider HTTP {response.status_code}: {detail}")
        try:
            choices = response.json().get("choices", [])
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"malformed provider response: {exc}") from exc
        return content or ""


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    return LLMProvider()
