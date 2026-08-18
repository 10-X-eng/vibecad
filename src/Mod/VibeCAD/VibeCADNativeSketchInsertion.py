# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact-state proof for one appended Native Sketch geometry item."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_geometry_records,
    serialize_sketch_diagnostics,
    serialize_sketch_geometry,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_reference


@dataclass(frozen=True, slots=True)
class PreparedSketchInsertion:
    target: PreparedActiveSketchTarget
    existing_geometry_sha256: str
    existing_constraints_sha256: str


def _records_digest(records: Any) -> str:
    digest = hashlib.sha256()
    for record in records:
        encoded = json.dumps(
            record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _geometry_digest(sketch: Any, count: int) -> str:
    return _records_digest(iter_sketch_geometry_records(sketch, count))


def _constraint_digest(sketch: Any, count: int) -> str:
    return _records_digest(iter_sketch_constraint_records(sketch, count))


def _counts(sketch: Any) -> tuple[int, int]:
    try:
        return int(sketch.GeometryCount), int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError("The active Sketch counts became unavailable.") from exc


def preflight_sketch_insertion(
    context: NativeRuntimeContext,
    spec: ActiveSketchTargetSpec,
) -> PreparedSketchInsertion:
    target = preflight_active_sketch(context, spec)
    return PreparedSketchInsertion(
        target,
        _geometry_digest(target.sketch, spec.expected_geometry_count),
        _constraint_digest(target.sketch, spec.expected_constraint_count),
    )


def require_unchanged_sketch_insertion(
    document: Any,
    prepared: PreparedSketchInsertion,
    *,
    stage: str,
) -> Any:
    if not isinstance(prepared, PreparedSketchInsertion):
        raise TypeError("prepared must be a PreparedSketchInsertion")
    sketch = require_prepared_active_sketch(document, prepared.target)
    geometry_count = prepared.target.spec.expected_geometry_count
    constraint_count = prepared.target.spec.expected_constraint_count
    if _counts(sketch) != (geometry_count, constraint_count):
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    if _geometry_digest(sketch, geometry_count) != prepared.existing_geometry_sha256:
        raise NativeSketchError(f"Existing Sketch geometry changed {stage}.")
    if _constraint_digest(sketch, constraint_count) != (
        prepared.existing_constraints_sha256
    ):
        raise NativeSketchError(f"Existing Sketch constraints changed {stage}.")
    return sketch


def verify_sketch_insertion(
    document: Any,
    prepared: PreparedSketchInsertion,
    geometry_index: int,
) -> tuple[Any, dict[str, Any]]:
    if geometry_index != prepared.target.spec.expected_geometry_count:
        raise NativeSketchError("Sketch geometry insertion returned an unexpected index.")
    sketch = verify_sketch_append(
        document,
        prepared,
        geometry_added=1,
        constraints_added=0,
    )
    return sketch, serialize_sketch_geometry(sketch, geometry_index)


def verify_sketch_append(
    document: Any,
    prepared: PreparedSketchInsertion,
    *,
    geometry_added: int,
    constraints_added: int,
) -> Any:
    if (
        type(geometry_added) is not int
        or type(constraints_added) is not int
        or geometry_added < 1
        or constraints_added < 0
    ):
        raise ValueError("Sketch append counts are invalid")
    sketch = require_prepared_active_sketch(document, prepared.target)
    before_geometry = prepared.target.spec.expected_geometry_count
    before_constraints = prepared.target.spec.expected_constraint_count
    if _counts(sketch) != (
        before_geometry + geometry_added,
        before_constraints + constraints_added,
    ):
        raise NativeSketchError(
            "Sketch geometry insertion changed unexpected geometry or constraints."
        )
    if _geometry_digest(sketch, before_geometry) != prepared.existing_geometry_sha256:
        raise NativeSketchError("Sketch geometry insertion changed pre-existing geometry.")
    if _constraint_digest(sketch, before_constraints) != (
        prepared.existing_constraints_sha256
    ):
        raise NativeSketchError("Sketch geometry insertion changed constraints.")
    return sketch


def sketch_insertion_result(
    sketch: Any,
    geometry: dict[str, Any],
) -> dict[str, Any]:
    """Return one concise verified geometry-insertion result."""

    return sketch_geometry_result(sketch, {"geometry": geometry})


def sketch_geometry_result(
    sketch: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return concise verified geometry payload plus current Sketch state."""

    if not isinstance(payload, Mapping) or not payload or "sketch" in payload:
        raise TypeError("Sketch geometry result payload is invalid")
    try:
        valid = bool(sketch.isValid())
        malformed = tuple(int(value) for value in sketch.MalformedConstraints)
        geometry_count = int(sketch.GeometryCount)
        constraint_count = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError("Sketch geometry postcondition is unavailable.") from exc
    if not valid or malformed:
        raise NativeSketchError("Sketch geometry left the active Sketch invalid.")
    return {
        "sketch": object_reference(sketch),
        **dict(payload),
        "geometry_count": geometry_count,
        "constraint_count": constraint_count,
        **serialize_sketch_diagnostics(sketch),
    }


def sketch_geometry_refs(records: Any) -> list[dict[str, Any]]:
    """Return actionable identities without repeating serialized curve data."""

    result = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("Sketch geometry reference source is invalid")
        index = record.get("index")
        kind = record.get("kind")
        if type(index) is not int or not isinstance(kind, str) or not kind:
            raise TypeError("Sketch geometry reference source is incomplete")
        reference = {
            "geometry_index": index,
            "kind": kind,
            "construction": bool(record.get("construction")),
        }
        geometry_id = record.get("geometry_id")
        if type(geometry_id) is int and geometry_id >= 0:
            reference["geometry_id"] = geometry_id
        result.append(reference)
    return result


def sketch_constraint_refs(records: Any) -> list[dict[str, Any]]:
    """Return actionable constraint identities without repeating references."""

    result = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("Sketch constraint reference source is invalid")
        index = record.get("index")
        constraint_type = record.get("type")
        if type(index) is not int or not isinstance(constraint_type, str):
            raise TypeError("Sketch constraint reference source is incomplete")
        result.append(
            {"constraint_index": index, "type": constraint_type}
        )
    return result
