# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-mutating constraint lookup for selected Sketch elements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintRelationships import (
    FrozenConstraintRelationshipState,
    SketchConstraintLink,
    freeze_constraint_relationship_state,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records_sha256
from VibeCADNativeSketchInspectSchema import MAX_SKETCH_INSPECT_SELECTION
from VibeCADNativeSketchInspectTarget import (
    SketchInspectElement,
    bounded_sketch_count,
    parse_sketch_inspect_element,
    validate_sketch_inspect_element,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_reference


MAX_ASSOCIATED_CONSTRAINTS = 64


@dataclass(frozen=True, slots=True)
class SketchConstraintSelectionSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    selection: tuple[SketchInspectElement, ...]


@dataclass(frozen=True, slots=True)
class PreparedConstraintSelection:
    target: PreparedActiveSketchTarget
    spec: SketchConstraintSelectionSpec
    state: FrozenConstraintRelationshipState


def prepare_constraint_selection(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchConstraintSelectionSpec:
    fields = {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeSketchError("Sketch constraint lookup has incorrect fields.")
    raw_selection = values["selection"]
    if not isinstance(raw_selection, list) or not (
        1 <= len(raw_selection) <= MAX_SKETCH_INSPECT_SELECTION
    ):
        raise NativeSketchError(
            "Sketch constraint lookup requires 1 to 32 exact elements."
        )
    selection = tuple(parse_sketch_inspect_element(value) for value in raw_selection)
    identities = tuple((item.geometry_index, item.position) for item in selection)
    if len(set(identities)) != len(identities):
        raise NativeSketchError("Sketch inspect elements must be distinct.")
    return SketchConstraintSelectionSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=values["sketch"],
            expected_geometry_count=values["expected_geometry_count"],
            expected_constraint_count=values["expected_constraint_count"],
        ),
        bounded_sketch_count(
            values["expected_external_geometry_count"],
            "external geometry count",
        ),
        selection,
    )


def _freeze_state(
    sketch: Any,
    spec: SketchConstraintSelectionSpec,
) -> FrozenConstraintRelationshipState:
    return freeze_constraint_relationship_state(
        sketch,
        spec,
        label="Sketch constraint lookup",
    )


def preflight_constraint_selection(
    context: NativeRuntimeContext,
    spec: SketchConstraintSelectionSpec,
) -> PreparedConstraintSelection:
    if not isinstance(spec, SketchConstraintSelectionSpec):
        raise TypeError("spec must be a SketchConstraintSelectionSpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze_state(target.sketch, spec)
    for element in spec.selection:
        validate_sketch_inspect_element(
            target.sketch,
            element,
            expected_geometry_count=spec.target.expected_geometry_count,
            expected_external_geometry_count=spec.expected_external_geometry_count,
            external_records=state.sketch_state.external_geometry_records,
        )
    return PreparedConstraintSelection(target, spec, state)


def _matches(link: SketchConstraintLink, element: SketchInspectElement) -> bool:
    if element.position == "whole":
        return any(
            index == element.geometry_index for index, _position in link.elements
        )
    return (element.geometry_index, element.position_code) in link.elements


def _result(prepared: PreparedConstraintSelection) -> dict[str, Any]:
    associated = []
    for link in prepared.state.links:
        matched = [
            index
            for index, element in enumerate(prepared.spec.selection)
            if _matches(link, element)
        ]
        if not matched:
            continue
        if len(associated) >= MAX_ASSOCIATED_CONSTRAINTS:
            raise NativeSketchError(
                "More than 64 constraints match this selection; narrow the exact element query."
            )
        associated.append(
            {
                "constraint_index": link.index,
                "type": link.constraint_type,
                "name": link.name,
                "matched_selection_indices": matched,
            }
        )
    state = prepared.state.sketch_state
    return {
        "operation": "select_constraints",
        "sketch": object_reference(prepared.target.sketch),
        "selection": [item.summary() for item in prepared.spec.selection],
        "selection_count": len(prepared.spec.selection),
        "associated_constraints": associated,
        "associated_constraint_count": len(associated),
        "geometry_count": prepared.spec.target.expected_geometry_count,
        "constraint_count": prepared.spec.target.expected_constraint_count,
        "external_geometry_count": prepared.state.external_geometry_count,
        "geometry_state_sha256": canonical_sketch_records_sha256(
            state.geometry_records
        ),
        "constraint_state_sha256": canonical_sketch_records_sha256(
            state.constraint_records
        ),
    }


def read_associated_constraints(
    context: NativeRuntimeContext,
    spec: SketchConstraintSelectionSpec,
) -> dict[str, Any]:
    prepared = preflight_constraint_selection(context, spec)
    result = _result(prepared)
    sketch = require_prepared_active_sketch(context.document, prepared.target)
    if _freeze_state(sketch, spec) != prepared.state:
        raise NativeSketchError("The active Sketch changed during constraint lookup.")
    return result
