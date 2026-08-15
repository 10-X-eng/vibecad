# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact targets for Sketch virtual-space view and constraint state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)


LABEL = "Sketch Virtual Space"
OPERATION = "set_virtual_space"
MAX_CONSTRAINT_TARGETS = 16
MAX_EXTERNAL_GEOMETRY = 1_000_000
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
    }
)
_VIEW_FIELDS = frozenset(
    {"kind", "expected_shown_virtual_space", "shown_virtual_space"}
)
_CONSTRAINT_FIELDS = frozenset({"kind", "constraints"})
_CONSTRAINT_TARGET_FIELDS = frozenset(
    {"constraint_index", "expected_virtual_space", "virtual_space"}
)


@dataclass(frozen=True, slots=True)
class SketchVirtualSpaceViewTarget:
    expected_shown_virtual_space: bool
    shown_virtual_space: bool


@dataclass(frozen=True, slots=True)
class SketchVirtualSpaceConstraintTarget:
    constraint_index: int
    expected_virtual_space: bool
    virtual_space: bool


@dataclass(frozen=True, slots=True)
class SketchVirtualSpaceConstraintsTarget:
    constraints: tuple[SketchVirtualSpaceConstraintTarget, ...]


SketchVirtualSpaceTarget = (
    SketchVirtualSpaceViewTarget | SketchVirtualSpaceConstraintsTarget
)


@dataclass(frozen=True, slots=True)
class SketchVirtualSpaceSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    virtual_space_target: SketchVirtualSpaceTarget


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EXTERNAL_GEOMETRY:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            f"{MAX_EXTERNAL_GEOMETRY}."
        )
    return value


def _view_target(value: Mapping[str, Any]) -> SketchVirtualSpaceViewTarget:
    expected = value["expected_shown_virtual_space"]
    shown = value["shown_virtual_space"]
    if type(expected) is not bool or type(shown) is not bool:
        raise NativeSketchError(f"{LABEL} view states must be booleans.")
    return SketchVirtualSpaceViewTarget(expected, shown)


def _constraint_target(value: Any) -> SketchVirtualSpaceConstraintTarget:
    if not isinstance(value, Mapping) or set(value) != _CONSTRAINT_TARGET_FIELDS:
        raise NativeSketchError(f"A {LABEL} constraint target has incorrect fields.")
    index = value["constraint_index"]
    expected = value["expected_virtual_space"]
    desired = value["virtual_space"]
    if type(index) is not int or not 0 <= index < 1_000_000:
        raise NativeSketchError(
            f"A {LABEL} constraint_index must be an integer from 0 to 999999."
        )
    if type(expected) is not bool or type(desired) is not bool:
        raise NativeSketchError(f"{LABEL} constraint states must be booleans.")
    if expected is desired:
        raise NativeSketchError(
            f"A {LABEL} constraint target must switch to the other space."
        )
    return SketchVirtualSpaceConstraintTarget(index, expected, desired)


def _constraints_target(
    value: Mapping[str, Any],
) -> SketchVirtualSpaceConstraintsTarget:
    raw = value["constraints"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CONSTRAINT_TARGETS:
        raise NativeSketchError(
            f"{LABEL} constraints must contain one through {MAX_CONSTRAINT_TARGETS} "
            "exact targets."
        )
    targets = tuple(_constraint_target(item) for item in raw)
    indices = tuple(item.constraint_index for item in targets)
    if len(indices) != len(set(indices)):
        raise NativeSketchError(f"{LABEL} constraint targets must be distinct.")
    return SketchVirtualSpaceConstraintsTarget(targets)


def _target(value: Any) -> SketchVirtualSpaceTarget:
    if not isinstance(value, Mapping):
        raise NativeSketchError(f"{LABEL} target must be an object.")
    kind = value.get("kind")
    if kind == "view" and set(value) == _VIEW_FIELDS:
        return _view_target(value)
    if kind == "constraints" and set(value) == _CONSTRAINT_FIELDS:
        return _constraints_target(value)
    raise NativeSketchError(f"{LABEL} target has incorrect fields.")


def prepare_sketch_virtual_space_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchVirtualSpaceSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"{LABEL} definition has incorrect fields.")
    return SketchVirtualSpaceSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _external_count(value["expected_external_geometry_count"]),
        _target(value["target"]),
    )
