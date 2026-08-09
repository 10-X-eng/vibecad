# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-loop wrapper around one exact Native turn dispatcher."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from VibeCADNativeSessionFactory import NativeSessionExecution


MAX_NATIVE_STEERING_MESSAGES = 8
MAX_NATIVE_STEERING_CHARACTERS = 1000


def _emit(callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(dict(event))


def _frozen_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


class NativeProviderToolRunner:
    """Expose a dispatcher to providers without exposing host bookkeeping."""

    def __init__(
        self,
        *,
        execution: NativeSessionExecution,
        document_dispatch: Callable[[Callable[[], Any]], Any],
        refresh_context: Callable[[], dict[str, Any]],
        frozen_surface: Mapping[str, Any],
        frozen_schemas: list[dict[str, Any]],
        frozen_modeling_surface: Mapping[str, Any],
        tool_trace: list[dict[str, Any]],
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        steering_check: Callable[[], list[str]] | None = None,
    ) -> None:
        if not isinstance(execution, NativeSessionExecution):
            raise TypeError("execution must be a NativeSessionExecution")
        if not callable(document_dispatch) or not callable(refresh_context):
            raise TypeError("Native provider runner callbacks must be callable")
        self._execution = execution
        self._document_dispatch = document_dispatch
        self._refresh_context = refresh_context
        self._frozen_surface = _frozen_copy(dict(frozen_surface))
        self._frozen_schemas = _frozen_copy(frozen_schemas)
        self._frozen_modeling_surface = _frozen_copy(dict(frozen_modeling_surface))
        self._tool_trace = tool_trace
        self._progress = progress_callback
        self._cancelled = cancellation_check
        self._steering = steering_check
        self._closed = False

    def __call__(
        self,
        tool_name: str,
        arguments_json: str = "{}",
        provider_call_id: str = "",
    ) -> dict[str, Any]:
        started = time.monotonic()
        name = str(tool_name or "").strip()
        if self._closed:
            return {
                "ok": False,
                "error_code": "NATIVE_TURN_CLOSED",
                "error": "This Native provider turn is closed.",
            }
        if self._cancelled is not None and self._cancelled():
            return {
                "ok": False,
                "error_code": "NATIVE_RUN_CANCELLED",
                "error": "VibeCAD stopped before this Native call executed.",
            }
        _emit(
            self._progress,
            {"event": "native_tool_started", "tool_name": name},
        )
        result = self._document_dispatch(
            lambda: self._execution.dispatcher.call(
                name,
                arguments_json,
                provider_call_id,
            )
        )
        if not isinstance(result, dict):
            result = {
                "ok": False,
                "error_code": "NATIVE_RESULT_INVALID",
                "error": "Native dispatch returned no result object.",
            }
        steering = []
        if self._steering is not None:
            try:
                steering = [
                    str(value)[:MAX_NATIVE_STEERING_CHARACTERS]
                    for value in list(self._steering() or [])[
                        :MAX_NATIVE_STEERING_MESSAGES
                    ]
                    if str(value).strip()
                ]
            except Exception:
                steering = []
        if steering:
            result = {**result, "human_steering": steering}
        elapsed = round(time.monotonic() - started, 4)
        trace = {
            "tool_name": name,
            "ok": bool(result.get("ok")),
            "elapsed_seconds": elapsed,
            "result": _frozen_copy(result),
        }
        self._tool_trace.append(trace)
        _emit(
            self._progress,
            {
                "event": "native_tool_completed",
                "tool_name": name,
                "ok": bool(result.get("ok")),
                "elapsed_seconds": elapsed,
            },
        )
        return result

    def provider_update(self) -> dict[str, Any]:
        try:
            context = dict(self._refresh_context())
        except Exception:
            context = {}
        live_surface = context.get("provider_tool_surface")
        live = dict(live_surface) if isinstance(live_surface, Mapping) else {}
        frozen_identity = (
            self._frozen_surface.get("engine"),
            self._frozen_surface.get("domain"),
            self._frozen_surface.get("surface_id"),
            self._frozen_surface.get("schema_sha256"),
        )
        live_identity = (
            live.get("engine"),
            live.get("domain"),
            live.get("surface_id"),
            live.get("schema_sha256"),
        )
        context["provider_tool_surface"] = _frozen_copy(self._frozen_surface)
        context["provider_tool_schemas"] = _frozen_copy(self._frozen_schemas)
        context["workbench"] = self._frozen_surface.get("workbench") or None
        if frozen_identity != live_identity:
            context.pop("native_state", None)
            context["modeling_surface"] = {
                **_frozen_copy(self._frozen_modeling_surface),
                "invalidated": True,
                "next_turn_required": True,
            }
        else:
            context["modeling_surface"] = _frozen_copy(
                self._frozen_modeling_surface
            )
        return context

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._execution.close()
