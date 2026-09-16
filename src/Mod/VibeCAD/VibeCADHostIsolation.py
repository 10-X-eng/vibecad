# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bridge scripted CAD jobs to the application-owned persistent worker pool."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import time
from typing import Any

REQUEST_SCHEMA = "vibecad-host-isolation-request-v1"
RESPONSE_SCHEMA = "vibecad-host-isolation-response-v1"


def _encode(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode(value: str) -> dict[str, Any]:
    try:
        payload = base64.b64decode(
            str(value).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "The native isolation worker returned invalid data."
        ) from exc
    if not isinstance(decoded, dict) or decoded.get("schema") != RESPONSE_SCHEMA:
        raise RuntimeError("The native isolation worker response schema is invalid.")
    return decoded


def _freecadcmd(freecad_home: str | Path) -> Path:
    home = Path(freecad_home).resolve()
    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if os.name == "nt"
        else ("FreeCADCmd", "freecadcmd")
    )
    for directory in (home / "bin", home):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
    raise FileNotFoundError(
        f"No windowless FreeCADCmd executable exists under {str(home)!r}."
    )


def ensure_started(
    *,
    app: Any | None = None,
    executable: str | Path | None = None,
    module_root: str | Path | None = None,
    workers: int = 0,
) -> dict[str, Any]:
    """Start (or confirm) the one process-lifetime isolation pool."""

    if str(os.environ.get("VIBECAD_ISOLATION_CHILD") or "").strip() == "1":
        return {"workers": 0, "ready": 0, "isolation_child": True}
    if app is None:
        import FreeCAD as app

    root = Path(module_root or Path(__file__).resolve().parent).resolve()
    worker = root / "VibeCADIsolationWorker.py"
    if not worker.is_file():
        raise FileNotFoundError(f"The native isolation worker is missing: {worker}")
    command = (
        Path(executable).resolve()
        if executable is not None
        else _freecadcmd(app.getHomePath())
    )
    if not command.is_file():
        raise FileNotFoundError(
            f"The native isolation executable is missing: {command}"
        )
    return dict(
        app.configureHostIsolationRuntime(
            str(command),
            str(root),
            max(0, int(workers)),
        )
    )


def execute_staged_script(
    *,
    staging: str | Path,
    script: str,
    environment: Mapping[str, str],
    cancellation_check: Callable[[], bool] | None,
    memory_limit_bytes: int,
    app: Any | None = None,
    executable: str | Path | None = None,
    module_root: str | Path | None = None,
    workers: int = 0,
    cpu_slots: int = 1,
) -> dict[str, Any]:
    """Execute one staged script on the persistent native isolation pool."""

    if app is None:
        import FreeCAD as app

    root = Path(module_root or Path(__file__).resolve().parent).resolve()
    working_directory = Path(staging).resolve()
    if not working_directory.is_dir():
        raise FileNotFoundError(
            f"The staged isolation directory is missing: {working_directory}"
        )
    relative_script = Path(str(script))
    if relative_script.is_absolute() or ".." in relative_script.parts:
        raise ValueError(
            "The isolation script must be relative to its staging directory."
        )
    script_path = (working_directory / relative_script).resolve()
    if script_path.parent != working_directory or not script_path.is_file():
        raise FileNotFoundError(
            f"The staged isolation script is missing: {script_path}"
        )
    clean_environment = {str(key): str(value) for key, value in environment.items()}
    if any("\0" in key or "\0" in value for key, value in clean_environment.items()):
        raise ValueError(
            "Isolation environment names and values cannot contain NUL bytes."
        )

    ensure_started(
        app=app,
        executable=executable,
        module_root=root,
        workers=workers,
    )
    request = _encode(
        {
            "schema": REQUEST_SCHEMA,
            "working_directory": str(working_directory),
            "script": relative_script.as_posix(),
            "environment": clean_environment,
            "memory_limit_bytes": max(0, int(memory_limit_bytes)),
        }
    )
    started_at = time.monotonic()
    native = dict(
        app.executeHostIsolationRequest(
            request,
            cancellation_check,
            max(0, int(memory_limit_bytes)),
            max(1, int(cpu_slots)),
        )
    )
    elapsed = time.monotonic() - started_at
    status = str(native.get("status") or "failed")
    cancelled = status == "cancelled"
    memory_exceeded = bool(native.get("memory_exceeded"))
    output_tail = str(native.get("output_tail") or "")
    diagnostic = str(native.get("diagnostic") or "")

    if status == "completed":
        response = _decode(str(native.get("response") or ""))
        returncode = int(response.get("returncode") or 0)
        stdout = str(response.get("stdout") or "")[-16_000:]
        started = True
        error = str(response.get("error") or "")
    else:
        returncode = -1
        stdout = ""
        started = cancelled or memory_exceeded
        error = diagnostic or "The native isolation worker failed."

    termination_reason = (
        "host_cancellation_request"
        if cancelled
        else (
            "memory_limit"
            if memory_exceeded
            else "process_exit" if status == "completed" else "worker_failure"
        )
    )
    result = {
        "started": started,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": output_tail[-16_000:],
        "cancelled": cancelled,
        "timed_out": False,
        "memory_exceeded": memory_exceeded,
        "cpu_exceeded": False,
        "cancelled_by": "host" if cancelled else None,
        "limit_reached": "memory_bytes" if memory_exceeded else None,
        "termination_reason": termination_reason,
        "timeout_mode": "none",
        "activity_observations": 0,
        "memory_limit_bytes": max(0, int(memory_limit_bytes)),
        "observed_memory_bytes": int(native.get("observed_memory_bytes") or 0),
        "job_id": int(native.get("job_id") or 0),
        "elapsed_seconds": elapsed,
    }
    if error:
        result["error"] = error
    if status == "completed":
        result["worker_pid"] = int(response.get("worker_pid") or 0)
        result["worker_sequence"] = int(response.get("worker_sequence") or 0)
    return result
