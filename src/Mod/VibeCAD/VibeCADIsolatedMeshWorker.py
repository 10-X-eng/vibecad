# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded process runner shared by isolated Mesh geometry workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADScriptedProcess import terminate_process_tree


MAX_RESULT_BYTES = 1024 * 1024
MAX_LOG_TAIL_BYTES = 4096


def freecadcmd_path() -> Path:
    import FreeCAD as App

    home = Path(str(App.getHomePath())).resolve()
    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    for directory in (home / "bin", home, home.parent / "MacOS"):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    raise NativeMeshError(
        "The isolated Mesh geometry worker is unavailable.",
        error_code="NATIVE_MESH_WORKER_UNAVAILABLE",
    )


def _stop(process: subprocess.Popen[Any]) -> None:
    terminate_process_tree(process)


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - MAX_LOG_TAIL_BYTES))
            return stream.read(MAX_LOG_TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def run_isolated_mesh_worker(
    *,
    freecadcmd: str,
    child_script: str,
    request_path: Path,
    result_path: Path,
    expected_schema: str,
    cancelled: Any,
    timeout_seconds: int,
    failure_code: str,
) -> dict[str, Any]:
    """Run one private FreeCADCmd worker without pipe backpressure."""

    workspace = request_path.parent.resolve()
    if result_path.parent.resolve() != workspace:
        raise NativeMeshError(
            "The isolated Mesh worker result is outside its private workspace.",
            error_code=failure_code,
        )
    console = (
        "import runpy,sys;"
        f"sys.argv=[{json.dumps(child_script)},{json.dumps(str(request_path))}];"
        f"runpy.run_path({json.dumps(child_script)},run_name='__main__')\n"
    )
    log_path = workspace / "worker.log"
    with log_path.open("wb") as log_stream:
        process = subprocess.Popen(
            [str(freecadcmd), "-c"],
            stdin=subprocess.PIPE,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if sys.platform == "win32"
                else 0
            ),
        )
        assert process.stdin is not None
        process.stdin.write(console.encode("utf-8"))
        process.stdin.close()
        started = time.monotonic()
        while process.poll() is None:
            if cancelled():
                _stop(process)
                raise NativeBackgroundCancelled()
            if time.monotonic() - started > timeout_seconds:
                _stop(process)
                raise NativeMeshError(
                    "The isolated Mesh worker exceeded its execution limit.",
                    error_code=failure_code,
                )
            time.sleep(0.05)
        returncode = int(process.returncode or 0)

    if not result_path.is_file():
        raise NativeMeshError(
            "The isolated Mesh worker exited without a result.",
            error_code=failure_code,
            repair={"worker_returncode": returncode, "worker_log": _log_tail(log_path)},
        )
    try:
        size = result_path.stat().st_size
        if not 1 <= size <= MAX_RESULT_BYTES:
            raise ValueError("result size is outside its bound")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativeMeshError(
            "The isolated Mesh worker returned an invalid result.",
            error_code=failure_code,
        ) from exc
    if not isinstance(result, dict) or result.get("schema") != expected_schema:
        raise NativeMeshError(
            "The isolated Mesh worker returned an unsupported result.",
            error_code=failure_code,
        )
    if result.get("ok") is not True:
        raise NativeMeshError(
            str(result.get("error") or "The isolated Mesh worker failed."),
            error_code=str(result.get("failure_code") or failure_code),
        )
    if returncode != 0:
        raise NativeMeshError(
            "The isolated Mesh worker exited unsuccessfully.",
            error_code=failure_code,
            repair={"worker_returncode": returncode, "worker_log": _log_tail(log_path)},
        )
    return result
