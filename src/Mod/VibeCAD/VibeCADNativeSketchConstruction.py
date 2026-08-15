# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact durable Construction-state toggles in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import (
    canonical_sketch_record,
    canonical_sketch_records,
    sketch_records_sha256,
)
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_identity


MAX_CONSTRUCTION_TARGETS = 64
MAX_EXTERNAL_SKETCH_GEOMETRY = 1_000_000
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "targets",
    }
)
_TARGET_FIELDS = frozenset({"geometry_index", "expected_state"})


@dataclass(frozen=True, slots=True)
class SketchConstructionTarget:
    geometry_index: int
    expected_state: bool


@dataclass(frozen=True, slots=True)
class SketchConstructionSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    targets: tuple[SketchConstructionTarget, ...]


@dataclass(frozen=True, slots=True)
class PreparedSketchConstruction:
    target: PreparedActiveSketchTarget
    spec: SketchConstructionSpec
    geometry_records: tuple[str, ...]
    constraint_sha256: str
    external_geometry_records: tuple[str, ...]


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EXTERNAL_SKETCH_GEOMETRY:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            f"{MAX_EXTERNAL_SKETCH_GEOMETRY}."
        )
    return value


def _target(value: Any) -> SketchConstructionTarget:
    if not isinstance(value, Mapping) or set(value) != _TARGET_FIELDS:
        raise NativeSketchError("A Sketch Construction target has incorrect fields.")
    index = value["geometry_index"]
    if type(index) is not int or not (
        0 <= index < 1_000_000 or -1_000_000 <= index <= -3
    ):
        raise NativeSketchError(
            "A Sketch Construction geometry_index must name internal geometry at zero "
            "or above, or external geometry at -3 or below."
        )
    expected_state = value["expected_state"]
    if type(expected_state) is not bool:
        raise NativeSketchError(
            "A Sketch Construction expected_state must be a boolean."
        )
    return SketchConstructionTarget(index, expected_state)


def prepare_sketch_construction(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchConstructionSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Construction definition has incorrect fields.")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not (
        1 <= len(raw_targets) <= MAX_CONSTRUCTION_TARGETS
    ):
        raise NativeSketchError(
            "Sketch Construction targets must contain 1 through "
            f"{MAX_CONSTRUCTION_TARGETS} exact geometry targets."
        )
    targets = tuple(_target(raw) for raw in raw_targets)
    indices = tuple(target.geometry_index for target in targets)
    if len(set(indices)) != len(indices):
        raise NativeSketchError("Sketch Construction geometry targets must be distinct.")
    return SketchConstructionSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _external_count(value["expected_external_geometry_count"]),
        targets,
    )


def _current_records(
    sketch: Any,
    spec: SketchConstructionSpec,
) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    geometry = canonical_sketch_records(
        iter_sketch_geometry_records(sketch, spec.target.expected_geometry_count)
    )
    constraints = sketch_records_sha256(
        iter_sketch_constraint_records(sketch, spec.target.expected_constraint_count)
    )
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    return geometry, constraints, external


def _group_members(sketch: Any) -> dict[int, int]:
    owners: dict[int, int] = {}
    try:
        constraints = list(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError("Sketch group constraints are unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "")) not in {"Group", "Text"}:
            continue
        raw_elements = getattr(constraint, "Elements", None)
        if not isinstance(raw_elements, (list, tuple)) or not raw_elements:
            raise NativeSketchError("Sketch group constraint elements are unavailable.")
        try:
            handle = int(raw_elements[0][0])
            for raw in raw_elements[1:]:
                member = int(raw[0])
                if member >= 0:
                    owners[member] = handle
        except (IndexError, TypeError, ValueError) as exc:
            raise NativeSketchError("Sketch group constraint elements are malformed.") from exc
    return owners


def _decoded_by_index(records: tuple[str, ...], key: str) -> dict[int, dict[str, Any]]:
    result = {}
    for encoded in records:
        record = json.loads(encoded)
        result[int(record[key])] = record
    return result


def _validate_targets(
    sketch: Any,
    spec: SketchConstructionSpec,
    geometry_records: tuple[str, ...],
    external_records: tuple[str, ...],
) -> None:
    internal = _decoded_by_index(geometry_records, "index")
    external = _decoded_by_index(external_records, "geometry_index")
    group_members = _group_members(sketch)
    for target in spec.targets:
        index = target.geometry_index
        if index >= 0:
            record = internal.get(index)
            if record is None:
                raise NativeSketchError(
                    f"Sketch internal geometry index {index} is unavailable."
                )
            if index in group_members:
                raise NativeSketchError(
                    f"Sketch geometry {index} is selected through group handle "
                    f"{group_members[index]}; target that handle explicitly."
                )
            if str(record.get("internal_type", "")):
                raise NativeSketchError(
                    f"Sketch geometry {index} is internal-alignment geometry and cannot "
                    "change Construction state."
                )
            current = bool(record.get("construction"))
        else:
            record = external.get(index)
            if record is None:
                raise NativeSketchError(
                    f"Sketch external geometry index {index} is unavailable."
                )
            if "defining" not in record:
                raise NativeSketchError(
                    f"Sketch external geometry {index} does not expose a defining "
                    "state and cannot be changed safely."
                )
            current = bool(record.get("defining"))
        if current is not target.expected_state:
            state_name = "construction" if index >= 0 else "defining"
            raise NativeSketchError(
                f"Sketch geometry {index} {state_name} state changed; read the current "
                "Sketch and retry."
            )


def preflight_sketch_construction(
    context: NativeRuntimeContext,
    spec: SketchConstructionSpec,
) -> PreparedSketchConstruction:
    if not isinstance(spec, SketchConstructionSpec):
        raise TypeError("spec must be a SketchConstructionSpec")
    target = preflight_active_sketch(context, spec.target)
    geometry, constraints, external = _current_records(target.sketch, spec)
    if len(external) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read its current state "
            "and retry."
        )
    _validate_targets(target.sketch, spec, geometry, external)
    return PreparedSketchConstruction(target, spec, geometry, constraints, external)


def _require_unchanged(
    document: Any,
    prepared: PreparedSketchConstruction,
    *,
    stage: str,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    geometry, constraints, external = _current_records(sketch, prepared.spec)
    if (
        geometry != prepared.geometry_records
        or constraints != prepared.constraint_sha256
        or external != prepared.external_geometry_records
    ):
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    return sketch


def create_sketch_construction(
    document: Any,
    prepared: PreparedSketchConstruction,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchConstruction):
        raise TypeError("prepared must be a PreparedSketchConstruction")
    sketch = _require_unchanged(
        document,
        prepared,
        stage="after Construction preflight",
    )
    for target in prepared.spec.targets:
        sketch.toggleConstruction(target.geometry_index)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_records(
    records: tuple[str, ...],
    targets: Mapping[int, SketchConstructionTarget],
    *,
    index_key: str,
    state_key: str,
) -> tuple[str, ...]:
    expected = []
    for encoded in records:
        record = json.loads(encoded)
        target = targets.get(int(record[index_key]))
        if target is not None:
            record[state_key] = not target.expected_state
        expected.append(canonical_sketch_record(record))
    return tuple(expected)


def verify_sketch_construction(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchConstruction = draft.value["prepared"]
    sketch = require_prepared_active_sketch(document, prepared.target)
    geometry, constraints, external = _current_records(sketch, prepared.spec)
    targets = {target.geometry_index: target for target in prepared.spec.targets}
    expected_geometry = _expected_records(
        prepared.geometry_records,
        targets,
        index_key="index",
        state_key="construction",
    )
    expected_external = _expected_records(
        prepared.external_geometry_records,
        targets,
        index_key="geometry_index",
        state_key="defining",
    )
    if constraints != prepared.constraint_sha256:
        raise NativeSketchError("Sketch Construction changed constraints.")
    if geometry != expected_geometry or external != expected_external:
        raise NativeSketchError(
            "Sketch Construction changed geometry beyond the exact requested states."
        )
    changed = []
    internal = _decoded_by_index(geometry, "index")
    external_by_index = _decoded_by_index(external, "geometry_index")
    for target in prepared.spec.targets:
        index = target.geometry_index
        record = internal[index] if index >= 0 else external_by_index[index]
        changed.append(
            {
                "geometry_index": index,
                "geometry_kind": record["kind"],
                "state_kind": "construction" if index >= 0 else "defining",
                "previous_state": target.expected_state,
                "current_state": not target.expected_state,
            }
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "toggle_construction",
            "changed_geometry": changed,
            "external_geometry_count": len(external),
        },
    )
