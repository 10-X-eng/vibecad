# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact whole-edge targets for Native Sketch Block constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    prepare_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError


LABEL = "Sketch Block"
MAX_BLOCK_TARGETS = 16
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
)
_INTERNAL_EDGE_TYPES = frozenset(
    {
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "HyperbolaMajor",
        "HyperbolaMinor",
        "ParabolaFocalAxis",
        "BSplineControlPoint",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBlockSpec:
    target: SketchConstraintTargetSpec


@dataclass(frozen=True, slots=True)
class ResolvedSketchBlock:
    references: tuple[SketchConstraintElement, ...]
    type_ids: tuple[str, ...]


def prepare_sketch_block_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBlockSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    selection = value["selection"]
    if not isinstance(selection, list) or not 1 <= len(selection) <= MAX_BLOCK_TARGETS:
        raise NativeSketchError(
            f"{LABEL} selection must contain one through {MAX_BLOCK_TARGETS} edges."
        )
    return SketchBlockSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value["expected_external_geometry_count"],
            selection=selection,
            maximum_selection=MAX_BLOCK_TARGETS,
            allowed_internal_types=_INTERNAL_EDGE_TYPES,
        )
    )


def _blocked_facade(sketch: Any, index: int) -> bool:
    try:
        return bool(sketch.GeometryFacadeList[index].Blocked)
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} geometry {index} blocked state is unavailable."
        ) from exc


def _existing_block_targets(sketch: Any) -> frozenset[int]:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    result = set()
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") != "Block":
            continue
        try:
            index = int(constraint.First)
        except Exception as exc:
            raise NativeSketchError(f"{LABEL} has a malformed Block constraint.") from exc
        if index < 0:
            raise NativeSketchError(f"{LABEL} has a malformed Block constraint target.")
        result.add(index)
    return frozenset(result)


def resolve_sketch_block(sketch: Any, spec: SketchBlockSpec) -> ResolvedSketchBlock:
    if not isinstance(spec, SketchBlockSpec):
        raise TypeError("spec must be a SketchBlockSpec")
    existing = _existing_block_targets(sketch)
    type_ids = []
    for element in spec.target.selection:
        index = element.geometry_index
        if element.position != "whole" or index < 0:
            raise NativeSketchError(f"{LABEL} targets must be exact internal whole edges.")
        geometry = sketch_constraint_geometry(sketch, index)
        type_id = str(getattr(geometry, "TypeId", "") or "")
        if not type_id or type_id == "Part::GeomPoint":
            raise NativeSketchError(f"{LABEL} cannot target Sketch points.")
        if index in existing:
            raise NativeSketchError(
                f"{LABEL} geometry {index} already has a Block constraint."
            )
        if _blocked_facade(sketch, index):
            raise NativeSketchError(
                f"{LABEL} geometry {index} is blocked without a matching constraint."
            )
        type_ids.append(type_id)
    return ResolvedSketchBlock(spec.target.selection, tuple(type_ids))


def make_block_constraints(resolved: ResolvedSketchBlock) -> tuple[Any, ...]:
    if not isinstance(resolved, ResolvedSketchBlock):
        raise TypeError("resolved must be a ResolvedSketchBlock")
    import Sketcher

    try:
        return tuple(
            Sketcher.Constraint("Block", element.geometry_index)
            for element in resolved.references
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} definitions."
        ) from exc
