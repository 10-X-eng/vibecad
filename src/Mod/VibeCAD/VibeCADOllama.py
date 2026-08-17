# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small native Ollama probes for model metadata and allocated context."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit


DEFAULT_OLLAMA_PROBE_TIMEOUT_SECONDS = 5.0
DEFAULT_OLLAMA_LOAD_TIMEOUT_SECONDS = 120.0
MIN_CODEX_OUTPUT_RESERVE_TOKENS = 2048


def native_api_base(openai_base_url: str) -> str:
    """Translate an Ollama OpenAI-compatible base URL to its native API root."""

    parsed = urlsplit(str(openai_base_url or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Ollama discovery requires an HTTP base URL.")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/api", "", ""))


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float,
    opener: Any | None = None,
) -> dict[str, Any]:
    body = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if payload is not None
        else None
    )
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    response = (opener or request.urlopen)(http_request, timeout=timeout_seconds)
    try:
        decoded = json.loads(response.read().decode("utf-8"))
    finally:
        if hasattr(response, "close"):
            response.close()
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Ollama returned a non-object response from {url}.")
    return decoded


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def codex_context_limits(runtime_context_length: int) -> tuple[int, int]:
    """Return the exact Codex window and a 25%-reserve compaction boundary."""

    context_window = int(runtime_context_length)
    if context_window <= MIN_CODEX_OUTPUT_RESERVE_TOKENS:
        raise ValueError(
            "Ollama's allocated context is too small for a VibeCAD tool turn."
        )
    reserve = max(MIN_CODEX_OUTPUT_RESERVE_TOKENS, context_window // 4)
    return context_window, context_window - reserve


def _supported_context_length(model_info: dict[str, Any]) -> int | None:
    architecture = str(model_info.get("general.architecture") or "").strip()
    if architecture:
        exact = _positive_int(model_info.get(f"{architecture}.context_length"))
        if exact is not None:
            return exact
    candidates = {
        parsed
        for key, value in model_info.items()
        if str(key).endswith(".context_length")
        for parsed in (_positive_int(value),)
        if parsed is not None
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _running_model(
    payload: dict[str, Any],
    model: str,
    digest: str,
) -> dict[str, Any] | None:
    for raw in list(payload.get("models") or []):
        if not isinstance(raw, dict):
            continue
        if digest and str(raw.get("digest") or "") == digest:
            return raw
        if model in {str(raw.get("model") or ""), str(raw.get("name") or "")}:
            return raw
    return None


def inspect_model(
    openai_base_url: str,
    model: str,
    *,
    preload: bool = False,
    timeout_seconds: float = DEFAULT_OLLAMA_PROBE_TIMEOUT_SECONDS,
    load_timeout_seconds: float = DEFAULT_OLLAMA_LOAD_TIMEOUT_SECONDS,
    opener: Any | None = None,
) -> dict[str, Any]:
    """Return Ollama metadata, or ``detected=False`` for another provider."""

    clean_model = str(model or "").strip()
    if not clean_model:
        return {
            "ok": False,
            "detected": False,
            "error": "Ollama inspection requires a model name.",
        }
    try:
        api_base = native_api_base(openai_base_url)
        version_payload = _json_request(
            f"{api_base}/version",
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except (ValueError, OSError, error.URLError, json.JSONDecodeError, RuntimeError):
        return {"ok": False, "detected": False, "error": None}

    version = str(version_payload.get("version") or "").strip()
    if not version:
        return {"ok": False, "detected": False, "error": None}
    try:
        shown = _json_request(
            f"{api_base}/show",
            payload={"model": clean_model, "verbose": False},
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        digest = str(shown.get("digest") or "")
        running_payload = _json_request(
            f"{api_base}/ps",
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        running = _running_model(running_payload, clean_model, digest)
        if running is None and preload:
            _json_request(
                f"{api_base}/generate",
                payload={
                    "model": clean_model,
                    "stream": False,
                    "keep_alive": "5m",
                },
                timeout_seconds=load_timeout_seconds,
                opener=opener,
            )
            running_payload = _json_request(
                f"{api_base}/ps",
                timeout_seconds=timeout_seconds,
                opener=opener,
            )
            running = _running_model(running_payload, clean_model, digest)
        model_info = shown.get("model_info")
        if not isinstance(model_info, dict):
            model_info = {}
        runtime_context = (
            _positive_int(running.get("context_length"))
            if isinstance(running, dict)
            else None
        )
        supported_context = _supported_context_length(model_info)
        return {
            "ok": True,
            "detected": True,
            "server": "ollama",
            "server_version": version,
            "model": clean_model,
            "digest": digest,
            "capabilities": [
                str(item) for item in list(shown.get("capabilities") or [])
            ],
            "parameter_size": str(
                dict(shown.get("details") or {}).get("parameter_size") or ""
            ),
            "quantization_level": str(
                dict(shown.get("details") or {}).get("quantization_level") or ""
            ),
            "supported_context_length": supported_context,
            "runtime_context_length": runtime_context,
            "fully_loaded_on_gpu": (
                int(running.get("size") or 0) > 0
                and int(running.get("size") or 0)
                == int(running.get("size_vram") or -1)
                if isinstance(running, dict)
                else None
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "detected": True,
            "server": "ollama",
            "server_version": version,
            "model": clean_model,
            "error": str(exc),
        }


def inspect_models(
    openai_base_url: str,
    models: list[str],
    *,
    timeout_seconds: float = DEFAULT_OLLAMA_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Inspect fetched Ollama models without loading them."""

    details: dict[str, dict[str, Any]] = {}
    detected = False
    for model in models:
        inspected = inspect_model(
            openai_base_url,
            model,
            preload=False,
            timeout_seconds=timeout_seconds,
        )
        if inspected.get("detected"):
            detected = True
        if inspected.get("ok"):
            details[model] = inspected
    return {"detected": detected, "models": details}
