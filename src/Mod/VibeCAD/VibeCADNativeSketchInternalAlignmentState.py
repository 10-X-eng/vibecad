# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and postconditions for durable Sketch internal geometry."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import require_healthy_external_records
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInternalAlignmentTarget import (
    LABEL,
    InternalAlignmentTarget,
    SketchInternalAlignmentSpec,
)
from VibeCADNativeSketchMutationState import expected_expression_records
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    frozen_transform_state,
)


_ROLE_KIND = {
    "EllipseMajorDiameter": "line",
    "EllipseMinorDiameter": "line",
    "EllipseFocus1": "point",
    "EllipseFocus2": "point",
    "HyperbolaMajor": "line",
    "HyperbolaMinor": "line",
    "HyperbolaFocus": "point",
    "ParabolaFocus": "point",
    "ParabolaFocalAxis": "line",
    "BSplineControlPoint": "circle",
    "BSplineKnotPoint": "point",
}
_CONIC_ROLES = {
    "Part::GeomEllipse": (
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ),
    "Part::GeomArcOfEllipse": (
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ),
    "Part::GeomArcOfHyperbola": (
        "HyperbolaMajor",
        "HyperbolaMinor",
        "HyperbolaFocus",
    ),
    "Part::GeomArcOfParabola": (
        "ParabolaFocus",
        "ParabolaFocalAxis",
    ),
}


@dataclass(frozen=True, slots=True)
class InternalHelperBinding:
    role: str
    alignment_index: int
    geometry_index: int
    geometry_tag: str
    constraint_index: int
    constraint_tag: str

    @property
    def key(self) -> tuple[str, int]:
        return self.role, self.alignment_index


@dataclass(frozen=True, slots=True)
class InternalAlignmentPlan:
    requested_index: int
    root_tag: str
    geometry_kind: str
    complete_keys: tuple[tuple[str, int], ...]
    before_helpers: tuple[InternalHelperBinding, ...]
    final_keys: tuple[tuple[str, int], ...]
    action: str


@dataclass(frozen=True, slots=True)
class PreparedSketchInternalAlignment:
    target: PreparedActiveSketchTarget
    spec: SketchInternalAlignmentSpec
    state: FrozenSketchTransformState
    plans: tuple[InternalAlignmentPlan, ...]


def _records(values: tuple[str, ...], label: str) -> tuple[dict[str, Any], ...]:
    result = []
    for encoded in values:
        try:
            record = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise NativeSketchError(f"{LABEL} found invalid {label} state.") from exc
        if not isinstance(record, dict):
            raise NativeSketchError(f"{LABEL} found invalid {label} state.")
        result.append(record)
    return tuple(result)


def _complete_keys(root: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    type_id = root.get("type_id")
    roles = _CONIC_ROLES.get(type_id)
    if roles is not None:
        return tuple((role, -1) for role in roles)
    if type_id != "Part::GeomBSplineCurve":
        raise NativeSketchError(
            f"{LABEL} requires an ellipse, conic arc, or B-spline edge."
        )
    poles = root.get("pole_count")
    knots = root.get("knot_count")
    if (
        type(poles) is not int
        or type(knots) is not int
        or not 2 <= poles <= 1_000_000
        or not 2 <= knots <= 1_000_000
    ):
        raise NativeSketchError(f"{LABEL} found invalid B-spline topology.")
    return tuple(
        [("BSplineControlPoint", index) for index in range(poles)]
        + [("BSplineKnotPoint", index) for index in range(knots)]
    )


def _references(record: Mapping[str, Any]) -> dict[int, int]:
    result = {}
    for value in record.get("references", []):
        if not isinstance(value, Mapping):
            continue
        slot = value.get("slot")
        geometry = value.get("geometry_index")
        if type(slot) is int and type(geometry) is int:
            result[slot] = geometry
    return result


def _involved_geometry(record: Mapping[str, Any]) -> set[int]:
    result = set(_references(record).values())
    for value in record.get("elements", []):
        if isinstance(value, Mapping) and type(value.get("geometry_index")) is int:
            result.add(value["geometry_index"])
    return result


def _bindings(
    sketch: Any,
    state: FrozenSketchTransformState,
    root_index: int,
    complete_keys: tuple[tuple[str, int], ...],
) -> tuple[InternalHelperBinding, ...]:
    geometry = _records(state.geometry_records, "geometry")
    constraints = _records(state.constraint_records, "constraint")
    raw_constraints = tuple(sketch.Constraints)
    if len(raw_constraints) != len(constraints):
        raise NativeSketchError(f"{LABEL} constraint state changed.")
    allowed = set(complete_keys)
    used_helpers: set[int] = set()
    used_keys: set[tuple[str, int]] = set()
    result = []
    for index, (raw, record) in enumerate(
        zip(raw_constraints, constraints, strict=True)
    ):
        if record.get("type") != "InternalAlignment":
            continue
        try:
            owner = int(raw.Second)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise NativeSketchError(
                f"{LABEL} found malformed alignment state."
            ) from exc
        if owner != root_index:
            continue
        try:
            helper_index = int(raw.First)
            alignment_index = int(raw.InternalAlignmentIndex)
            helper = geometry[helper_index]
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:
            raise NativeSketchError(f"{LABEL} found malformed helper state.") from exc
        role = str(helper.get("internal_type") or "")
        if not role.startswith("BSpline"):
            alignment_index = -1
        key = role, alignment_index
        refs = _references(record)
        if (
            key not in allowed
            or helper_index == root_index
            or helper_index in used_helpers
            or key in used_keys
            or refs.get(1) != helper_index
            or refs.get(2) != root_index
            or record.get("driving") is not True
            or record.get("active") is not True
            or record.get("virtual") is not False
            or helper.get("construction") is not True
            or bool(helper.get("blocked"))
            or helper.get("kind") != _ROLE_KIND[role]
        ):
            raise NativeSketchError(f"{LABEL} found malformed helper alignment.")
        used_helpers.add(helper_index)
        used_keys.add(key)
        result.append(
            InternalHelperBinding(
                role,
                alignment_index,
                helper_index,
                state.geometry_tags[helper_index],
                index,
                state.constraint_tags[index],
            )
        )
    return tuple(sorted(result, key=lambda item: item.key))


def _retained_conic_keys(
    root_type: str,
    helpers: tuple[InternalHelperBinding, ...],
    constraints: tuple[dict[str, Any], ...],
) -> tuple[tuple[str, int], ...]:
    priority = list(_CONIC_ROLES[root_type])
    if root_type == "Part::GeomArcOfParabola":
        priority = ["ParabolaFocalAxis", "ParabolaFocus"]
    by_role = {item.role: item for item in helpers}
    counts = {item.key: 0 for item in helpers}
    for record in constraints:
        involved = _involved_geometry(record)
        for role in priority:
            binding = by_role.get(role)
            if binding is not None and binding.geometry_index in involved:
                counts[binding.key] += 1
                break
    return tuple(sorted(key for key, count in counts.items() if count >= 2))


def _retained_bspline_keys(
    helpers: tuple[InternalHelperBinding, ...],
    constraints: tuple[dict[str, Any], ...],
) -> tuple[tuple[str, int], ...]:
    poles = {
        item.geometry_index: item.key
        for item in helpers
        if item.role == "BSplineControlPoint"
    }
    knots = {
        item.geometry_index: item.key
        for item in helpers
        if item.role == "BSplineKnotPoint"
    }
    counts = {item.key: 0 for item in helpers}
    for record in constraints:
        constraint_type = record.get("type")
        refs = _references(record)
        first = refs.get(1)
        second = refs.get(2)
        first_is_pole = first in poles
        second_is_pole = second in poles
        if constraint_type not in {"InternalAlignment", "Weight"}:
            if not (constraint_type == "Equal" and first_is_pole == second_is_pole):
                if first_is_pole:
                    counts[poles[first]] += 1
                if second_is_pole:
                    counts[poles[second]] += 1
        involved = _involved_geometry(record)
        for index, key in knots.items():
            if index in involved:
                counts[key] += 1
    return tuple(
        sorted(
            key
            for key, count in counts.items()
            if count >= (1 if key[0] == "BSplineControlPoint" else 2)
        )
    )


def _require_safe_deletions(
    state: FrozenSketchTransformState,
    helpers: tuple[InternalHelperBinding, ...],
    retained_keys: tuple[tuple[str, int], ...],
) -> None:
    deleted = {
        item.geometry_index for item in helpers if item.key not in set(retained_keys)
    }
    if not deleted:
        raise NativeSketchError(f"{LABEL} found no unused helpers to hide.")
    constraints = _records(state.constraint_records, "constraint")
    for expression in state.expression_records:
        index = expression.constraint_index
        if index is not None and _involved_geometry(constraints[index]) & deleted:
            raise NativeSketchError(
                f"{LABEL} will not discard an expression on internal geometry."
            )
    for record in constraints:
        if record.get("name") and _involved_geometry(record) & deleted:
            raise NativeSketchError(
                f"{LABEL} will not discard a named internal-geometry constraint."
            )


def _plan(
    sketch: Any,
    state: FrozenSketchTransformState,
    target: InternalAlignmentTarget,
) -> InternalAlignmentPlan:
    geometry = _records(state.geometry_records, "geometry")
    try:
        root = geometry[target.geometry_index]
        root_tag = state.geometry_tags[target.geometry_index]
    except IndexError as exc:
        raise NativeSketchError(f"{LABEL} geometry index is stale.") from exc
    if root.get("internal_type"):
        raise NativeSketchError(f"{LABEL} requires owner curves, not helper geometry.")
    complete = _complete_keys(root)
    helpers = _bindings(sketch, state, target.geometry_index, complete)
    if len(helpers) != target.expected_internal_geometry_count:
        raise NativeSketchError(
            f"{LABEL} helper count changed; read the current Sketch and retry."
        )
    current = tuple(item.key for item in helpers)
    if not set(current) <= set(complete):
        raise NativeSketchError(f"{LABEL} found unexpected helper roles.")
    if set(current) == set(complete):
        constraints = _records(state.constraint_records, "constraint")
        retained = (
            _retained_bspline_keys(helpers, constraints)
            if root.get("type_id") == "Part::GeomBSplineCurve"
            else _retained_conic_keys(str(root.get("type_id")), helpers, constraints)
        )
        _require_safe_deletions(state, helpers, retained)
        action = "hide_unused"
        final = retained
    else:
        action = "expose_missing"
        final = complete
    return InternalAlignmentPlan(
        target.geometry_index,
        root_tag,
        str(root.get("kind") or "unknown"),
        complete,
        helpers,
        final,
        action,
    )


def preflight_sketch_internal_alignment(
    context: NativeRuntimeContext,
    spec: SketchInternalAlignmentSpec,
) -> PreparedSketchInternalAlignment:
    if not isinstance(spec, SketchInternalAlignmentSpec):
        raise TypeError("spec must be a SketchInternalAlignmentSpec")
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=LABEL,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(
            f"{LABEL} external state changed; read the current Sketch and retry."
        )
    if any(state.solver_issues):
        raise NativeSketchError(f"{LABEL} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=LABEL)
    plans = tuple(_plan(target.sketch, state, item) for item in spec.targets)
    return PreparedSketchInternalAlignment(target, spec, state, plans)


def require_internal_alignment_unchanged(
    document: Any,
    prepared: PreparedSketchInternalAlignment,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    state = frozen_transform_state(
        sketch,
        prepared.spec.target.expected_geometry_count,
        prepared.spec.target.expected_constraint_count,
        label=LABEL,
    )
    if state != prepared.state:
        raise NativeSketchError(f"{LABEL} target changed after preflight.")
    return sketch


def current_geometry_index(sketch: Any, root_tag: str) -> int:
    try:
        values = tuple(sketch.Geometry)
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} geometry identities are unavailable."
        ) from exc
    matches = [
        index
        for index, geometry in enumerate(values)
        if str(getattr(geometry, "Tag", "") or "") == root_tag
    ]
    if len(matches) != 1:
        raise NativeSketchError(f"{LABEL} owner identity changed during execution.")
    return matches[0]


def capture_final_state(sketch: Any) -> FrozenSketchTransformState:
    return frozen_transform_state(
        sketch,
        int(sketch.GeometryCount),
        int(sketch.ConstraintCount),
        label=LABEL,
    )


def identity_mapping(
    before: tuple[str, ...],
    after: tuple[str, ...],
) -> tuple[dict[int, int], set[int], set[int]]:
    before_by_tag = {tag: index for index, tag in enumerate(before)}
    after_by_tag = {tag: index for index, tag in enumerate(after)}
    if len(before_by_tag) != len(before) or len(after_by_tag) != len(after):
        raise NativeSketchError(f"{LABEL} found duplicate durable identities.")
    shared = set(before_by_tag) & set(after_by_tag)
    mapping = {before_by_tag[tag]: after_by_tag[tag] for tag in shared}
    deleted = {before_by_tag[tag] for tag in set(before_by_tag) - shared}
    created = {after_by_tag[tag] for tag in set(after_by_tag) - shared}
    return mapping, deleted, created


def expected_final_expressions(
    before: FrozenSketchTransformState,
    constraint_mapping: Mapping[int, int],
) -> tuple[Any, ...]:
    return expected_expression_records(before.expression_records, constraint_mapping)


def final_bindings(
    sketch: Any,
    state: FrozenSketchTransformState,
    root_index: int,
    complete_keys: tuple[tuple[str, int], ...],
) -> tuple[InternalHelperBinding, ...]:
    return _bindings(sketch, state, root_index, complete_keys)


def constraint_geometry(record: Mapping[str, Any]) -> set[int]:
    return _involved_geometry(record)


def decoded_records(values: tuple[str, ...], label: str) -> tuple[dict[str, Any], ...]:
    return _records(values, label)
