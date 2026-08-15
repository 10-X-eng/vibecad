# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state, source guards, and postconditions for Sketch external geometry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintToggleState import (
    SketchExpressionRecord,
    sketch_expression_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchExternalTarget import SketchExternalSpec
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_reference, resolve_object


_EXTERNAL_KINDS = {
    0: "projection",
    1: "intersection",
    2: "projection_and_intersection",
}
_WHOLE_SOURCE_TYPES = (
    "Part::Datum",
    "Part::DatumLine",
    "Part::DatumPoint",
    "App::Plane",
    "App::Line",
    "App::Point",
)


@dataclass(frozen=True, slots=True)
class SketchExternalSnapshot:
    target: PreparedActiveSketchTarget
    spec: SketchExternalSpec
    source: Any
    source_token: str
    projection_configuration: str
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    external_reference_records: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    method = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(method(type_id)) if callable(method) else False
    except Exception:
        return False


def _source_token(source: Any) -> str:
    digest = hashlib.sha256()

    def add(value: Any) -> None:
        digest.update(repr(value).encode())

    def vector(value: Any):
        if value is None:
            return None
        result = []
        for lower, upper in (("x", "X"), ("y", "Y"), ("z", "Z")):
            raw = getattr(value, lower, getattr(value, upper, None))
            if raw is None:
                return None
            result.append(float(raw))
        return tuple(result)

    add((getattr(source, "Name", ""), getattr(source, "TypeId", "")))
    shape = getattr(source, "Shape", None)
    export = getattr(shape, "exportBrepToString", None)
    if callable(export):
        try:
            content = export()
            digest.update(
                content if isinstance(content, bytes) else str(content).encode()
            )
        except Exception:
            add("<shape-unavailable>")
    placement = getattr(source, "Placement", None)
    if placement is not None:
        rotation = getattr(placement, "Rotation", None)
        add(
            (
                "Placement",
                vector(getattr(placement, "Base", None)),
                tuple(float(value) for value in (getattr(rotation, "Q", ()) or ())),
            )
        )
    for name in ("BasePoint", "Direction", "Position"):
        try:
            add((name, vector(getattr(source, name, None))))
        except Exception:
            add((name, "<unavailable>"))
    for name in ("getBasePoint", "getDirection"):
        method = getattr(source, name, None)
        if callable(method):
            try:
                add((name, vector(method())))
            except Exception:
                add((name, "<unavailable>"))
    add(("NativeStateToken", getattr(source, "NativeStateToken", None)))
    return digest.hexdigest()


def _configuration_token(sketch: Any) -> str:
    placement = getattr(sketch, "Placement", None)
    base = getattr(placement, "Base", None)
    rotation = getattr(placement, "Rotation", None)
    quaternion = tuple(getattr(rotation, "Q", ()) or ())
    values = (
        tuple(float(getattr(base, axis, 0.0)) for axis in ("x", "y", "z")),
        tuple(float(value) for value in quaternion),
        float(
            getattr(
                getattr(sketch, "ArcFitTolerance", 0.0),
                "Value",
                getattr(sketch, "ArcFitTolerance", 0.0),
            )
        ),
    )
    return hashlib.sha256(repr(values).encode()).hexdigest()


def iter_external_reference_records(sketch: Any) -> Iterable[dict[str, Any]]:
    """Yield every durable LinkSub reference in exact property order."""

    document = getattr(sketch, "Document", None)
    types = list(getattr(sketch, "ExternalTypes", ()) or ())
    index = 0
    for raw in list(getattr(sketch, "ExternalGeometry", ()) or ()):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise NativeSketchError(
                "The active Sketch has malformed external references."
            )
        obj = raw[0]
        raw_names = raw[1]
        names = [raw_names] if isinstance(raw_names, str) else list(raw_names or ())
        if not names:
            names = [""]
        for raw_name in names:
            if obj is None or getattr(obj, "Document", None) is not document:
                raise NativeSketchError(
                    "The active Sketch has an unavailable external source."
                )
            raw_type = int(types[index]) if index < len(types) else 0
            kind = _EXTERNAL_KINDS.get(raw_type)
            if kind is None:
                raise NativeSketchError(
                    "The active Sketch has an unknown external type."
                )
            yield {
                "reference_index": index,
                "object": object_reference(obj),
                "subelement": str(raw_name or ""),
                "kind": kind,
            }
            index += 1
    # PropertyIntegerList exposes one legacy zero on a brand-new Sketch even though
    # ExternalGeometry has no links. Sketcher's own add/rebuild paths resize this
    # list to the durable LinkSub count, so only aligned entries are state.


def normalized_external_geometry_record(encoded: str) -> str:
    record = json.loads(encoded)
    record.pop("geometry_index", None)
    return json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def external_geometry_groups(records: Iterable[str]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for encoded in records:
        record = json.loads(encoded)
        grouped.setdefault(str(record.get("reference", "") or ""), []).append(
            normalized_external_geometry_record(encoded)
        )
    return {key: tuple(values) for key, values in grouped.items()}


def _records(sketch: Any, spec: SketchExternalSpec, *, label: str):
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, spec.target.expected_geometry_count)
    )
    constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch, spec.target.expected_constraint_count)
    )
    references = canonical_sketch_records(iter_external_reference_records(sketch))
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    expressions = sketch_expression_records(sketch, constraints, label=label)
    return geometry, constraints, references, external, expressions


def _validate_source(
    document: Any, sketch: Any, spec: SketchExternalSpec, label: str
) -> Any:
    source = resolve_object(document, spec.source)
    if source is sketch:
        raise NativeSketchError(f"{label} cannot reference the active Sketch itself.")
    if spec.subelement:
        shape = getattr(source, "Shape", None)
        getter = getattr(shape, "getElement", None)
        if not callable(getter):
            raise NativeSketchError(f"{label} source has no selectable shape.")
        try:
            element = getter(spec.subelement)
            expected = spec.subelement.rstrip("0123456789")
            if str(getattr(element, "ShapeType", "")) != expected:
                raise ValueError
        except Exception as exc:
            raise NativeSketchError(
                f"The exact {label} source subelement no longer exists."
            ) from exc
    elif not any(_is_derived(source, type_id) for type_id in _WHOLE_SOURCE_TYPES):
        raise NativeSketchError(
            f"{label} whole-object sources must be a datum, plane, line, or point."
        )
    return source


def capture_external_snapshot(
    context: NativeRuntimeContext,
    spec: SketchExternalSpec,
    *,
    label: str,
) -> SketchExternalSnapshot:
    if not isinstance(spec, SketchExternalSpec):
        raise TypeError("spec must be a SketchExternalSpec")
    target = preflight_active_sketch(context, spec.target)
    source = _validate_source(context.document, target.sketch, spec, label)
    records = _records(target.sketch, spec, label=label)
    if len(records[2]) != spec.expected_external_reference_count:
        raise NativeSketchError(
            "The active Sketch external reference count changed; read it and retry."
        )
    if len(records[3]) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read it and retry."
        )
    solver = sketch_solver_issues(target.sketch, label)
    if any(solver):
        raise NativeSketchError(f"{label} requires a Sketch without solver issues.")
    return SketchExternalSnapshot(
        target,
        spec,
        source,
        _source_token(source),
        _configuration_token(target.sketch),
        *records,
        solver,
    )


def require_external_snapshot_unchanged(
    document: Any,
    snapshot: SketchExternalSnapshot,
    *,
    label: str,
) -> tuple[Any, Any]:
    sketch = require_prepared_active_sketch(document, snapshot.target)
    source = _validate_source(document, sketch, snapshot.spec, label)
    if (
        source is not snapshot.source
        or _source_token(source) != snapshot.source_token
        or _configuration_token(sketch) != snapshot.projection_configuration
        or _records(sketch, snapshot.spec, label=label)
        != (
            snapshot.geometry_records,
            snapshot.constraint_records,
            snapshot.external_reference_records,
            snapshot.external_geometry_records,
            snapshot.expression_records,
        )
        or sketch_solver_issues(sketch, label) != snapshot.solver_issues
    ):
        raise NativeSketchError(f"The exact {label} state changed after preflight.")
    return sketch, source


def require_pure_external_diagnostic(
    snapshot: SketchExternalSnapshot,
    *,
    label: str,
) -> None:
    require_external_snapshot_unchanged(
        snapshot.target.context.document,
        snapshot,
        label=label,
    )


def verify_external_state(
    document: Any,
    snapshot: SketchExternalSnapshot,
    plan: Any,
    *,
    label: str,
) -> tuple[Any, tuple[int, ...]]:
    sketch = require_prepared_active_sketch(document, snapshot.target)
    source = _validate_source(document, sketch, snapshot.spec, label)
    geometry, constraints, references, external, expressions = _records(
        sketch,
        snapshot.spec,
        label=label,
    )
    if (
        geometry != snapshot.geometry_records
        or constraints != snapshot.constraint_records
    ):
        raise NativeSketchError(f"{label} changed internal geometry or constraints.")
    if expressions != snapshot.expression_records:
        raise NativeSketchError(f"{label} changed Sketch expressions.")
    if _configuration_token(sketch) != snapshot.projection_configuration:
        raise NativeSketchError(f"{label} changed the Sketch projection configuration.")
    if source is not snapshot.source or _source_token(source) != snapshot.source_token:
        raise NativeSketchError(f"{label} changed its source object.")
    if references != plan.reference_records:
        raise NativeSketchError(f"{label} produced the wrong durable external link.")
    if len(external) != plan.external_geometry_count:
        raise NativeSketchError(f"{label} produced the wrong projected-geometry count.")
    before_groups = external_geometry_groups(snapshot.external_geometry_records)
    actual_groups = external_geometry_groups(external)
    if actual_groups.get(plan.reference) != plan.affected_geometry_records:
        raise NativeSketchError(f"{label} produced the wrong projected geometry.")
    if set(actual_groups) != set(before_groups) | {plan.reference}:
        raise NativeSketchError(f"{label} changed unrelated external references.")
    for reference, records in before_groups.items():
        if reference != plan.reference and actual_groups.get(reference) != records:
            raise NativeSketchError(f"{label} changed unrelated external geometry.")
    if any(sketch_solver_issues(sketch, label)):
        raise NativeSketchError(f"{label} left the Sketch with solver issues.")
    affected = tuple(
        int(json.loads(encoded)["geometry_index"])
        for encoded in external
        if str(json.loads(encoded).get("reference", "") or "") == plan.reference
    )
    return sketch, affected
