"""Minimal local Ollama client (no paid APIs). Any failure raises OllamaError so callers fail closed."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2"
DEFAULT_TIMEOUT = 120.0

# transport(url, payload, timeout) -> response JSON. Injected in tests; the default performs a POST.
Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


class OllamaError(RuntimeError):
    pass


def urllib_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-configured local host
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise OllamaError(f"Ollama HTTP {exc.code}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise OllamaError(f"Ollama unreachable or timed out: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama returned a non-JSON HTTP body") from exc


class OllamaClient:
    def __init__(self, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT, transport: Transport | None = None):
        parsed = urlparse(host)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc: raise ValueError(f"invalid OLLAMA_HOST {host!r}")
        if timeout <= 0: raise ValueError("timeout must be positive")
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout
        self.transport = transport or urllib_transport

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OllamaClient":
        return cls(os.environ.get("OLLAMA_HOST", DEFAULT_HOST), os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL),
                   float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)), **kwargs)

    def chat(self, messages: list[dict[str, str]], format_schema: dict[str, Any]) -> str:
        """Return the assistant message content (expected to be JSON text constrained by format_schema)."""
        payload = {"model": self.model, "messages": messages, "stream": False, "format": format_schema,
                   "options": {"temperature": 0, "seed": 7, "num_ctx": 8192}}
        try:
            response = self.transport(f"{self.host}/api/chat", payload, self.timeout)
        except OllamaError:
            raise
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            raise OllamaError(f"Ollama unreachable or timed out: {exc}") from exc
        if not isinstance(response, dict): raise OllamaError("Ollama response is not an object")
        if response.get("error"): raise OllamaError(f"Ollama error: {response['error']}")
        content = (response.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip(): raise OllamaError("Ollama returned empty content")
        return content
