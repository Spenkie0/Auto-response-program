from pathlib import Path
from typing import Any

import base64
import json
import time
from urllib import error, request


class OllamaError(RuntimeError):
    code = "MODEL_REQUEST_FAILED"


class OllamaUnavailableError(OllamaError):
    code = "OLLAMA_UNAVAILABLE"


class ModelNotInstalledError(OllamaError):
    code = "MODEL_NOT_INSTALLED"


class InvalidOllamaResponseError(OllamaError):
    code = "MODEL_INVALID_RESPONSE"


class OllamaClient:
    def __init__(self, host: str, timeout: int = 180) -> None:
        """Create an Ollama API client with a base URL and request timeout."""
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.last_metrics = {}

    def list_models(self) -> list[str]:
        """Return model names currently installed on the Ollama server."""
        raw = self._get("/api/tags")
        models = raw.get("models", [])
        if not isinstance(models, list):
            raise InvalidOllamaResponseError("Ollama /api/tags returned an invalid models list.")
        result = []
        for item in models:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                result.append(item["name"])
        return result

    def model_installed(self, model: str) -> bool:
        """Return whether a model name is installed on the Ollama server."""
        names = self.list_models()
        if model in names:
            return True
        if ":" not in model:
            return f"{model}:latest" in names
        return False

    def chat_json(
        self,
        model: str,
        system_prompt: str,
        user_content: str,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON chat request, optionally attaching local image files."""
        user_message: dict[str, Any] = {"role": "user", "content": user_content}
        if images:
            encoded_images: list[str] = []
            for image_path in images:
                data = image_path.read_bytes()
                encoded_images.append(base64.b64encode(data).decode("ascii"))
            user_message["images"] = encoded_images
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "format": "json",
        }
        raw = self._post("/api/chat", payload, model=model)
        response_text = raw.get("message", {}).get("content", "")
        if not response_text:
            raise InvalidOllamaResponseError("Ollama returned an empty message.")
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise InvalidOllamaResponseError(
                f"Ollama returned non-JSON content: {response_text[:500]!r}"
            ) from exc
        if not isinstance(result, dict):
            raise InvalidOllamaResponseError("Ollama JSON response was not an object.")
        return result

    def _get(self, path: str) -> dict[str, Any]:
        """Perform a GET request against the Ollama API and decode its JSON."""
        req = request.Request(self.host + path, method="GET")
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                data = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise OllamaError(f"Ollama GET {path} returned HTTP {exc.code}: {body}") from exc
        except (error.URLError, ConnectionError, TimeoutError) as exc:
            raise OllamaUnavailableError(f"Could not reach Ollama at {self.host}: {exc}") from exc
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise InvalidOllamaResponseError(f"Ollama returned invalid JSON from {path}: {data[:500]!r}") from exc

    def _post(self, path: str, payload: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
        """Perform a POST request and store timing and token metrics from the response."""
        self.last_metrics = {}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.host + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                data = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            lower = body.lower()
            if exc.code == 404 and model and ("model" in lower and ("not found" in lower or "try pulling" in lower)):
                raise ModelNotInstalledError(
                    f"Ollama model '{model}' is not installed or unavailable on {self.host}. "
                    f"Install it with: ollama pull {model}"
                ) from exc
            raise OllamaError(
                f"Ollama POST {path} returned HTTP {exc.code}: {body[:500]}"
            ) from exc
        except (error.URLError, ConnectionError, TimeoutError) as exc:
            raise OllamaUnavailableError(f"Could not reach Ollama at {self.host}: {exc}") from exc

        try:
            payload_data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise InvalidOllamaResponseError(f"Ollama returned invalid JSON: {data[:500]!r}") from exc

        payload_data["_client_wall_seconds"] = round(time.perf_counter() - start, 3)
        self.last_metrics = {
            key: payload_data.get(key)
            for key in (
                "total_duration", "load_duration", "prompt_eval_count",
                "prompt_eval_cached_count", "prompt_eval_duration",
                "eval_count", "eval_duration",
            )
            if key in payload_data
        }
        self.last_metrics["client_wall_seconds"] = payload_data["_client_wall_seconds"]
        return payload_data
