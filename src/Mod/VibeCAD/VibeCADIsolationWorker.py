# SPDX-License-Identifier: LGPL-2.1-or-later

"""Long-lived FreeCADCmd worker for application-owned isolated CAD jobs."""

from __future__ import annotations

import base64
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import gc
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import sys
import traceback
from typing import Any, Mapping

import FreeCAD as App

PROTOCOL = "VIBECAD-ISOLATION/1"
REQUEST_SCHEMA = "vibecad-host-isolation-request-v1"
RESPONSE_SCHEMA = "vibecad-host-isolation-response-v1"
_OUTPUT_LIMIT = 16_000


def _encode(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode(value: str) -> dict[str, Any]:
    payload = base64.b64decode(
        value.encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict) or decoded.get("schema") != REQUEST_SCHEMA:
        raise ValueError("Unsupported native isolation request schema.")
    return decoded


def _write(line: str) -> None:
    stream = getattr(sys, "__stdout__", None) or sys.stdout
    # Native progress writes carriage-return updates without a final newline.
    # Terminate that diagnostic fragment before emitting a complete protocol
    # record; otherwise the host treats RESULT as diagnostic text and waits
    # indefinitely even though the job has finished.
    stream.write(f"\n{line}\n")
    stream.flush()


def _module_is_below(module: Any, root: Path) -> bool:
    filename = getattr(module, "__file__", None)
    if not filename:
        return False
    try:
        return Path(filename).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _staged_module_names(root: Path) -> set[str]:
    return {
        path.stem
        for path in root.glob("*.py")
        if path.name != "worker.py" and path.stem.isidentifier()
    }


def _close_job_documents(existing: set[str]) -> None:
    for name in tuple(App.listDocuments()):
        if name not in existing:
            try:
                App.closeDocument(name)
            except Exception:
                pass


def _run(payload: Mapping[str, Any], cpu_slots: int = 1) -> dict[str, Any]:
    working_directory = Path(str(payload.get("working_directory") or "")).resolve()
    if not working_directory.is_dir():
        raise FileNotFoundError(
            f"The native isolation working directory is missing: {working_directory}"
        )
    relative_script = Path(str(payload.get("script") or ""))
    if relative_script.is_absolute() or ".." in relative_script.parts:
        raise ValueError("The native isolation script path is invalid.")
    script = (working_directory / relative_script).resolve()
    if script.parent != working_directory or not script.is_file():
        raise FileNotFoundError(f"The native isolation script is missing: {script}")
    raw_environment = payload.get("environment")
    if not isinstance(raw_environment, dict):
        raise TypeError("The native isolation environment must be an object.")
    environment = {str(key): str(value) for key, value in raw_environment.items()}
    environment["VIBECAD_HOST_CPU_SLOTS"] = str(max(1, int(cpu_slots)))
    if sys.platform == "win32":
        environment["OPENBLAS_NUM_THREADS"] = "1"
    if any("\0" in key or "\0" in value for key, value in environment.items()):
        raise ValueError("The native isolation environment contains a NUL byte.")

    original_directory = Path.cwd()
    original_environment = dict(os.environ)
    original_path = list(sys.path)
    existing_documents = set(App.listDocuments())
    staged_names = _staged_module_names(working_directory)
    for name in staged_names:
        sys.modules.pop(name, None)

    stdout = StringIO()
    returncode = 0
    try:
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(working_directory)
        job_pythonpath = [
            item
            for item in str(environment.get("PYTHONPATH") or "").split(os.pathsep)
            if item
        ]
        sys.path[:] = [str(working_directory), *job_pythonpath, *original_path]
        try:
            with redirect_stdout(stdout), redirect_stderr(stdout):
                numerical_scope = nullcontext()
                if sys.platform == "win32":
                    from VibeCADNumericalRuntime import numerical_thread_limits

                    numerical_scope = numerical_thread_limits(
                        cpu_slots, int(payload.get("memory_limit_bytes") or 0)
                    )
                with numerical_scope:
                    runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code is None:
                returncode = 0
            elif isinstance(exc.code, int):
                returncode = int(exc.code)
            else:
                returncode = 1
                print(str(exc.code), file=stdout)
    finally:
        _close_job_documents(existing_documents)
        for name, module in tuple(sys.modules.items()):
            if name in staged_names or _module_is_below(module, working_directory):
                sys.modules.pop(name, None)
        sys.path[:] = original_path
        os.chdir(original_directory)
        os.environ.clear()
        os.environ.update(original_environment)
        gc.collect()

    return {
        "schema": RESPONSE_SCHEMA,
        "returncode": returncode,
        "stdout": stdout.getvalue()[-_OUTPUT_LIMIT:],
    }


def main() -> int:
    _write(f"{PROTOCOL} READY")
    jobs_completed = 0
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\r\n")
        if line == f"{PROTOCOL} SHUTDOWN":
            return 0
        prefix = f"{PROTOCOL} JOB "
        if not line.startswith(prefix):
            continue
        fields = line[len(prefix) :].split(" ", 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            job_id, raw_cpu_slots, encoded_request = fields
            cpu_slots = max(1, int(raw_cpu_slots))
        elif len(fields) == 2 and fields[0].isdigit():
            job_id, encoded_request = fields
            cpu_slots = 1
        else:
            continue
        try:
            response = _run(_decode(encoded_request), cpu_slots)
        except BaseException as exc:
            response = {
                "schema": RESPONSE_SCHEMA,
                "returncode": 70,
                "stdout": "",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=40),
            }
        jobs_completed += 1
        response["worker_pid"] = os.getpid()
        response["worker_sequence"] = jobs_completed
        response["cpu_slots"] = cpu_slots
        _write(f"{PROTOCOL} RESULT {job_id} {_encode(response)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
