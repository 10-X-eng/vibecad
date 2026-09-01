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


def _completed_document_change(name: str, result: Mapping[str, Any]) -> bool:
    job = result.get("job")
    return bool(
        name == "native.job"
        and isinstance(job, Mapping)
        and job.get("terminal") is True
        and job.get("document_changed") is True
    )


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
        debug_events: list[dict[str, Any]] | None = None,
        debug_capture_directory: str = "",
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
        self._debug_events = debug_events
        self._debug_capture_directory = str(debug_capture_directory or "")
        self._progress = progress_callback
        self._cancelled = cancellation_check
        self._steering = steering_check
        self._closed = False
        self._turn_transition_requested = False
        self._pending_context: dict[str, Any] | None = None

    @staticmethod
    def _background_job(snapshot: Any) -> dict[str, Any]:
        result = {
            "job_id": str(getattr(snapshot, "job_id", "") or ""),
            "capability": str(
                getattr(snapshot, "capability_name", "") or ""
            ),
            "phase": str(getattr(snapshot, "phase", "") or ""),
            "terminal": bool(getattr(snapshot, "terminal", False)),
        }
        failure = getattr(snapshot, "error", None)
        if isinstance(failure, Mapping):
            result["failure"] = dict(failure)
        return result

    def _wait_for_active_background_job(
        self,
        tool_name: str,
    ) -> dict[str, Any] | None:
        if tool_name == "native.job":
            return None
        manager = self._execution.background_manager
        document_uid = str(self._execution.document_uid or "")
        if manager is None or not document_uid:
            return None
        try:
            snapshot = manager.latest_document_snapshot(document_uid)
        except Exception:
            return {
                "ok": False,
                "error_code": "NATIVE_BACKGROUND_STATE_INVALID",
                "error": "The active document background state is unavailable.",
            }
        if snapshot is None or bool(getattr(snapshot, "terminal", False)):
            return None
        job_id = str(getattr(snapshot, "job_id", "") or "")
        _emit(
            self._progress,
            {
                "event": "native_background_waiting",
                "job_id": job_id,
                "capability": str(
                    getattr(snapshot, "capability_name", "") or ""
                ),
            },
        )
        while not bool(getattr(snapshot, "terminal", False)):
            if self._cancelled is not None and self._cancelled():
                try:
                    manager.cancel(job_id)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error_code": "NATIVE_RUN_CANCELLED",
                    "error": "VibeCAD stopped before this Native call executed.",
                    "job": self._background_job(snapshot),
                }
            try:
                snapshot = manager.wait(job_id, timeout=0.1)
            except Exception:
                return {
                    "ok": False,
                    "error_code": "NATIVE_BACKGROUND_STATE_INVALID",
                    "error": "The active document background state is unavailable.",
                }
        if str(getattr(snapshot, "phase", "") or "") == "completed":
            return None
        failure = getattr(snapshot, "error", None)
        return {
            "ok": False,
            "error_code": str(
                failure.get("error_code")
                if isinstance(failure, Mapping)
                else ""
            )
            or "NATIVE_BACKGROUND_PREREQUISITE_FAILED",
            "error": str(
                failure.get("message")
                if isinstance(failure, Mapping)
                else ""
            )
            or "The active document background operation did not complete.",
            "job": self._background_job(snapshot),
        }

    def _wait_for_submitted_document_change(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a provider-started mutation only after its exact commit finishes."""

        if tool_name == "native.job" or result.get("ok") is not True:
            return result
        submitted = result.get("job")
        if not isinstance(submitted, Mapping) or submitted.get("terminal") is not False:
            return result
        job_id = str(submitted.get("job_id") or "")
        manager = self._execution.background_manager
        document_uid = str(self._execution.document_uid or "")
        if manager is None or not document_uid or not job_id:
            return result
        try:
            snapshot = manager.snapshot(job_id)
        except Exception:
            return result
        if (
            str(getattr(snapshot, "document_uid", "") or "") != document_uid
            or not bool(getattr(snapshot, "changes_document", False))
        ):
            return result
        _emit(
            self._progress,
            {
                "event": "native_background_waiting",
                "job_id": job_id,
                "capability": str(
                    getattr(snapshot, "capability_name", "") or ""
                ),
            },
        )
        while not bool(getattr(snapshot, "terminal", False)) or bool(
            getattr(snapshot, "worker_active", False)
        ):
            if self._cancelled is not None and self._cancelled():
                try:
                    manager.cancel(job_id)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error_code": "NATIVE_RUN_CANCELLED",
                    "error": "VibeCAD stopped before this Native call completed.",
                    "job": self._background_job(snapshot),
                }
            try:
                snapshot = manager.wait(job_id, timeout=0.1)
            except Exception:
                return {
                    "ok": False,
                    "error_code": "NATIVE_BACKGROUND_STATE_INVALID",
                    "error": "The active document background state is unavailable.",
                }
        phase = str(getattr(snapshot, "phase", "") or "")
        completed = getattr(snapshot, "result", None)
        if phase == "completed" and isinstance(completed, Mapping) and "ok" not in completed:
            return {
                "ok": True,
                **dict(completed),
                "background_job": {
                    "job_id": job_id,
                    "capability": str(
                        getattr(snapshot, "capability_name", "") or ""
                    ),
                    "document_changed": True,
                },
            }
        failure = getattr(snapshot, "error", None)
        return {
            "ok": False,
            "error_code": str(
                failure.get("error_code")
                if isinstance(failure, Mapping)
                else ""
            )
            or "NATIVE_BACKGROUND_PREREQUISITE_FAILED",
            "error": str(
                failure.get("message")
                if isinstance(failure, Mapping)
                else ""
            )
            or "The active document background operation did not complete.",
            "job": self._background_job(snapshot),
        }

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
        result = self._wait_for_active_background_job(name)
        if result is None:
            result = self._document_dispatch(
                lambda: self._execution.dispatcher.call(
                    name,
                    arguments_json,
                    provider_call_id,
                )
            )
        if self._debug_events is not None and self._debug_capture_directory:
            events = [dict(event) for event in self._debug_events]
            self._debug_events.clear()
            if events:
                from VibeCADDebug import capture_native_diagnostic

                for event in events:
                    try:
                        capture_native_diagnostic(
                            directory=self._debug_capture_directory,
                            provider_call_id=provider_call_id,
                            event=event,
                        )
                    except Exception:
                        # Debug capture must never change Native execution.
                        pass
        if not isinstance(result, dict):
            result = {
                "ok": False,
                "error_code": "NATIVE_RESULT_INVALID",
                "error": "Native dispatch returned no result object.",
            }
        result = self._wait_for_submitted_document_change(name, result)
        if result.get("error_code") == "NATIVE_SURFACE_CHANGED":
            result = {
                **result,
                "provider_surface_changed": True,
                "next_turn_required": True,
                "next_surface": str(result.get("current_surface") or ""),
            }
            self._turn_transition_requested = True
        if result.get("ok") is True and result.get("next_turn_required") is not True:
            try:
                refreshed = dict(self._refresh_context())
            except Exception:
                refreshed = {}
            if refreshed:
                self._pending_context = refreshed
                live_surface = refreshed.get("provider_tool_surface")
                live = dict(live_surface) if isinstance(live_surface, Mapping) else {}
                frozen_authority = tuple(
                    self._frozen_surface.get(name)
                    for name in ("engine", "domain", "surface_id")
                )
                live_authority = tuple(
                    live.get(name) for name in ("engine", "domain", "surface_id")
                )
                if (
                    live_authority == frozen_authority
                    and live.get("schema_sha256")
                    and live.get("schema_sha256")
                    != self._frozen_surface.get("schema_sha256")
                ):
                    result = {
                        **result,
                        "provider_surface_changed": True,
                        "next_turn_required": True,
                        "next_surface": str(live.get("domain") or ""),
                    }
                    self._turn_transition_requested = True
        if (
            result.get("ok") is True
            and result.get("next_turn_required") is not True
            and _completed_document_change(name, result)
        ):
            result = {
                **result,
                "provider_surface_changed": True,
                "next_turn_required": True,
                "next_surface": str(self._frozen_surface.get("domain") or ""),
            }
            self._turn_transition_requested = True
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
        if result.get("ok") is True and result.get("next_turn_required") is True:
            self._turn_transition_requested = True
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
        return {**result, "_vibecad_native_result": True}

    def turn_transition_requested(self) -> bool:
        """Return whether an exact CAD transition ended this frozen turn."""

        return self._turn_transition_requested

    def provider_update(self) -> dict[str, Any]:
        if self._pending_context is not None:
            context = self._pending_context
            self._pending_context = None
        else:
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
