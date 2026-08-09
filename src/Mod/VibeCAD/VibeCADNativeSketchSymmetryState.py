# SPDX-License-Identifier: LGPL-2.1-or-later

"""Symmetry-specific reference and echo checks over exact transform state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import ISSUE_FIELDS
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchConstraintTargets import sketch_constraint_geometry
from VibeCADNativeSketchSymmetryTarget import LABEL, SketchSymmetrySpec
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    SketchTransformPlan,
    SketchTransformSnapshot,
    capture_transform_snapshot,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    verify_transform_state,
)


_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
        "external_reference_count",
        "external_references",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
        "input_geometry_indices",
        "reference_geometry_index",
        "reference_position",
        "source_mode",
        "deleted_originals",
        "constrained_symmetry",
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)
_LINE_TYPE = "Part::GeomLineSegment"

FrozenSymmetryState = FrozenSketchTransformState
SketchSymmetrySnapshot = SketchTransformSnapshot
SketchSymmetryPlan = SketchTransformPlan


def _reference_geometry(sketch: Any, index: int) -> Any:
    try:
        geometry = sketch_constraint_geometry(sketch, index)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} reference geometry is unavailable.") from exc
    if geometry is None:
        raise NativeSketchError(f"{LABEL} reference geometry is unavailable.")
    return geometry


def _validate_reference(snapshot: SketchSymmetrySnapshot) -> None:
    spec = snapshot.spec
    reference = spec.reference
    index = reference.geometry_index
    if index >= spec.target.expected_geometry_count:
        raise NativeSketchError(f"{LABEL} internal reference geometry is stale.")
    if index <= -3 and -index - 3 >= len(snapshot.state.external_geometry_records):
        raise NativeSketchError(f"{LABEL} external reference geometry is stale.")
    if index == -1:
        if reference.position not in {"whole", "start"}:
            raise NativeSketchError(
                f"{LABEL} index -1 is the horizontal axis at whole or origin at start."
            )
        return
    if index == -2:
        if reference.position != "whole":
            raise NativeSketchError(f"{LABEL} vertical axis must use whole position.")
        return
    sketch = snapshot.target.sketch
    geometry = _reference_geometry(sketch, index)
    if reference.position == "whole":
        if str(getattr(geometry, "TypeId", "") or "") != _LINE_TYPE:
            raise NativeSketchError(
                f"{LABEL} whole reference must be one exact straight line or axis."
            )
        return
    point = getattr(sketch, "getPoint", None)
    if not callable(point):
        raise NativeSketchError(f"{LABEL} point reference lookup is unavailable.")
    try:
        point(index, reference.position_code)
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} reference does not expose the requested exact point."
        ) from exc


def capture_symmetry_snapshot(
    context: NativeRuntimeContext,
    spec: SketchSymmetrySpec,
) -> SketchSymmetrySnapshot:
    if not isinstance(spec, SketchSymmetrySpec):
        raise TypeError("spec must be a SketchSymmetrySpec")
    snapshot = capture_transform_snapshot(context, spec, label=LABEL)
    _validate_reference(snapshot)
    return snapshot


def require_symmetry_snapshot_unchanged(
    document: Any,
    snapshot: SketchSymmetrySnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_symmetry_diagnostic(snapshot: SketchSymmetrySnapshot) -> None:
    require_pure_transform_diagnostic(snapshot)


def parse_symmetry_diagnostic(
    result: Any,
    snapshot: SketchSymmetrySnapshot,
) -> SketchSymmetryPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    spec = snapshot.spec
    indices = (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    )
    reference = spec.reference
    if (
        indices != spec.geometry_indices
        or type(result["reference_geometry_index"]) is not int
        or result["reference_geometry_index"] != reference.geometry_index
        or result["reference_position"] != reference.position
        or result["source_mode"] != spec.source_mode
        or type(result["deleted_originals"]) is not bool
        or result["deleted_originals"] is not (spec.source_mode == "delete")
        or type(result["constrained_symmetry"]) is not bool
        or result["constrained_symmetry"] is not (spec.source_mode == "constrain")
    ):
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    return parse_transform_diagnostic(result, snapshot)


def verify_symmetry_state(
    document: Any,
    snapshot: SketchSymmetrySnapshot,
    plan: SketchSymmetryPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan, receipt)
