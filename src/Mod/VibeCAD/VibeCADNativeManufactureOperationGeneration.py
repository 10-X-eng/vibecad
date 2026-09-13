# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated CAM path generation with one exact document-thread publication."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    persistent_configuration_state,
    resolve_job_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADScriptedProcess import run_process


MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024 * 1024
MAX_GENERATED_PYTHON_STATE_BYTES = 64 * 1024 * 1024
MAX_GENERATED_PYTHON_STATE_DEPTH = 64
MAX_GENERATED_PYTHON_STATE_NODES = 1_000_000
PATH_TIMEOUT_SECONDS = 3600.0
PATH_MEMORY_LIMIT_BYTES = 4 * 1024 * 1024 * 1024
_CACHE_LIMIT = 8
_CACHE_BYTES = 256 * 1024 * 1024
_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_SIZE = 0
_CACHE_LOCK = threading.RLock()
_GENERATION_VALUE_TYPES = frozenset(
    {
        "App::PropertyAngle",
        "App::PropertyBool",
        "App::PropertyBoolList",
        "App::PropertyDistance",
        "App::PropertyEnumeration",
        "App::PropertyFloat",
        "App::PropertyFloatConstraint",
        "App::PropertyFloatList",
        "App::PropertyInteger",
        "App::PropertyIntegerConstraint",
        "App::PropertyIntegerList",
        "App::PropertyLength",
        "App::PropertyPercent",
        "App::PropertyPythonObject",
        "App::PropertyQuantity",
        "App::PropertyString",
        "App::PropertyStringList",
        "App::PropertyVector",
        "App::PropertyVectorDistance",
        "App::PropertyVectorList",
    }
)


@dataclass(frozen=True, slots=True)
class FrozenFile:
    path: Path = field(repr=False, compare=False)
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str | None = None


@dataclass(slots=True)
class OperationGenerationWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class CapturedOperationGeneration:
    request: Mapping[str, Any]
    request_bytes: bytes = field(repr=False, compare=False)
    job: Any = field(repr=False, compare=False)
    job_name: str
    job_state_sha256: str
    geometry_tolerance_mm: float


@dataclass(frozen=True, slots=True)
class MaterializedOperationGeneration:
    workspace: OperationGenerationWorkspace = field(repr=False, compare=False)
    captured: CapturedOperationGeneration = field(repr=False, compare=False)
    snapshot_path: Path = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class FrozenOperationGeneration:
    workspace: OperationGenerationWorkspace = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request_file: FrozenFile = field(repr=False, compare=False)
    result_path: Path = field(repr=False, compare=False)
    request: Mapping[str, Any]
    cache_key: str
    job_name: str


@dataclass(frozen=True, slots=True)
class PreparedOperationGeneration:
    frozen: FrozenOperationGeneration = field(repr=False, compare=False)
    artifact: Mapping[str, Any]
    artifact_sha256: str
    cache_hit: bool


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def _capture_document_state(
    document: Any,
    request: Mapping[str, Any],
    request_bytes: bytes,
    geometry_tolerance_mm: float,
) -> CapturedOperationGeneration:
    job, current = resolve_job_target(document, request["job"])
    return CapturedOperationGeneration(
        request=dict(request),
        request_bytes=request_bytes,
        job=job,
        job_name=str(job.Name),
        job_state_sha256=str(current["state_sha256"]),
        geometry_tolerance_mm=geometry_tolerance_mm,
    )


def _cam_geometry_tolerance() -> float:
    import Path as CamPath

    value = float(CamPath.Preferences.defaultGeometryTolerance())
    if not math.isfinite(value) or value <= 0.0:
        _error(
            "CAM geometry tolerance must be positive and finite.",
            "NATIVE_MANUFACTURE_STATE_INVALID",
        )
    return round(value, 12)


def _captured_state_is_current(
    document: Any,
    captured: CapturedOperationGeneration,
) -> bool:
    try:
        job, current = resolve_job_target(document, captured.request["job"])
        return bool(
            job is captured.job
            and str(current["state_sha256"]) == captured.job_state_sha256
            and _cam_geometry_tolerance() == captured.geometry_tolerance_mm
            and _transaction_id(document) == 0
            and not bool(getattr(document, "HasPendingTransaction", False))
            and not bool(getattr(document, "Recomputing", False))
            and not bool(getattr(document, "RecomputePending", False))
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _freeze_file(
    path: Path,
    maximum: int,
    *,
    executable: bool = False,
    hash_contents: bool = True,
) -> FrozenFile:
    try:
        resolved = path.resolve(strict=True)
        before = resolved.stat()
    except OSError as exc:
        raise NativeManufactureError(
            "An isolated CAM path file is unavailable.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or not 1 <= int(before.st_size) <= maximum
        or (executable and not os.access(resolved, os.X_OK))
    ):
        _error(
            "An isolated CAM path file is not a usable bounded regular file.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    digest = None
    if hash_contents:
        value = hashlib.sha256()
        size = 0
        with resolved.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    _error(
                        "An isolated CAM path file exceeds its bound.",
                        "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
                    )
                value.update(chunk)
        digest = value.hexdigest()
    try:
        after = resolved.stat()
    except OSError as exc:
        raise NativeManufactureError(
            "An isolated CAM path file disappeared during inspection.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    identity = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
    )
    if identity != (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
    ):
        _error(
            "An isolated CAM path file changed during inspection.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    return FrozenFile(resolved, *identity, digest)


def _validate_file(frozen: FrozenFile, maximum: int, *, executable: bool = False) -> None:
    current = _freeze_file(
        frozen.path,
        maximum,
        executable=executable,
        hash_contents=frozen.sha256 is not None,
    )
    if current != frozen:
        _error(
            "An isolated CAM path runtime changed after capture.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )


def _freecadcmd() -> Path:
    import FreeCAD as App

    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    home = Path(str(App.getHomePath())).resolve()
    for directory in (home / "bin", home, home.parent / "MacOS"):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
    _error(
        "The isolated FreeCADCmd CAM path worker is unavailable.",
        "NATIVE_MANUFACTURE_PATH_WORKER_UNAVAILABLE",
    )
    raise AssertionError("unreachable")


def _canonical_request(
    document_uid: str,
    request: Mapping[str, Any],
    *,
    geometry_tolerance_mm: float,
) -> bytes:
    try:
        data = json.dumps(
            {
                "document_uid": document_uid,
                "request": dict(request),
                "runtime_preferences": {
                    "geometry_tolerance_mm": geometry_tolerance_mm,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The CAM operation request cannot be isolated.",
            error_code="NATIVE_MANUFACTURE_PATH_REQUEST_INVALID",
        ) from exc
    if not 1 <= len(data) <= MAX_REQUEST_BYTES:
        _error(
            "The CAM operation request exceeds its isolated-worker bound.",
            "NATIVE_MANUFACTURE_PATH_REQUEST_INVALID",
        )
    return data


def capture_operation_generation(
    document: Any,
    document_uid: str,
    request: Mapping[str, Any],
) -> CapturedOperationGeneration:
    """Capture exact setup-scoped input without generating a path."""

    if bool(getattr(document, "HasPendingTransaction", False)) or _transaction_id(document):
        _error(
            "Finish or cancel the open task before generating a CAM path.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for the document recompute before generating a CAM path.",
            "NATIVE_MANUFACTURE_PATH_BACKGROUND_UNAVAILABLE",
        )
    geometry_tolerance_mm = _cam_geometry_tolerance()
    request_bytes = _canonical_request(
        document_uid,
        request,
        geometry_tolerance_mm=geometry_tolerance_mm,
    )
    if not isinstance(request.get("job"), Mapping):
        _error(
            "CAM path generation requires one exact setup target.",
            "NATIVE_MANUFACTURE_PATH_REQUEST_INVALID",
        )
    return _capture_document_state(
        document,
        request,
        request_bytes,
        geometry_tolerance_mm,
    )


def create_operation_generation_workspace() -> OperationGenerationWorkspace:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-path-")
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = _freeze_file(
            Path(__file__).with_name(
                "VibeCADNativeManufactureOperationGenerationChild.py"
            ),
            1024 * 1024,
        )
        command = _freeze_file(
            _freecadcmd(),
            1024 * 1024 * 1024,
            executable=True,
            hash_contents=False,
        )
        return OperationGenerationWorkspace(temporary, root, command, child)
    except Exception:
        temporary.cleanup()
        raise


def materialize_operation_generation(
    document: Any,
    captured: CapturedOperationGeneration,
    workspace: OperationGenerationWorkspace,
) -> MaterializedOperationGeneration:
    """Copy the exact live document on its owner thread."""

    if not _captured_state_is_current(document, captured):
        _error(
            "The exact CAM setup changed before its snapshot was created.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    snapshot = workspace.path / "snapshot.FCStd"
    try:
        result = document.saveCopy(str(snapshot))
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM document could not be copied for path generation.",
            error_code="NATIVE_MANUFACTURE_PATH_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot.is_file():
        _error(
            "The exact CAM document snapshot was not created.",
            "NATIVE_MANUFACTURE_PATH_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot, 0o600)
    if not _captured_state_is_current(document, captured):
        _error(
            "Creating the private CAM snapshot changed the live document or UI state.",
            "NATIVE_MANUFACTURE_STATE_INVALID",
        )
    return MaterializedOperationGeneration(workspace, captured, snapshot)


def freeze_operation_generation(
    materialized: MaterializedOperationGeneration,
) -> FrozenOperationGeneration:
    """Authenticate the snapshot and bounded worker request off-thread."""

    workspace = materialized.workspace
    captured = materialized.captured
    snapshot = _freeze_file(materialized.snapshot_path, MAX_SNAPSHOT_BYTES)
    request_path = workspace.path / "request.json"
    result_path = workspace.path / "result.json"
    payload = json.loads(captured.request_bytes.decode("utf-8"))
    payload.update(
        {
            "schema": "vibecad-cam-path-request-v1",
            "snapshot": snapshot.path.name,
            "snapshot_bytes": snapshot.size,
            "snapshot_sha256": snapshot.sha256,
            "result": result_path.name,
        }
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        _error(
            "The CAM path worker request exceeds its bound.",
            "NATIVE_MANUFACTURE_PATH_REQUEST_INVALID",
        )
    _write_private(request_path, encoded)
    request_file = _freeze_file(request_path, MAX_REQUEST_BYTES)
    cache_key = hashlib.sha256(
        captured.request_bytes
        + b"\0"
        + str(workspace.child.sha256).encode("ascii")
    ).hexdigest()
    return FrozenOperationGeneration(
        workspace,
        snapshot,
        request_file,
        result_path,
        dict(captured.request),
        cache_key,
        captured.job_name,
    )


def _cache_get(key: str) -> bytes | None:
    with _CACHE_LOCK:
        data = _CACHE.pop(key, None)
        if data is not None:
            _CACHE[key] = data
        return data


def _cache_put(key: str, data: bytes) -> None:
    global _CACHE_SIZE
    if len(data) > _CACHE_BYTES:
        return
    with _CACHE_LOCK:
        prior = _CACHE.pop(key, None)
        if prior is not None:
            _CACHE_SIZE -= len(prior)
        _CACHE[key] = data
        _CACHE_SIZE += len(data)
        while len(_CACHE) > _CACHE_LIMIT or _CACHE_SIZE > _CACHE_BYTES:
            _old_key, old = _CACHE.popitem(last=False)
            _CACHE_SIZE -= len(old)


def _read_artifact(data: bytes) -> Mapping[str, Any]:
    try:
        artifact = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeManufactureError(
            "The isolated CAM path worker returned unreadable output.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    if not isinstance(artifact, Mapping) or artifact.get("schema") != (
        "vibecad-cam-path-result-v1"
    ):
        _error(
            "The isolated CAM path worker returned invalid output.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    if artifact.get("ok") is not True:
        repair = artifact.get("repair")
        raise NativeManufactureError(
            str(artifact.get("message") or "")[:320]
            or "The isolated CAM path worker failed.",
            error_code=str(artifact.get("error_code") or "")[:80]
            or "NATIVE_MANUFACTURE_PATH_WORKER_FAILED",
            repair=dict(repair) if isinstance(repair, Mapping) else None,
        )
    if set(artifact) != {
        "schema",
        "ok",
        "commands",
        "center_mm",
        "cycle_time",
        "derived_properties",
        "generation_property_changes",
        "generation_diagnostics",
    }:
        _error(
            "The isolated CAM path worker returned an unknown result shape.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    if not isinstance(artifact.get("commands"), list) or not artifact["commands"]:
        _error(
            "The isolated CAM path worker returned no toolpath commands.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    return dict(artifact)


def generate_operation_path(
    frozen: FrozenOperationGeneration,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedOperationGeneration:
    """Generate one path in isolated FreeCADCmd or reuse its exact-session cache."""

    _validate_file(frozen.workspace.freecadcmd, 1024 * 1024 * 1024, executable=True)
    _validate_file(frozen.workspace.child, 1024 * 1024)
    _validate_file(frozen.snapshot, MAX_SNAPSHOT_BYTES)
    _validate_file(frozen.request_file, MAX_REQUEST_BYTES)
    cached = _cache_get(frozen.cache_key)
    if cached is not None:
        progress(88, "Reusing exact CAM path")
        return PreparedOperationGeneration(
            frozen,
            _read_artifact(cached),
            hashlib.sha256(cached).hexdigest(),
            True,
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(8, "Starting isolated CAM path generation")
    environment = dict(os.environ)
    environment["VIBECAD_NATIVE_CAM_PATH_REQUEST"] = str(frozen.request_file.path)
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), str(frozen.workspace.child.path)],
        cwd=frozen.workspace.path,
        environment=environment,
        cancellation_check=cancelled,
        timeout_seconds=PATH_TIMEOUT_SECONDS,
        memory_limit_bytes=PATH_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated CAM path worker could not start.",
            "NATIVE_MANUFACTURE_PATH_WORKER_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "CAM path generation exceeded one hour.",
            "NATIVE_MANUFACTURE_PATH_WORKER_TIMEOUT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "CAM path generation exceeded 4 GiB.",
            "NATIVE_MANUFACTURE_PATH_WORKER_LIMIT",
        )
    if not frozen.result_path.is_file():
        _error(
            "The isolated CAM path worker returned no result.",
            "NATIVE_MANUFACTURE_PATH_WORKER_FAILED",
        )
    result_file = _freeze_file(frozen.result_path, MAX_RESULT_BYTES)
    try:
        data = result_file.path.read_bytes()
    except OSError as exc:
        raise NativeManufactureError(
            "The isolated CAM path worker result could not be read.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    _validate_file(result_file, MAX_RESULT_BYTES)
    artifact = _read_artifact(data)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated CAM path worker exited unsuccessfully.",
            "NATIVE_MANUFACTURE_PATH_WORKER_FAILED",
        )
    _cache_put(frozen.cache_key, data)
    progress(88, "Verified isolated CAM path")
    return PreparedOperationGeneration(frozen, artifact, str(result_file.sha256), False)


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            f"The isolated CAM path returned invalid {name}.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    if not math.isfinite(result):
        _error(
            f"The isolated CAM path returned invalid {name}.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    return result


def _path_from_artifact(artifact: Mapping[str, Any]) -> Any:
    import FreeCAD as App
    import Path as CamPath

    commands = []
    for index, item in enumerate(list(artifact["commands"])):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"name", "parameters", "annotations"}
            or not isinstance(item["parameters"], Mapping)
            or not isinstance(item["annotations"], Mapping)
        ):
            _error(
                f"The isolated CAM command {index} is malformed.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        command = CamPath.Command(str(item["name"]), dict(item["parameters"]))
        command.Annotations = dict(item["annotations"])
        commands.append(command)
    path = CamPath.Path(commands)
    center = artifact.get("center_mm")
    if not isinstance(center, list) or len(center) != 3:
        _error(
            "The isolated CAM path returned no valid center.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    path.Center = App.Vector(*(_finite(value, "path center") for value in center))
    return path


def _adopt_diagnostics(operation: Any, artifact: Mapping[str, Any]) -> None:
    diagnostics = artifact.get("generation_diagnostics")
    if not isinstance(diagnostics, Mapping):
        _error(
            "The isolated CAM path returned no generation diagnostics.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    command_count = len(artifact["commands"])
    if (
        diagnostics.get("status") != "succeeded"
        or diagnostics.get("stage") != "complete"
        or diagnostics.get("error") is not None
        or int(diagnostics.get("command_count", -1)) != command_count
    ):
        _error(
            "The isolated CAM path diagnostics do not match its toolpath.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    proxy = operation.Proxy
    proxy._beginGenerationDiagnostics(operation)
    details = {
        key: value
        for key, value in diagnostics.items()
        if key not in {"generation", "operation", "operation_type", "status", "stage"}
    }
    proxy._updateGenerationDiagnostics("complete", status="succeeded", **details)


def _generation_property_changes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return exact simple properties changed by path generation itself."""

    changed = []
    for name in sorted(set(before) | set(after)):
        prior = before.get(name)
        current = after.get(name)
        if prior == current:
            continue
        if not isinstance(prior, Mapping) or not isinstance(current, Mapping):
            _error(
                "CAM path generation changed an unreadable persistent property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        type_id = str(current.get("type") or "")
        if (
            str(prior.get("type") or "") != type_id
            or type_id not in _GENERATION_VALUE_TYPES
            or "expression_bound" in prior
            or "expression_bound" in current
            or set(current) != {"type", "value"}
        ):
            _error(
                f"CAM path generation changed unsupported property {name!r}.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        value = current["value"]
        if type_id == "App::PropertyPythonObject":
            value = _plain_json_property(value)
        changed.append(
            {
                "name": name,
                "type": type_id,
                "value": value,
            }
        )
    return changed


def _plain_json_property(value: Any) -> Any:
    nodes = 0

    def validate(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if (
            depth > MAX_GENERATED_PYTHON_STATE_DEPTH
            or nodes > MAX_GENERATED_PYTHON_STATE_NODES
        ):
            _error(
                "The isolated CAM path returned oversized generated Python state.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        if item is None or type(item) in {bool, int, str}:
            return
        if type(item) is float:
            if not math.isfinite(item):
                _error(
                    "The isolated CAM path returned non-finite generated Python state.",
                    "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
                )
            return
        if isinstance(item, list):
            for child in item:
                validate(child, depth + 1)
            return
        if isinstance(item, dict) and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        _error(
            "The isolated CAM path returned non-JSON generated Python state.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )

    validate(value, 0)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise NativeManufactureError(
            "The isolated CAM path returned unreadable generated Python state.",
            error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        ) from exc
    if len(encoded) > MAX_GENERATED_PYTHON_STATE_BYTES:
        _error(
            "The isolated CAM path returned oversized generated Python state.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    return json.loads(encoded)


def _property_assignment(type_id: str, value: Any) -> Any:
    if type_id == "App::PropertyPythonObject":
        return _plain_json_property(value)
    if type_id in {"App::PropertyVector", "App::PropertyVectorDistance"}:
        if (
            not isinstance(value, list)
            or len(value) != 3
            or any(isinstance(item, bool) for item in value)
        ):
            _error(
                "The isolated CAM path returned an invalid vector property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        import FreeCAD as App

        return App.Vector(*(_finite(item, "generated property") for item in value))
    if type_id == "App::PropertyVectorList":
        if not isinstance(value, list) or len(value) > 4096:
            _error(
                "The isolated CAM path returned an invalid vector-list property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        return [_property_assignment("App::PropertyVector", item) for item in value]
    if type_id in {"App::PropertyBool"}:
        if type(value) is not bool:
            _error(
                "The isolated CAM path returned an invalid boolean property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        return value
    if type_id in {"App::PropertyInteger", "App::PropertyIntegerConstraint"}:
        if type(value) is not int:
            _error(
                "The isolated CAM path returned an invalid integer property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        return value
    if type_id in {
        "App::PropertyAngle",
        "App::PropertyDistance",
        "App::PropertyFloat",
        "App::PropertyFloatConstraint",
        "App::PropertyLength",
        "App::PropertyPercent",
        "App::PropertyQuantity",
    }:
        if isinstance(value, bool):
            _error(
                "The isolated CAM path returned an invalid numeric property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        return _finite(value, "generated property")
    if type_id in {"App::PropertyString", "App::PropertyEnumeration"}:
        if not isinstance(value, str) or len(value) > 16384:
            _error(
                "The isolated CAM path returned an invalid text property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        return value
    if type_id in {
        "App::PropertyBoolList",
        "App::PropertyFloatList",
        "App::PropertyIntegerList",
        "App::PropertyStringList",
    }:
        if not isinstance(value, list) or len(value) > 4096:
            _error(
                "The isolated CAM path returned an invalid list property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        scalar_type = {
            "App::PropertyBoolList": "App::PropertyBool",
            "App::PropertyFloatList": "App::PropertyFloat",
            "App::PropertyIntegerList": "App::PropertyInteger",
            "App::PropertyStringList": "App::PropertyString",
        }[type_id]
        return [_property_assignment(scalar_type, item) for item in value]
    _error(
        "The isolated CAM path returned an unsupported property value.",
        "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
    )
    raise AssertionError("unreachable")


def _adopt_generation_properties(
    operation: Any,
    artifact: Mapping[str, Any],
) -> None:
    raw = artifact.get("generation_property_changes")
    if not isinstance(raw, list) or len(raw) > 128:
        _error(
            "The isolated CAM path returned invalid generated properties.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    seen = set()
    expression_roots = {
        str(path).lstrip(".").split(".", 1)[0].split("[", 1)[0]
        for path, _expression in tuple(getattr(operation, "ExpressionEngine", ()) or ())
    }
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"name", "type", "value"}:
            _error(
                "The isolated CAM path returned a malformed generated property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        name = str(item["name"])
        type_id = str(item["type"])
        if (
            not name
            or name in seen
            or name in expression_roots
            or name not in tuple(getattr(operation, "PropertiesList", ()) or ())
            or type_id not in _GENERATION_VALUE_TYPES
            or str(operation.getTypeIdOfProperty(name) or "") != type_id
        ):
            _error(
                "The isolated CAM path returned an unsupported generated property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        seen.add(name)
        try:
            setattr(operation, name, _property_assignment(type_id, item["value"]))
        except Exception as exc:
            raise NativeManufactureError(
                f"The generated CAM property {name!r} could not be published.",
                error_code="NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            ) from exc


def apply_generated_operation_path(
    document: Any,
    draft: NativeMutationDraft,
    prepared: PreparedOperationGeneration,
) -> NativeMutationDraft:
    """Attach authenticated output without recomputing the expensive operation."""

    if not isinstance(draft, NativeMutationDraft) or not isinstance(
        prepared, PreparedOperationGeneration
    ):
        raise TypeError("CAM path publication requires exact prepared values")
    operation = draft.value["operation"]
    artifact = prepared.artifact
    operation.Active = False
    if document.recompute((operation,), True, True) is False:
        _error(
            "The live CAM operation could not resolve its setup expressions.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
        )
    operation.Active = True
    operation.Path = _path_from_artifact(artifact)
    cycle_time = artifact.get("cycle_time")
    if not isinstance(cycle_time, str) or len(cycle_time) > 32:
        _error(
            "The isolated CAM path returned an invalid cycle time.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    if hasattr(operation, "CycleTime"):
        operation.CycleTime = cycle_time
    derived_properties = artifact.get("derived_properties")
    if not isinstance(derived_properties, Mapping):
        _error(
            "The isolated CAM path returned invalid derived properties.",
            "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
        )
    for name, value in derived_properties.items():
        if name not in {
            "OpToolDiameter",
            "OpStartDepth",
            "OpFinalDepth",
            "OpStockZMin",
            "OpStockZMax",
        } or not hasattr(operation, name):
            _error(
                "The isolated CAM path returned an unsupported derived property.",
                "NATIVE_MANUFACTURE_PATH_WORKER_INVALID",
            )
        setattr(operation, name, _finite(value, name))
    _adopt_generation_properties(operation, artifact)
    _adopt_diagnostics(operation, artifact)
    operation.purgeTouched()
    job = draft.value["prepared"].job
    return NativeMutationDraft(
        value=draft.value,
        recompute_targets=(job,),
        created=draft.created,
        changed=draft.changed,
        deleted=draft.deleted,
        replaced=draft.replaced,
        after_recompute=draft.after_recompute,
    )


def start_background_operation_mutation(
    context: Any,
    *,
    request: Mapping[str, Any],
    ticket: Any,
    transaction_name: str,
    mutate: Callable[[Any], NativeMutationDraft],
    verify: Callable[[Any, NativeMutationDraft], Mapping[str, Any]],
    **options: Any,
) -> Mapping[str, Any]:
    """Schedule exact path work and retain the established Native mutation contract."""

    if options:
        raise TypeError("Unexpected CAM path mutation options")
    manager = context.background_manager
    dispatcher = context.document_thread_dispatch
    if manager is None or dispatcher is None:
        raise NativeManufactureError(
            "Background CAM path generation is unavailable in this session.",
            error_code="NATIVE_MANUFACTURE_PATH_BACKGROUND_UNAVAILABLE",
        )
    captured = capture_operation_generation(
        context.document,
        context.document_uid,
        request,
    )
    workspace = create_operation_generation_workspace()

    def prepare(cancelled: Any, progress: Any) -> PreparedOperationGeneration:
        progress(3, "Capturing exact CAM setup")
        materialized = dispatcher(
            lambda: materialize_operation_generation(
                context.document,
                captured,
                workspace,
            )
        )
        progress(5, "Authenticating exact CAM setup")
        frozen = freeze_operation_generation(materialized)
        return generate_operation_path(
            frozen,
            cancelled=cancelled,
            progress=progress,
        )

    def validate() -> None:
        context.guard()
        if not _captured_state_is_current(context.document, captured):
            _error(
                "The exact CAM setup changed during path generation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )

    def commit(generated: PreparedOperationGeneration) -> Mapping[str, Any]:
        from VibeCADNativeManufactureOperationRuntime import (
            NativeManufactureOperationRuntime,
        )

        def publish(
            runtime_context: Any,
            *,
            request: Mapping[str, Any],
            ticket: Any,
            transaction_name: str,
            mutate: Callable[[Any], NativeMutationDraft],
            verify: Callable[[Any, NativeMutationDraft], Mapping[str, Any]],
            **publish_options: Any,
        ) -> Mapping[str, Any]:
            if publish_options or dict(request) != dict(captured.request):
                raise TypeError("Unexpected CAM path publication options")
            return run_immediate_mutation(
                runtime_context,
                ticket=ticket,
                transaction_name=transaction_name,
                mutate=lambda document: apply_generated_operation_path(
                    document,
                    mutate(document),
                    generated,
                ),
                verify=verify,
            )

        commit_ticket = replace(
            ticket,
            expected_revision=context.state.current_revision(context.document_uid),
        )
        return NativeManufactureOperationRuntime(
            context,
            mutation_executor=publish,
        ).mutate_operation(request, ticket=commit_ticket)

    def cleanup(_prepared: Any) -> None:
        workspace.cleanup()
        context.state.cancel_mutation(ticket)

    try:
        snapshot = manager.submit(
            document_uid=context.document_uid,
            capability_name=f"manufacture.path.{request['operation']}",
            prepare=prepare,
            validate_before_commit=validate,
            commit=commit,
            dispatch_to_document_thread=dispatcher,
            finalize_message="Committing generated CAM path",
            cleanup=cleanup,
            changes_document=True,
            resource_scope=f"manufacture:{captured.job_name}",
        )
    except NativeBackgroundError as exc:
        workspace.cleanup()
        context.state.cancel_mutation(ticket)
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_MANUFACTURE_PATH_QUEUE_FAILED",
        ) from exc
    except Exception:
        workspace.cleanup()
        context.state.cancel_mutation(ticket)
        raise
    return {
        "job": {
            "job_id": str(snapshot.job_id),
            "capability": str(snapshot.capability_name),
            "resource_scope": str(snapshot.resource_scope),
            "phase": str(snapshot.phase),
            "progress_percent": int(snapshot.progress_percent),
            "progress_message": str(snapshot.progress_message),
            "terminal": bool(snapshot.terminal),
        },
        "next": {
            "tool": "native.job",
            "operation": "status",
            "job_id": str(snapshot.job_id),
            "poll_after_seconds": 5,
        },
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    number = float(getattr(value, "Value", value))
    if not math.isfinite(number):
        raise ValueError("non-finite CAM command value")
    return number


def operation_artifact(
    operation: Any,
    *,
    configuration_before_generation: Mapping[str, Any],
) -> dict[str, Any]:
    commands = []
    for command in tuple(operation.Path.Commands):
        commands.append(
            {
                "name": str(command.Name),
                "parameters": {
                    str(name): _json_value(value)
                    for name, value in dict(command.Parameters).items()
                },
                "annotations": {
                    str(name): _json_value(value)
                    for name, value in dict(command.Annotations).items()
                },
            }
        )
    center = operation.Path.Center
    configuration_after_generation = persistent_configuration_state(operation)
    derived = {}
    for name in (
        "OpToolDiameter",
        "OpStartDepth",
        "OpFinalDepth",
        "OpStockZMin",
        "OpStockZMax",
    ):
        if hasattr(operation, name):
            derived[name] = _json_value(getattr(operation, name))
    return {
        "schema": "vibecad-cam-path-result-v1",
        "ok": True,
        "commands": commands,
        "center_mm": [float(center.x), float(center.y), float(center.z)],
        "cycle_time": str(getattr(operation, "CycleTime", "00:00:00")),
        "derived_properties": derived,
        "generation_property_changes": _generation_property_changes(
            configuration_before_generation,
            configuration_after_generation,
        ),
        "generation_diagnostics": _json_value(
            operation.Proxy.getGenerationDiagnostics(operation)
        ),
    }


def run_child_request(request_path: Path) -> None:
    """Execute one fixed request inside FreeCADCmd."""

    result_path: Path | None = None
    document = None
    preference_group = None
    geometry_tolerance_present = False
    prior_geometry_tolerance = 0.01
    try:
        request_file = _freeze_file(request_path, MAX_REQUEST_BYTES)
        request = json.loads(request_file.path.read_text(encoding="utf-8"))
        expected = {
            "schema",
            "document_uid",
            "request",
            "runtime_preferences",
            "snapshot",
            "snapshot_bytes",
            "snapshot_sha256",
            "result",
        }
        if not isinstance(request, Mapping) or set(request) != expected:
            raise ValueError("invalid CAM path request")
        if request.get("schema") != "vibecad-cam-path-request-v1":
            raise ValueError("unsupported CAM path request")
        runtime_preferences = request["runtime_preferences"]
        if not isinstance(runtime_preferences, Mapping) or set(
            runtime_preferences
        ) != {"geometry_tolerance_mm"}:
            raise ValueError("invalid CAM path runtime preferences")
        geometry_tolerance = runtime_preferences["geometry_tolerance_mm"]
        if (
            isinstance(geometry_tolerance, bool)
            or not isinstance(geometry_tolerance, (int, float))
            or not math.isfinite(float(geometry_tolerance))
            or float(geometry_tolerance) <= 0.0
        ):
            raise ValueError("invalid CAM path geometry tolerance")
        root = request_file.path.parent.resolve()
        snapshot = (root / str(request["snapshot"])).resolve()
        result_path = (root / str(request["result"])).resolve()
        if snapshot.parent != root or result_path.parent != root:
            raise ValueError("CAM path request escaped its private workspace")
        frozen_snapshot = _freeze_file(snapshot, MAX_SNAPSHOT_BYTES)
        if (
            frozen_snapshot.size != int(request["snapshot_bytes"])
            or frozen_snapshot.sha256 != str(request["snapshot_sha256"])
        ):
            raise ValueError("CAM path snapshot authentication failed")
        import FreeCAD as App
        import Path as CamPath
        from VibeCADNativeManufactureOperationRuntime import (
            NativeManufactureOperationRuntime,
        )
        from VibeCADNativeRuntimeContext import NativeRuntimeContext
        from VibeCADNativeState import NativeDocumentStateStore
        from VibeCADNativeTargets import document_uid
        from VibeCADNativeUndo import NativeAssistantUndoLedger

        preference_group = CamPath.Preferences.preferences()
        geometry_tolerance_present = "GeometryTolerance" in tuple(
            preference_group.GetFloats() or ()
        )
        prior_geometry_tolerance = float(
            preference_group.GetFloat("GeometryTolerance", 0.01)
        )
        preference_group.SetFloat("GeometryTolerance", float(geometry_tolerance))

        document = App.openDocument(str(frozen_snapshot.path))
        if document.recompute(None, True, True) is False:
            raise RuntimeError("isolated CAM snapshot stabilization failed")
        state = NativeDocumentStateStore()
        uid = document_uid(document)
        if uid != str(request["document_uid"]):
            raise ValueError("CAM path snapshot document identity changed")
        state.ensure_document(uid)
        ledger = NativeAssistantUndoLedger()

        def execute_isolated(
            _context: Any,
            *,
            request: Mapping[str, Any],
            transaction_name: str,
            mutate: Callable[[Any], NativeMutationDraft],
            verify: Callable[[Any, NativeMutationDraft], Mapping[str, Any]],
            **_options: Any,
        ) -> Mapping[str, Any]:
            document.openTransaction(transaction_name)
            try:
                draft = mutate(document)
                configuration_before_generation = persistent_configuration_state(
                    draft.value["operation"]
                )
                if document.recompute(list(draft.recompute_targets), True, True) is False:
                    raise RuntimeError("isolated CAM path recompute failed")
                verify(document, draft)
                return operation_artifact(
                    draft.value["operation"],
                    configuration_before_generation=configuration_before_generation,
                )
            finally:
                document.abortTransaction()

        context = NativeRuntimeContext(
            service=None,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: document,
            active_surface_id=lambda: "manufacture",
            edit_or_task_active=lambda: False,
        )
        runtime = NativeManufactureOperationRuntime(
            context,
            mutation_executor=execute_isolated,
        )
        operation_request = dict(request["request"])
        ticket = state.begin_call(uid, "manufacture.operation.worker")
        artifact = runtime.mutate_operation(operation_request, ticket=ticket)
        state.cancel_mutation(ticket)
    except Exception as exc:
        failure = getattr(exc, "failure", None)
        try:
            failure_value = failure() if callable(failure) else {}
        except Exception:
            failure_value = {}
        artifact = {
            "schema": "vibecad-cam-path-result-v1",
            "ok": False,
            "error_code": str(
                getattr(exc, "error_code", "")
                or "NATIVE_MANUFACTURE_PATH_WORKER_FAILED"
            ),
            "message": str(exc)[:320] or "The isolated CAM path worker failed.",
        }
        if isinstance(failure_value, Mapping) and isinstance(
            failure_value.get("repair"), Mapping
        ):
            artifact["repair"] = dict(failure_value["repair"])
    finally:
        if preference_group is not None:
            try:
                if geometry_tolerance_present:
                    preference_group.SetFloat(
                        "GeometryTolerance",
                        prior_geometry_tolerance,
                    )
                else:
                    preference_group.RemFloat("GeometryTolerance")
            except Exception:
                pass
        if document is not None:
            try:
                import FreeCAD as App

                App.closeDocument(str(document.Name))
            except Exception:
                pass
    if result_path is None:
        raise RuntimeError("The isolated CAM request has no safe result path")
    encoded = json.dumps(
        artifact,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        encoded = json.dumps(
            {
                "schema": "vibecad-cam-path-result-v1",
                "ok": False,
                "error_code": "NATIVE_MANUFACTURE_PATH_WORKER_LIMIT",
                "message": "The isolated CAM path exceeds its result bound.",
            },
            separators=(",", ":"),
        ).encode("utf-8")
    _write_private(result_path, encoded)


__all__ = [
    "apply_generated_operation_path",
    "capture_operation_generation",
    "create_operation_generation_workspace",
    "freeze_operation_generation",
    "generate_operation_path",
    "materialize_operation_generation",
    "operation_artifact",
    "run_child_request",
    "start_background_operation_mutation",
]
