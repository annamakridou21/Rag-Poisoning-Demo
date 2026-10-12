from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # HTTPError is also a URLError. Read the response body first so a
            # WSL user sees Ollama's actionable message (for example, a missing
            # model) rather than an incorrect generic connection diagnosis.
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            raise OllamaError(
                f"Ollama returned HTTP {exc.code} for {path}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. Start it with 'ollama serve'. ({exc})"
            ) from exc
        except (json.JSONDecodeError, TimeoutError) as exc:
            raise OllamaError(f"Invalid or timed-out response from Ollama: {exc}") from exc

    def models(self) -> list[str]:
        result = self._request("/api/tags")
        return [item["name"] for item in result.get("models", [])]

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        result = self._request(
            "/api/chat",
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {
                    "temperature": 0,
                    "seed": 0,
                    "num_ctx": 4096,
                    "num_predict": 256,
                },
            },
        )
        if "message" not in result:
            raise OllamaError(f"Ollama returned no assistant message: {result}")
        return result["message"]
