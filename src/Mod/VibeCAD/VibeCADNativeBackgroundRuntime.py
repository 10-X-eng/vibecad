# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-scoped runtime for Native background-job control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError, NativeBackgroundSnapshot
from VibeCADNativeRuntimeContext import NativeRuntimeContext


class NativeBackgroundRuntimeError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_BACKGROUND_JOB_INVALID",
            "message": str(self),
        }


def _summary(snapshot: NativeBackgroundSnapshot) -> dict[str, Any]:
    active = bool(snapshot.worker_active and not snapshot.terminal)
    result: dict[str, Any] = {
        "job_id": snapshot.job_id,
        "capability": snapshot.capability_name,
        "resource_scope": snapshot.resource_scope,
        "phase": snapshot.phase,
        "progress_percent": snapshot.progress_percent,
        "progress_message": snapshot.progress_message,
        "terminal": snapshot.terminal,
        "cancel_requested": snapshot.cancel_requested,
        "worker_state": "active" if active else "terminal",
        "elapsed_seconds": int(snapshot.elapsed_seconds),
        "seconds_since_progress": int(snapshot.seconds_since_progress),
        "recommended_poll_seconds": 30 if active else 0,
        "guidance": (
            "Continue waiting. Do not cancel an active job solely because its percent "
            "is unchanged."
            if active
            else "The job is terminal; read its result or failure."
        ),
    }
    if snapshot.result is not None:
        result["result"] = dict(snapshot.result)
    if snapshot.error is not None:
        result["failure"] = dict(snapshot.error)
    if snapshot.document_changed:
        result["document_changed"] = True
    return result


class NativeBackgroundRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def control(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "status": frozenset({"job_id"}),
                "cancel": frozenset({"job_id"}),
            },
        )
        self._context.guard(allow_owned_cam_simulation=True)
        manager = self._context.background_manager
        if manager is None:
            raise NativeBackgroundRuntimeError(
                "Background Native operations are unavailable in this session."
            )
        job_id = str(values["job_id"])
        try:
            before = manager.snapshot(job_id)
            if before.document_uid != self._context.document_uid:
                raise NativeBackgroundRuntimeError(
                    "The exact background job belongs to another document."
                )
            if operation == "cancel":
                accepted = bool(manager.cancel(job_id))
                after = manager.snapshot(job_id)
                return {"cancel_accepted": accepted, "job": _summary(after)}
            return {"job": _summary(before)}
        except NativeBackgroundRuntimeError:
            raise
        except NativeBackgroundError as exc:
            raise NativeBackgroundRuntimeError(str(exc)) from exc
