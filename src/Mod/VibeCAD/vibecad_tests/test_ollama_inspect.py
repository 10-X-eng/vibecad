# SPDX-License-Identifier: LGPL-2.1-or-later

"""Ollama discovery must probe /api/version once before inspecting models."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib import error

import VibeCADOllama as ollama


class _TimeoutOpener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request, timeout=None):
        self.urls.append(getattr(request, "full_url", str(request)))
        raise error.URLError("timed out")


class _OllamaOpener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request, timeout=None):
        url = getattr(request, "full_url", str(request))
        self.urls.append(url)
        path = url.rstrip("/").rsplit("/", 1)[-1]
        payload = {
            "version": {"version": "0.11.0"},
            "show": {
                "digest": "sha256:abc",
                "capabilities": ["completion"],
                "details": {},
            },
            "ps": {"models": []},
        }.get(path, {})
        body = json.dumps(payload).encode("utf-8")
        return SimpleNamespace(read=lambda: body, close=lambda: None)


def test_inspect_models_probes_version_once_for_non_ollama_endpoint() -> None:
    opener = _TimeoutOpener()

    result = ollama.inspect_models(
        "http://127.0.0.1:9/v1",
        ["alpha", "beta", "gamma"],
        timeout_seconds=0.1,
        opener=opener,
    )

    assert result == {"detected": False, "models": {}}
    version_calls = [url for url in opener.urls if url.rstrip("/").endswith("/version")]
    assert len(version_calls) == 1
    assert len(opener.urls) == 1


def test_inspect_models_reuses_cached_version_for_each_model() -> None:
    opener = _OllamaOpener()

    result = ollama.inspect_models(
        "http://127.0.0.1:11434/v1",
        ["alpha", "beta"],
        timeout_seconds=0.1,
        opener=opener,
    )

    assert result["detected"] is True
    assert set(result["models"]) == {"alpha", "beta"}
    version_calls = [url for url in opener.urls if url.rstrip("/").endswith("/version")]
    assert len(version_calls) == 1
