# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child for FEM case generation and solver execution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

_PROTOCOL = "vibecad-native-analyze-solver-execution-v1"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_PROGRESS_BYTES = 8 * 1024
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024 * 1024
_MAX_INPUT_FILES = 4096
_ALLOWED_PREFERENCES = {
    (
        "User parameter:BaseApp/Preferences/Mod/Fem/General",
        "KeepResultsOnReRun",
        "bool",
    ),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Ccx", "ccxBinaryPath", "string"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Ccx", "AnalysisNumCPUs", "int"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Ccx", "BinaryOutput", "bool"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "gridBinaryPath", "string"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "elmerBinaryPath", "string"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "mpiBinaryPath", "string"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "NumberOfTasks", "int"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "ThreadsPerTask", "int"),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Elmer", "MaxOutputLevel", "int"),
    (
        "User parameter:BaseApp/Preferences/Mod/Fem/Mystran",
        "mystranBinaryPath",
        "string",
    ),
    ("User parameter:BaseApp/Preferences/Mod/Fem/Z88", "z88BinaryPath", "string"),
    (
        "User parameter:BaseApp/Preferences/Mod/Fem/OpenFOAM",
        "EnvironmentFile",
        "string",
    ),
    ("User parameter:BaseApp/Preferences/Units", "UserSchema", "int"),
}
_COMMON_PREFERENCES = {
    (
        "User parameter:BaseApp/Preferences/Mod/Fem/General",
        "KeepResultsOnReRun",
        "bool",
    ),
    ("User parameter:BaseApp/Preferences/Units", "UserSchema", "int"),
}
_BACKEND_PREFERENCE_PATHS = {
    "calculix": "User parameter:BaseApp/Preferences/Mod/Fem/Ccx",
    "elmer": "User parameter:BaseApp/Preferences/Mod/Fem/Elmer",
    "mystran": "User parameter:BaseApp/Preferences/Mod/Fem/Mystran",
    "openfoam": "User parameter:BaseApp/Preferences/Mod/Fem/OpenFOAM",
    "z88": "User parameter:BaseApp/Preferences/Mod/Fem/Z88",
}


class _ChildFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise _ChildFailure(code, message)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact escaped its private workspace.",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is unavailable.",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is not a regular file.",
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
            _fail(
                "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                "A detached FEM artifact changed while opening.",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail(
                    "NATIVE_ANALYZE_SOLVER_INPUT_LIMIT",
                    "A detached FEM artifact exceeds its safety bound.",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is empty.",
        )
    return bytes(data)


def _hash_regular(path: Path, *, root: Path, maximum: int) -> tuple[int, str]:
    """Authenticate a potentially large artifact without retaining it in memory."""

    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact escaped its private workspace.",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is unavailable.",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is not a regular file.",
        )
    descriptor = -1
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _fail(
                "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                "A detached FEM artifact changed while opening.",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                _fail(
                    "NATIVE_ANALYZE_SOLVER_INPUT_LIMIT",
                    "A detached FEM artifact exceeds its safety bound.",
                )
            digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size < 1:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "A detached FEM artifact is empty.",
        )
    return size, digest.hexdigest()


def _write_private(path: Path, data: bytes, maximum: int) -> None:
    if not data or len(data) > maximum:
        _fail(
            "NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
            "A detached FEM metadata artifact exceeds its safety bound.",
        )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_private(path: Path, data: bytes) -> None:
    if not data or len(data) > _MAX_PROGRESS_BYTES:
        return
    temporary = path.with_suffix(".tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        _write_private(temporary, data, _MAX_PROGRESS_BYTES)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _request() -> tuple[dict[str, Any], Path, Path, str]:
    raw_path = os.environ.get("VIBECAD_NATIVE_ANALYZE_SOLVER_EXECUTION_REQUEST", "")
    if not raw_path:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM request path is unavailable.",
        )
    path = Path(raw_path).resolve()
    root = path.parent
    if path.name != "request.json":
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM request has an unexpected identity.",
        )
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    request_sha256 = hashlib.sha256(data).hexdigest()
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ChildFailure(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM request is unreadable.",
        ) from exc
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "solver",
        "timeout_seconds",
        "keep_results",
        "runtime_preferences",
        "case",
        "result",
    }
    if (
        not isinstance(value, dict)
        or frozenset(value) not in {
            frozenset(required),
            frozenset({*required, "mesh"}),
        }
        or value.get("protocol") != _PROTOCOL
        or Path(str(value.get("workspace") or "")).resolve() != root
        or value.get("snapshot") != "document.FCStd"
        or value.get("case") != "case"
        or value.get("result") != "result.json"
    ):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM request failed protocol validation.",
        )
    value.setdefault("mesh", None)
    return value, root, root / "result.json", request_sha256


def _solver_descriptor(value: Any) -> dict[str, Any]:
    required = {"object_name", "object_id", "type_id", "kind", "state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM solver descriptor is malformed.",
        )
    result = dict(value)
    if (
        not str(result["object_name"] or "")
        or type(result["object_id"]) is not int
        or result["object_id"] < 1
        or not str(result["type_id"] or "")
        or str(result["kind"])
        not in {"calculix", "elmer", "mystran", "openfoam", "z88"}
        or len(str(result["state_sha256"] or "")) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(result["state_sha256"])
        )
    ):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM solver descriptor is invalid.",
        )
    return result


def _mesh_descriptor(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    required = {"object_name", "object_id", "type_id", "kind", "state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM mesh descriptor is malformed.",
        )
    result = dict(value)
    if (
        not str(result["object_name"] or "")
        or type(result["object_id"]) is not int
        or result["object_id"] < 1
        or not str(result["type_id"] or "")
        or str(result["kind"]) not in {"gmsh", "netgen"}
        or len(str(result["state_sha256"] or "")) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(result["state_sha256"])
        )
    ):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM mesh descriptor is invalid.",
        )
    return result


def _isolate_solver_mesh(analysis: Any, selected: Any) -> None:
    """Keep one explicitly selected mesh in the private solver snapshot."""

    if selected not in tuple(getattr(analysis, "Group", ()) or ()):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The selected FEM mesh is outside the solver's study.",
        )
    for member in tuple(analysis.Group or ()):
        try:
            is_mesh = bool(member.isDerivedFrom("Fem::FemMeshObject"))
        except Exception:
            is_mesh = False
        if is_mesh and member is not selected:
            analysis.removeObject(member)


def _preferences(
    value: Any,
    solver_kind: str,
) -> tuple[tuple[str, str, str, Any], ...]:
    expected = _COMMON_PREFERENCES | {
        identity
        for identity in _ALLOWED_PREFERENCES
        if identity[0] == _BACKEND_PREFERENCE_PATHS[solver_kind]
    }
    if not isinstance(value, list) or len(value) != len(expected):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM runtime preferences are malformed.",
        )
    result = []
    identities = set()
    for item in value:
        if not isinstance(item, list) or len(item) != 4:
            _fail(
                "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                "A detached FEM runtime preference is malformed.",
            )
        path, name, kind, setting = item
        identity = (str(path), str(name), str(kind))
        if identity not in expected or identity in identities:
            _fail(
                "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                "A detached FEM runtime preference is unsupported.",
            )
        if (
            (kind == "bool" and type(setting) is not bool)
            or (kind == "int" and type(setting) is not int)
            or (
                kind == "string"
                and (not isinstance(setting, str) or len(setting) > 4096)
            )
        ):
            _fail(
                "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                "A detached FEM runtime preference has an invalid value.",
            )
        identities.add(identity)
        result.append((identity[0], identity[1], identity[2], setting))
    if identities != expected:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM runtime preference set is incomplete.",
        )
    return tuple(result)


def _apply_preferences(preferences: tuple[tuple[str, str, str, Any], ...]) -> None:
    import FreeCAD as App

    for path, name, kind, value in preferences:
        group = App.ParamGet(path)
        if kind == "bool":
            group.SetBool(name, value)
        elif kind == "int":
            group.SetInt(name, value)
        else:
            group.SetString(name, value)


def _progress_writer(root: Path, request_sha256: str):
    progress_path = root / "progress.json"

    def report(percent: int, message: str) -> None:
        mapped = max(20, min(82, 20 + int(int(percent) * 62 / 90)))
        value = {
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "progress_percent": mapped,
            "progress_message": str(message or "")[:160],
        }
        _replace_private(
            progress_path,
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    return report


def _execute(
    request: Mapping[str, Any], root: Path, request_sha256: str
) -> dict[str, Any]:
    import FreeCAD as App

    solver_spec = _solver_descriptor(request["solver"])
    mesh_spec = _mesh_descriptor(request["mesh"])
    timeout = request["timeout_seconds"]
    keep_results = request["keep_results"]
    snapshot_size = request["snapshot_bytes"]
    snapshot_sha256 = str(request["snapshot_sha256"] or "")
    if (
        type(timeout) is not int
        or not 1 <= timeout <= 86400
        or type(keep_results) is not bool
        or type(snapshot_size) is not int
        or not 1 <= snapshot_size <= _MAX_SNAPSHOT_BYTES
        or len(snapshot_sha256) != 64
    ):
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM execution bounds are invalid.",
        )
    preferences = _preferences(
        request["runtime_preferences"],
        str(solver_spec["kind"]),
    )
    captured_keep_results = next(
        value
        for path, name, kind, value in preferences
        if path.endswith("/General") and name == "KeepResultsOnReRun" and kind == "bool"
    )
    if captured_keep_results is not keep_results:
        _fail(
            "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            "The detached FEM result-retention guards disagree.",
        )
    _apply_preferences(preferences)
    snapshot_path = root / "document.FCStd"
    observed_snapshot_size, observed_snapshot_sha256 = _hash_regular(
        snapshot_path,
        root=root,
        maximum=_MAX_SNAPSHOT_BYTES,
    )
    if (
        observed_snapshot_size != snapshot_size
        or observed_snapshot_sha256 != snapshot_sha256
    ):
        _fail(
            "NATIVE_ANALYZE_SOLVER_SNAPSHOT_CHANGED",
            "The detached FEM document snapshot changed before execution.",
        )
    report = _progress_writer(root, request_sha256)
    report(1, "Generating exact FEM solver input outside the UI process")
    document = App.openDocument(str(snapshot_path))
    try:
        solver = document.getObject(str(solver_spec["object_name"]))
        if (
            solver is None
            or int(solver.ID) != solver_spec["object_id"]
            or str(solver.TypeId) != solver_spec["type_id"]
        ):
            _fail(
                "NATIVE_ANALYZE_STATE_STALE",
                "The exact FEM solver is unavailable in its frozen snapshot.",
            )
        from VibeCADNativeAnalyzeSolverState import solver_state

        state = solver_state(solver)
        if (
            state["solver_kind"] != solver_spec["kind"]
            or state["state_sha256"] != solver_spec["state_sha256"]
        ):
            _fail(
                "NATIVE_ANALYZE_STATE_STALE",
                "The exact FEM solver changed in its frozen snapshot.",
            )
        mesh = None
        if mesh_spec is not None:
            mesh = document.getObject(str(mesh_spec["object_name"]))
            if (
                mesh is None
                or int(mesh.ID) != mesh_spec["object_id"]
                or str(mesh.TypeId) != mesh_spec["type_id"]
            ):
                _fail(
                    "NATIVE_ANALYZE_STATE_STALE",
                    "The selected FEM mesh is unavailable in its frozen snapshot.",
                )
            from VibeCADNativeAnalyzeMeshState import (
                fem_mesh_definition_context_state,
                fem_mesh_definition_still_exact,
            )

            mesh_state = fem_mesh_definition_context_state(mesh)
            if (
                mesh_state["backend"] != mesh_spec["kind"]
                or not fem_mesh_definition_still_exact(
                    mesh,
                    str(mesh_spec["state_sha256"]),
                )
            ):
                _fail(
                    "NATIVE_ANALYZE_STATE_STALE",
                    "The selected FEM mesh changed in its frozen snapshot.",
                )
            analysis = document.getObject(str(state.get("analysis") or ""))
            if analysis is None:
                _fail(
                    "NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
                    "The solver's FEM study is unavailable in its frozen snapshot.",
                )
            _isolate_solver_mesh(analysis, mesh)
        from VibeCADNativeAnalyzeSolverExecution import (
            prepare_solver_execution_request,
            run_solver_execution,
        )

        execution_request = prepare_solver_execution_request(
            document,
            str(document.Uid),
            target={
                "object_name": str(solver.Name),
                "expected_state_sha256": str(solver_spec["state_sha256"]),
            },
            mesh=(
                {
                    "object_name": str(mesh.Name),
                    "expected_state_sha256": str(mesh_spec["state_sha256"]),
                }
                if mesh is not None and mesh_spec is not None
                else None
            ),
            timeout_seconds=timeout,
            working_directory=root / "case",
            progress=report,
        )
        if execution_request.runtime_preferences != preferences:
            _fail(
                "NATIVE_ANALYZE_STATE_STALE",
                "The isolated FEM runtime preferences changed before execution.",
            )
        report(7, f"Prepared exact {solver_spec['kind'].title()} solver case")
        prepared = run_solver_execution(
            execution_request,
            cancelled=lambda: False,
            progress=report,
        )
        importer_state = dict(execution_request.importer_state)
        encoded_importer = json.dumps(
            importer_state,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded_importer.encode("utf-8")) > _MAX_REQUEST_BYTES:
            _fail(
                "NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
                "The detached FEM importer metadata exceeds its safety bound.",
            )
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "solver_name": str(solver.Name),
            "solver_kind": str(execution_request.target.kind),
            "solver_state_sha256": str(execution_request.target.expected_state_sha256),
            "implementation": str(execution_request.implementation),
            "case": "case",
            "input_sha256": str(execution_request.input_sha256),
            "input_file_count": int(execution_request.input_file_count),
            "keep_results": bool(execution_request.keep_results),
            "importer_state": importer_state,
            "stages": list(prepared.stages),
        }
    finally:
        App.closeDocument(document.Name)


def _main() -> int:
    result_path: Path | None = None
    request_sha256 = ""
    try:
        request, root, result_path, request_sha256 = _request()
        result = _execute(request, root, request_sha256)
        _write_private(
            result_path,
            json.dumps(
                result,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            _MAX_RESULT_BYTES,
        )
        return 0
    except _ChildFailure as exc:
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": exc.code,
            "message": str(exc)[:320],
        }
    except Exception as exc:
        code = str(getattr(exc, "error_code", "") or "")
        message = str(exc).strip()
        repair = getattr(exc, "repair", None)
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": (
                code
                if code.startswith("NATIVE_ANALYZE_")
                else "NATIVE_ANALYZE_SOLVER_EXECUTION_FAILED"
            ),
            "message": (
                message[:320]
                if code.startswith("NATIVE_ANALYZE_") and message
                else "The isolated FEM solver process failed."
            ),
        }
        if isinstance(repair, Mapping):
            try:
                encoded_repair = json.dumps(
                    dict(repair),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                encoded_repair = ""
            if encoded_repair and len(encoded_repair.encode("utf-8")) <= 2048:
                failure["repair"] = json.loads(encoded_repair)
    if result_path is not None and not result_path.exists():
        try:
            _write_private(
                result_path,
                json.dumps(failure, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                ),
                _MAX_RESULT_BYTES,
            )
        except Exception:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
