# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, non-mutating element lookup for selected Sketch constraints."""

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
from VibeCADNativeSketchInspectSchema import (
    MAX_SKETCH_INSPECT_CONSTRAINT_SELECTION,
)
from VibeCADNativeSketchInspectTarget import (
    SketchInspectElement,
    bounded_sketch_count,
    relationship_element,
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


MAX_ASSOCIATED_ELEMENTS = 256
_CONSTRAINT_FIELDS = frozenset({"constraint_index", "expected_type", "expected_name"})


@dataclass(frozen=True, slots=True)
class SketchInspectConstraint:
    constraint_index: int
    expected_type: str
    expected_name: str


@dataclass(frozen=True, slots=True)
class SketchElementSelectionSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    constraints: tuple[SketchInspectConstraint, ...]


@dataclass(frozen=True, slots=True)
class PreparedElementSelection:
    target: PreparedActiveSketchTarget
    spec: SketchElementSelectionSpec
    state: FrozenConstraintRelationshipState
    links: tuple[SketchConstraintLink, ...]


def _parse_constraint(value: Any) -> SketchInspectConstraint:
    if not isinstance(value, Mapping) or set(value) != _CONSTRAINT_FIELDS:
        raise NativeSketchError("A Sketch inspect constraint has incorrect fields.")
    index = value["constraint_index"]
    expected_type = value["expected_type"]
    expected_name = value["expected_name"]
    if type(index) is not int or not 0 <= index < 1_000_000:
        raise NativeSketchError(
            "A Sketch inspect constraint_index must be from 0 to 999999."
        )
    if (
        not isinstance(expected_type, str)
        or not expected_type
        or len(expected_type) > 96
    ):
        raise NativeSketchError(
            "A Sketch inspect expected_type must be a bounded non-empty string."
        )
    if not isinstance(expected_name, str) or len(expected_name) > 128:
        raise NativeSketchError(
            "A Sketch inspect expected_name must be a bounded string."
        )
    return SketchInspectConstraint(index, expected_type, expected_name)


def prepare_element_selection(
    document_uid: str,
    values: Mapping[str, Any],
) -> SketchElementSelectionSpec:
    fields = {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "constraints",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeSketchError("Sketch element lookup has incorrect fields.")
    raw_constraints = values["constraints"]
    if not isinstance(raw_constraints, list) or not (
        1 <= len(raw_constraints) <= MAX_SKETCH_INSPECT_CONSTRAINT_SELECTION
    ):
        raise NativeSketchError(
            "Sketch element lookup requires 1 to 32 exact constraints."
        )
    constraints = tuple(_parse_constraint(value) for value in raw_constraints)
    indices = tuple(item.constraint_index for item in constraints)
    if len(set(indices)) != len(indices):
        raise NativeSketchError("Sketch inspect constraint indices must be distinct.")
    return SketchElementSelectionSpec(
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
        constraints,
    )


def _freeze_state(
    sketch: Any,
    spec: SketchElementSelectionSpec,
) -> FrozenConstraintRelationshipState:
    return freeze_constraint_relationship_state(
        sketch,
        spec,
        label="Sketch element lookup",
    )


def _selected_links(
    state: FrozenConstraintRelationshipState,
    constraints: tuple[SketchInspectConstraint, ...],
) -> tuple[SketchConstraintLink, ...]:
    selected = []
    for target in constraints:
        if target.constraint_index >= len(state.links):
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} is unavailable."
            )
        link = state.links[target.constraint_index]
        if (
            link.index != target.constraint_index
            or link.constraint_type != target.expected_type
            or link.name != target.expected_name
        ):
            raise NativeSketchError(
                f"Sketch constraint {target.constraint_index} changed; read its current state and retry."
            )
        selected.append(link)
    return tuple(selected)


def preflight_element_selection(
    context: NativeRuntimeContext,
    spec: SketchElementSelectionSpec,
) -> PreparedElementSelection:
    if not isinstance(spec, SketchElementSelectionSpec):
        raise TypeError("spec must be a SketchElementSelectionSpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze_state(target.sketch, spec)
    links = _selected_links(state, spec.constraints)
    seen: set[SketchInspectElement] = set()
    for link in links:
        for index, position in link.elements:
            element = relationship_element(index, position)
            if element in seen:
                continue
            if len(seen) >= MAX_ASSOCIATED_ELEMENTS:
                raise NativeSketchError(
                    "More than 256 elements match these constraints; narrow the exact constraint query."
                )
            validate_sketch_inspect_element(
                target.sketch,
                element,
                expected_geometry_count=spec.target.expected_geometry_count,
                expected_external_geometry_count=spec.expected_external_geometry_count,
                external_records=state.sketch_state.external_geometry_records,
            )
            seen.add(element)
    return PreparedElementSelection(target, spec, state, links)


def _result(prepared: PreparedElementSelection) -> dict[str, Any]:
    associated: list[dict[str, Any]] = []
    by_element: dict[SketchInspectElement, dict[str, Any]] = {}
    for selection_index, link in enumerate(prepared.links):
        for index, position in link.elements:
            element = relationship_element(index, position)
            existing = by_element.get(element)
            if existing is None:
                existing = {
                    **element.summary(),
                    "matched_constraint_selection_indices": [],
                }
                by_element[element] = existing
                associated.append(existing)
            existing["matched_constraint_selection_indices"].append(selection_index)
    state = prepared.state.sketch_state
    return {
        "operation": "select_elements",
        "sketch": object_reference(prepared.target.sketch),
        "selected_constraints": [
            {
                "constraint_index": link.index,
                "type": link.constraint_type,
                "name": link.name,
            }
            for link in prepared.links
        ],
        "selected_constraint_count": len(prepared.links),
        "associated_elements": associated,
        "associated_element_count": len(associated),
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


def read_associated_elements(
    context: NativeRuntimeContext,
    spec: SketchElementSelectionSpec,
) -> dict[str, Any]:
    prepared = preflight_element_selection(context, spec)
    result = _result(prepared)
    sketch = require_prepared_active_sketch(context.document, prepared.target)
    if _freeze_state(sketch, spec) != prepared.state:
        raise NativeSketchError("The active Sketch changed during element lookup.")
    return result
