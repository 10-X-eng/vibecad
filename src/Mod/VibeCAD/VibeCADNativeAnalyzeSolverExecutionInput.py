# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private exact-document input for isolated FEM solver execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    CapturedSolverExecutionRequest,
    validate_captured_solver_execution,
)
from VibeCADNativeDrawingProjectionInput import (
    FrozenFile,
    freeze_regular_file,
    resolve_freecadcmd,
)

ANALYZE_SOLVER_EXECUTION_PROTOCOL = "vibecad-native-analyze-solver-execution-v1"
MAX_SOLVER_SNAPSHOT_BYTES = 16 * 1024 * 1024 * 1024
MAX_SOLVER_REQUEST_BYTES = 256 * 1024
MAX_SOLVER_CHILD_BYTES = 1024 * 1024


@dataclass(slots=True)
class SolverExecutionWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class MaterializedSolverExecutionSnapshot:
    workspace: SolverExecutionWorkspace = field(repr=False, compare=False)
    captured: CapturedSolverExecutionRequest = field(repr=False, compare=False)
    snapshot_path: Path = field(repr=False, compare=False)
    solver_name: str
    solver_id: int
    solver_type_id: str


@dataclass(frozen=True, slots=True)
class FrozenSolverExecution:
    workspace: SolverExecutionWorkspace = field(repr=False, compare=False)
    captured: CapturedSolverExecutionRequest = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request: FrozenFile = field(repr=False, compare=False)
    request_sha256: str
    solver_name: str


def _error(message: str, code: str) -> None:
    raise NativeAnalyzeError(message, error_code=code)


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_solver_execution_workspace() -> SolverExecutionWorkspace:
    """Own every snapshot, case, log, and result for one solver job."""

    temporary = tempfile.TemporaryDirectory(
        prefix="vibecad-native-analyze-solver-execution-"
    )
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = freeze_regular_file(
            Path(__file__)
            .with_name("VibeCADNativeAnalyzeSolverExecutionChild.py")
            .resolve(),
            maximum=MAX_SOLVER_CHILD_BYTES,
        )
        return SolverExecutionWorkspace(
            temporary=temporary,
            path=root,
            freecadcmd=resolve_freecadcmd(),
            child=child,
        )
    except Exception as exc:
        temporary.cleanup()
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            "The fixed windowless FEM worker runtime is unavailable.",
            error_code="NATIVE_ANALYZE_SOLVER_BACKGROUND_UNAVAILABLE",
        ) from exc


def materialize_solver_execution_snapshot(
    document: Any,
    captured: CapturedSolverExecutionRequest,
    workspace: SolverExecutionWorkspace,
) -> MaterializedSolverExecutionSnapshot:
    """Copy exact live state on the document thread, without case generation."""

    if not isinstance(workspace, SolverExecutionWorkspace):
        raise TypeError("workspace must be a SolverExecutionWorkspace")
    validate_captured_solver_execution(document, captured)
    snapshot_path = workspace.path / "document.FCStd"
    try:
        result = document.saveCopy(str(snapshot_path))
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact FEM document could not be copied for detached execution.",
            error_code="NATIVE_ANALYZE_SOLVER_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot_path.is_file():
        _error(
            "The exact FEM document snapshot was not created.",
            "NATIVE_ANALYZE_SOLVER_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot_path, 0o600)
    validate_captured_solver_execution(document, captured)
    solver = captured.target.solver
    return MaterializedSolverExecutionSnapshot(
        workspace=workspace,
        captured=captured,
        snapshot_path=snapshot_path,
        solver_name=str(solver.Name),
        solver_id=int(solver.ID),
        solver_type_id=str(solver.TypeId),
    )


def freeze_solver_execution_snapshot(
    materialized: MaterializedSolverExecutionSnapshot,
) -> FrozenSolverExecution:
    """Authenticate the snapshot and write bounded child metadata off-thread."""

    if not isinstance(materialized, MaterializedSolverExecutionSnapshot):
        raise TypeError("materialized must be a MaterializedSolverExecutionSnapshot")
    workspace = materialized.workspace
    captured = materialized.captured
    try:
        snapshot = freeze_regular_file(
            materialized.snapshot_path,
            maximum=MAX_SOLVER_SNAPSHOT_BYTES,
        )
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact FEM document snapshot could not be authenticated.",
            error_code="NATIVE_ANALYZE_SOLVER_SNAPSHOT_FAILED",
        ) from exc
    preferences = [list(value) for value in captured.runtime_preferences]
    request_value = {
        "protocol": ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        "workspace": str(workspace.path),
        "snapshot": "document.FCStd",
        "snapshot_bytes": snapshot.size_bytes,
        "snapshot_sha256": snapshot.sha256,
        "solver": {
            "object_name": materialized.solver_name,
            "object_id": materialized.solver_id,
            "type_id": materialized.solver_type_id,
            "kind": str(captured.target.kind),
            "state_sha256": str(captured.target.expected_state_sha256),
        },
        "timeout_seconds": int(captured.timeout_seconds),
        "keep_results": bool(captured.keep_results),
        "runtime_preferences": preferences,
        "case": "case",
        "result": "result.json",
    }
    encoded = json.dumps(
        request_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_SOLVER_REQUEST_BYTES:
        _error(
            "The exact FEM solver request exceeds its metadata bound.",
            "NATIVE_ANALYZE_SOLVER_INPUT_LIMIT",
        )
    request_path = workspace.path / "request.json"
    _write_private(request_path, encoded)
    try:
        request = freeze_regular_file(
            request_path,
            maximum=MAX_SOLVER_REQUEST_BYTES,
        )
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact FEM solver request could not be authenticated.",
            error_code="NATIVE_ANALYZE_SOLVER_SNAPSHOT_FAILED",
        ) from exc
    if request.sha256 != hashlib.sha256(encoded).hexdigest():
        _error(
            "The exact FEM solver request changed while it was written.",
            "NATIVE_ANALYZE_SOLVER_SNAPSHOT_FAILED",
        )
    return FrozenSolverExecution(
        workspace=workspace,
        captured=captured,
        snapshot=snapshot,
        request=request,
        request_sha256=request.sha256,
        solver_name=materialized.solver_name,
    )


__all__ = [
    "ANALYZE_SOLVER_EXECUTION_PROTOCOL",
    "FrozenSolverExecution",
    "MaterializedSolverExecutionSnapshot",
    "SolverExecutionWorkspace",
    "create_solver_execution_workspace",
    "freeze_solver_execution_snapshot",
    "materialize_solver_execution_snapshot",
]
