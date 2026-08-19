# SPDX-License-Identifier: LGPL-2.1-or-later

"""Process-isolated native geometry execution for VibeCAD."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable


DEFAULT_DEADLINE_SECONDS = 30.0


def worker_executable() -> Path:
    import FreeCAD as App

    bin_root = Path(str(App.getHomePath())) / "bin"
    names = (
        ("VibeCADGeometryWorker.exe", "vibecadgeometryworker.exe")
        if sys.platform == "win32"
        else ("VibeCADGeometryWorker", "vibecadgeometryworker")
    )
    for name in names:
        candidate = bin_root / name
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        f"The VibeCAD geometry worker is missing from {bin_root}. Rebuild or reinstall VibeCAD."
    )


def fallback_command() -> tuple[Path, Path]:
    """Return the bundled FreeCADCmd and Python fallback runner."""

    import FreeCAD as App

    home = Path(str(App.getHomePath())).resolve()
    command_names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    directories = (home / "bin", home, home.parent / "MacOS")
    command = next(
        (
            directory / name
            for directory in directories
            for name in command_names
            if (directory / name).is_file()
        ),
        None,
    )
    runner = Path(__file__).resolve().with_name("VibeCADGeometryFallbackRunner.py")
    if command is None or not runner.is_file():
        raise RuntimeError(
            "The isolated VibeCAD geometry fallback is missing. "
            "Rebuild or reinstall VibeCAD."
        )
    return command, runner


def validate_shape(
    shape: Any,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Run crash-prone BREP and BOP validation outside the FreeCAD process."""
    export_brep = getattr(shape, "exportBrep", None)
    if not callable(export_brep):
        return {
            "ok": False,
            "valid": None,
            "failure_code": "BREP_EXPORT_UNAVAILABLE",
            "failure_stage": "brep_export",
            "error": "This shape does not support BREP export for isolated validation.",
        }

    deadline = max(0.1, float(deadline_seconds))
    with tempfile.TemporaryDirectory(prefix="vibecad-validation-") as temporary:
        directory = Path(temporary)
        brep_path = directory / "shape.brep"
        request_path = directory / "request.json"
        result_path = directory / "result.json"
        try:
            export_brep(str(brep_path))
            if not brep_path.is_file() or brep_path.stat().st_size == 0:
                raise OSError("BREP export produced no artifact.")
            request_path.write_text(
                json.dumps(
                    {
                        "schema": "vibecad-geometry-job-v1",
                        "operation": "validate_brep",
                        "shape": {"format": "brep", "path": str(brep_path)},
                        "result_path": str(result_path),
                        "deadline_ms": round(deadline * 1000.0),
                    }
                ),
                encoding="utf-8",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "ok": False,
                "valid": None,
                "failure_code": "BREP_EXPORT_FAILED",
                "failure_stage": "brep_export",
                "error": f"Could not export the shape for isolated validation: {exc}",
            }

        try:
            result = execute_job(
                request_path,
                result_path,
                cancellation_check=cancellation_check,
                deadline_seconds=deadline,
            )
        except (OSError, RuntimeError) as exc:
            return {
                "ok": False,
                "valid": None,
                "failure_code": "GEOMETRY_WORKER_UNAVAILABLE",
                "failure_stage": "external_process",
                "error": f"Could not start the isolated geometry validator: {exc}",
            }

    if result.get("ok") is True and isinstance(result.get("valid"), bool):
        result["validation_status"] = "valid" if result["valid"] else "invalid"
    else:
        result["valid"] = None
        result["validation_status"] = "unknown"
    return result


def runtime_execution_smoke() -> dict[str, Any]:
    """Prove that the installed worker can validate a real BREP shape."""
    import Part

    executable = worker_executable()
    result = validate_shape(
        Part.makeBox(1.0, 2.0, 3.0),
        deadline_seconds=10.0,
    )
    if result.get("ok") is not True or result.get("valid") is not True:
        diagnostic = json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
        raise RuntimeError(
            f"VibeCAD geometry worker smoke validation failed: {diagnostic}"
        )
    return {
        "worker": str(executable),
        "valid": True,
        "elapsed_seconds": result.get("elapsed_seconds"),
    }


def execute_job(
    request_path: str | Path,
    result_path: str | Path,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    try:
        executable = worker_executable()
    except RuntimeError as exc:
        if "geometry worker is missing" not in str(exc):
            raise
        return _execute_job_fallback_process(
            request_path,
            result_path,
            cancellation_check=cancellation_check,
            deadline_seconds=deadline_seconds,
        )
    try:
        return _execute_job_isolated(
            executable,
            request_path,
            result_path,
            cancellation_check=cancellation_check,
            deadline_seconds=deadline_seconds,
        )
    except FileNotFoundError:
        return _execute_job_fallback_process(
            request_path,
            result_path,
            cancellation_check=cancellation_check,
            deadline_seconds=deadline_seconds,
        )


def _execute_job_in_process(
    request_path: str | Path,
    result_path: str | Path,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Compatibility entry point; fallback work is now process-isolated."""

    return _execute_job_fallback_process(
        request_path,
        result_path,
        cancellation_check=cancellation_check,
        deadline_seconds=deadline_seconds,
    )


def _execute_job_fallback_process(
    request_path: str | Path,
    result_path: str | Path,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    executable, runner = fallback_command()
    request = str(Path(request_path))
    console_command = (
        "import runpy,sys;"
        f"sys.argv=[{json.dumps(str(runner))},{json.dumps(request)}];"
        f"runpy.run_path({json.dumps(str(runner))},run_name='__main__')\n"
    )
    return _execute_job_command(
        [str(executable), "-c"],
        result_path,
        cancellation_check=cancellation_check,
        deadline_seconds=deadline_seconds,
        execution_mode="isolated_freecadcmd_fallback",
        stdin_text=console_command,
    )


def _execute_job_isolated(
    executable: Path,
    request_path: str | Path,
    result_path: str | Path,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    return _execute_job_command(
        [str(executable), str(Path(request_path))],
        result_path,
        cancellation_check=cancellation_check,
        deadline_seconds=deadline_seconds,
        execution_mode="isolated_geometry_worker",
    )


def _execute_job_command(
    command: list[str],
    result_path: str | Path,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    execution_mode: str,
    stdin_text: str | None = None,
) -> dict[str, Any]:
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform == "win32"
        else 0
    )
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",
        creationflags=creation_flags,
    )
    if stdin_text is not None:
        stream = getattr(process, "stdin", None)
        if stream is None:
            _terminate_process(process)
            raise RuntimeError("The isolated geometry console has no input stream.")
        try:
            stream.write(stdin_text)
            stream.flush()
        finally:
            stream.close()
            process.stdin = None
    cancelled = False
    timed_out = False
    deadline = started + max(0.1, float(deadline_seconds)) + 2.0
    while process.poll() is None:
        if cancellation_check is not None and cancellation_check():
            cancelled = True
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(0.05)
    if cancelled or timed_out:
        _terminate_process(process)
    stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 6)
    if cancelled:
        return {
            "ok": False,
            "failure_code": "RUN_CANCELLED",
            "failure_stage": "external_process",
            "error": "The isolated geometry operation was stopped by the user.",
            "elapsed_seconds": elapsed,
        }
    if timed_out:
        return {
            "ok": False,
            "failure_code": "GEOMETRY_DEADLINE_EXCEEDED",
            "failure_stage": "external_process",
            "error": f"The isolated geometry operation exceeded {deadline_seconds:.1f} seconds.",
            "elapsed_seconds": elapsed,
        }
    result_file = Path(result_path)
    if not result_file.is_file():
        crashed = (
            sys.platform != "win32"
            and process.returncode is not None
            and process.returncode < 0
        )
        if crashed:
            termination = f"signal {-process.returncode}"
            try:
                signal_name = signal.strsignal(-process.returncode)
            except (ValueError, OverflowError):
                signal_name = None
            if signal_name:
                termination = f"{termination} ({signal_name})"
            failure_code = "GEOMETRY_WORKER_CRASHED"
            error = f"The isolated geometry worker crashed with {termination}."
        else:
            failure_code = "GEOMETRY_WORKER_NO_RESULT"
            error = "The isolated geometry worker exited without a result."
        return {
            "ok": False,
            "failure_code": failure_code,
            "failure_stage": "external_process",
            "error": error,
            "worker": {
                "returncode": process.returncode,
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
            },
            "elapsed_seconds": elapsed,
        }
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "failure_code": "GEOMETRY_WORKER_RESULT_INVALID",
            "failure_stage": "external_process",
            "error": f"The isolated geometry worker returned invalid JSON: {exc}",
            "elapsed_seconds": elapsed,
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "failure_code": "GEOMETRY_WORKER_RESULT_INVALID",
            "failure_stage": "external_process",
            "error": "The isolated geometry worker result is not an object.",
            "elapsed_seconds": elapsed,
        }
    result.setdefault("elapsed_seconds", elapsed)
    result["execution_mode"] = execution_mode
    if process.returncode != 0 and result.get("ok"):
        result.update(
            {
                "ok": False,
                "failure_code": "GEOMETRY_WORKER_EXITED",
                "failure_stage": "external_process",
                "error": f"The isolated geometry worker exited with code {process.returncode}.",
            }
        )
    return result


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1.5)
    except Exception:
        try:
            if sys.platform == "win32":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            pass
