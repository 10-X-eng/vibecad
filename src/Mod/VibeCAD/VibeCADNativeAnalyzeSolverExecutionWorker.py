# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable isolated FEM worker and authenticated result adoption."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    PreparedSolverExecution,
    SolverExecutionRequest,
)
from VibeCADNativeAnalyzeSolverExecutionInput import (
    ANALYZE_SOLVER_EXECUTION_PROTOCOL,
    MAX_SOLVER_CHILD_BYTES,
    MAX_SOLVER_REQUEST_BYTES,
    MAX_SOLVER_SNAPSHOT_BYTES,
    FrozenSolverExecution,
)
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADScriptedProcess import run_process

MAX_SOLVER_RESULT_BYTES = 16 * 1024 * 1024
MAX_SOLVER_PROGRESS_BYTES = 8 * 1024
MAX_SOLVER_PREPARATION_SECONDS = 3600.0


class _ArtifactChangedWhileOpening(NativeAnalyzeError):
    """An atomic metadata replacement raced with one parent-side sample."""

    def __init__(self) -> None:
        super().__init__(
            "A detached FEM artifact changed while opening.",
            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )


def _error(message: str, code: str) -> None:
    raise NativeAnalyzeError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached FEM artifact escaped its private workspace.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeAnalyzeError(
            "A detached FEM artifact is unavailable.",
            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A detached FEM artifact is not a regular file.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    descriptor = -1
    data = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            raise _ArtifactChangedWhileOpening()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "A detached FEM artifact exceeds its safety bound.",
                    "NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "A detached FEM artifact is empty.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenSolverExecution) -> dict[str, str]:
    root = str(frozen.workspace.path)
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "TEMP": root,
            "TMP": root,
            "TMPDIR": root,
            "VIBECAD_NATIVE_ANALYZE_SOLVER_EXECUTION_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_ANALYZE_SOLVER_EXECUTION_CHILD": str(
                frozen.workspace.child.path
            ),
        }
    )
    return environment


def _validate_inputs(frozen: FrozenSolverExecution) -> None:
    try:
        validate_frozen_file(
            frozen.workspace.freecadcmd,
            maximum=None,
            executable=True,
        )
        validate_frozen_file(
            frozen.workspace.child,
            maximum=MAX_SOLVER_CHILD_BYTES,
        )
        validate_frozen_file(frozen.request, maximum=MAX_SOLVER_REQUEST_BYTES)
        validate_frozen_file(frozen.snapshot, maximum=MAX_SOLVER_SNAPSHOT_BYTES)
    except Exception as exc:
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            "A frozen FEM worker input changed after preflight.",
            error_code="NATIVE_ANALYZE_SOLVER_INPUT_CHANGED",
        ) from exc


def _read_progress(
    frozen: FrozenSolverExecution,
    progress: Callable[[int, str], None],
    state: dict[str, Any],
) -> None:
    path = frozen.workspace.path / "progress.json"
    if not path.exists():
        return
    try:
        data = _read_regular(
            path,
            root=frozen.workspace.path,
            maximum=MAX_SOLVER_PROGRESS_BYTES,
        )
    except _ArtifactChangedWhileOpening:
        # progress.json is an atomic, replace-in-place status channel. Missing
        # one racing sample is harmless; the next process poll reads the new
        # complete file. Final result artifacts remain strict and fatal.
        return
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeAnalyzeError(
            "The isolated FEM progress report is unreadable.",
            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        ) from exc
    required = {
        "protocol",
        "request_sha256",
        "progress_percent",
        "progress_message",
    }
    percent = value.get("progress_percent") if isinstance(value, Mapping) else None
    message = value.get("progress_message") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("protocol") != ANALYZE_SOLVER_EXECUTION_PROTOCOL
        or value.get("request_sha256") != frozen.request_sha256
        or type(percent) is not int
        or not 10 <= percent <= 89
        or not isinstance(message, str)
        or not message.strip()
        or len(message) > 160
    ):
        _error(
            "The isolated FEM progress report failed protocol validation.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    if percent > state["percent"] or message != state["message"]:
        progress(percent, message)
        state["percent"] = percent
        state["message"] = message


def _simple_name(value: Any, field: str) -> str:
    name = str(value or "")
    if not name or len(name) > 260 or Path(name).name != name or name in {".", ".."}:
        _error(
            f"The detached FEM {field} is invalid.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    return name


def _sha256(value: Any, field: str) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        _error(
            f"The detached FEM {field} is invalid.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    return digest


def _importer_state(
    value: Any,
    *,
    frozen: FrozenSolverExecution,
    implementation: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _error(
            "The detached FEM importer metadata is malformed.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    result = dict(value)
    kind = frozen.captured.target.kind
    case = frozen.workspace.path / "case"
    if kind == "calculix" and implementation == "ccx_tools":
        if set(result) != {"input_file"}:
            _error(
                "The detached CalculiX importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        path = Path(str(result["input_file"] or "")).resolve()
        try:
            path.relative_to(case)
        except ValueError:
            _error(
                "The detached CalculiX input path escaped its private case.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        if not path.is_file():
            _error(
                "The detached CalculiX input file is unavailable.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        result["input_file"] = str(path)
    elif kind == "calculix":
        if set(result) != {"input_deck"}:
            _error(
                "The detached CalculiX importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        result["input_deck"] = _simple_name(result["input_deck"], "input deck")
    elif kind == "elmer":
        if set(result) != {"result_format"} or result["result_format"] not in {
            ".vtu",
            ".pvtu",
            ".pvd",
        }:
            _error(
                "The detached Elmer importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
    elif kind == "mystran":
        if set(result) != {"input_deck"}:
            _error(
                "The detached Mystran importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        result["input_deck"] = _simple_name(result["input_deck"], "input deck")
    elif kind == "openfoam":
        if set(result) != {"result_glob", "solver_log", "summary_context"}:
            _error(
                "The detached OpenFOAM importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        if result["result_glob"] != "VTK/*.vtk":
            _error(
                "The detached OpenFOAM result path is invalid.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        _simple_name(result["solver_log"], "solver log")
        if not isinstance(result["summary_context"], Mapping):
            _error(
                "The detached OpenFOAM summary context is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
    elif kind == "z88":
        if result:
            _error(
                "The detached Z88 importer metadata is malformed.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
    else:
        _error(
            "The detached FEM backend is unsupported.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    return result


def _stages(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        _error(
            "The detached FEM stage summary is malformed.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    result = []
    for index, item in enumerate(value, 1):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"stage", "program", "exit_code"}
            or item.get("stage") != index
            or item.get("exit_code") != 0
        ):
            _error(
                "The detached FEM stage summary is invalid.",
                "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
            )
        result.append(
            {
                "stage": index,
                "program": _simple_name(item.get("program"), "stage program"),
                "exit_code": 0,
            }
        )
    return tuple(result)


def _read_result(frozen: FrozenSolverExecution) -> PreparedSolverExecution:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_SOLVER_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeAnalyzeError(
            "The detached FEM result is unreadable.",
            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached FEM result is malformed.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        repair = value.get("repair")
        try:
            encoded_repair = (
                json.dumps(
                    dict(repair),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if isinstance(repair, Mapping)
                else ""
            )
        except (TypeError, ValueError):
            encoded_repair = ""
        if (
            value.get("protocol") != ANALYZE_SOLVER_EXECUTION_PROTOCOL
            or value.get("request_sha256") != frozen.request_sha256
            or not code.startswith("NATIVE_ANALYZE_")
            or not message
            or (repair is not None and not encoded_repair)
            or len(encoded_repair.encode("utf-8")) > 2048
        ):
            _error(
                "The detached FEM solver process failed.",
                "NATIVE_ANALYZE_SOLVER_EXECUTION_FAILED",
            )
        raise NativeAnalyzeError(
            message,
            error_code=code,
            repair=(json.loads(encoded_repair) if encoded_repair else None),
        )
    required = {
        "ok",
        "protocol",
        "request_sha256",
        "solver_name",
        "solver_kind",
        "solver_state_sha256",
        "implementation",
        "case",
        "input_sha256",
        "input_file_count",
        "keep_results",
        "importer_state",
        "stages",
    }
    implementation = str(value.get("implementation") or "")
    input_count = value.get("input_file_count")
    kind = frozen.captured.target.kind
    allowed_implementations = (
        {"pipeline", "ccx_tools"} if kind == "calculix" else {kind}
    )
    if (
        set(value) != required
        or value.get("ok") is not True
        or value.get("protocol") != ANALYZE_SOLVER_EXECUTION_PROTOCOL
        or value.get("request_sha256") != frozen.request_sha256
        or value.get("solver_name") != frozen.solver_name
        or value.get("solver_kind") != frozen.captured.target.kind
        or value.get("solver_state_sha256")
        != frozen.captured.target.expected_state_sha256
        or value.get("case") != "case"
        or implementation not in allowed_implementations
        or type(input_count) is not int
        or not 1 <= input_count <= 4096
        or value.get("keep_results") is not frozen.captured.keep_results
    ):
        _error(
            "The detached FEM result failed protocol validation.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    input_sha256 = _sha256(value["input_sha256"], "input digest")
    case = frozen.workspace.path / "case"
    if case.is_symlink() or not case.is_dir():
        _error(
            "The detached FEM case directory is unavailable.",
            "NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
        )
    request = SolverExecutionRequest(
        target=frozen.captured.target,
        implementation=implementation,
        history_operations=frozen.captured.history_operations,
        working_directory=str(case),
        commands=(),
        environment={},
        timeout_seconds=frozen.captured.timeout_seconds,
        input_sha256=input_sha256,
        input_file_count=input_count,
        keep_results=frozen.captured.keep_results,
        importer_state=_importer_state(
            value["importer_state"],
            frozen=frozen,
            implementation=implementation,
        ),
        runtime_preferences=frozen.captured.runtime_preferences,
        mesh=frozen.captured.mesh,
    )
    return PreparedSolverExecution(request=request, stages=_stages(value["stages"]))


def execute_frozen_solver_execution(
    frozen: FrozenSolverExecution,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedSolverExecution:
    """Generate and run one exact solver case outside the VibeCAD GUI process."""

    if not isinstance(frozen, FrozenSolverExecution):
        raise TypeError("frozen must be a FrozenSolverExecution")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(8, "Authenticating exact FEM solver inputs")
    _validate_inputs(frozen)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(10, "Starting isolated FEM solver worker")
    state = {"percent": 10, "message": "Starting isolated FEM solver worker"}
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_ANALYZE_SOLVER_EXECUTION_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=(
            float(frozen.captured.timeout_seconds) + MAX_SOLVER_PREPARATION_SECONDS
        ),
        memory_limit_bytes=0,
        poll_callback=lambda: _read_progress(frozen, progress, state),
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated FEM solver process could not start.",
            "NATIVE_ANALYZE_SOLVER_START_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "FEM case generation or execution exceeded its safety timeout.",
            "NATIVE_ANALYZE_SOLVER_TIMEOUT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(max(84, state["percent"]), "Reading isolated FEM solver outcome")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated FEM solver process exited unsuccessfully.",
            "NATIVE_ANALYZE_SOLVER_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact FEM solver results for import")
    return prepared


__all__ = ["execute_frozen_solver_execution"]
