# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact direction and extent implementation for Design Extrude."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignProfileBase import (
    create_profile_design_operation,
    set_exact_axis_link,
    set_exact_link,
    verify_exact_link,
)
from VibeCADNativeDesignProfileInput import (
    axis_spec,
    face_spec,
    preflight_profile_inputs,
    profile_spec,
    shape_spec,
    vector_from_mapping,
)
from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    link_summary,
    property_link_list_summary,
    resolve_definition_link,
)
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_SIDE_TYPES = {
    "one_side": "One side",
    "two_sides": "Two sides",
    "symmetric": "Symmetric",
}
_EXTENT_FIELDS = frozenset({"kind", "sides", "reversed"})
_DIRECTION_FIELDS = {
    "sketch_normal": frozenset({"kind"}),
    "reference_axis": frozenset({"kind", "target", "along_sketch_normal"}),
    "custom_vector": frozenset({"kind", "vector", "along_sketch_normal"}),
}
_TERMINATIONS = {
    "length": "Length",
    "up_to_last": "UpToLast",
    "up_to_first": "UpToFirst",
    "up_to_face": "UpToFace",
    "up_to_shape": "UpToShape",
}
_TERMINATION_FIELDS = {
    "length": frozenset({"kind", "length_mm", "taper_degrees"}),
    "up_to_last": frozenset({"kind", "offset_mm"}),
    "up_to_first": frozenset({"kind", "offset_mm"}),
    "up_to_face": frozenset({"kind", "target", "offset_mm"}),
    "up_to_shape": frozenset({"kind", "target", "offset_mm"}),
}


@dataclass(frozen=True, slots=True)
class ExtrudeSideSpec:
    kind: str
    length: float
    taper: float
    offset: float
    target: DesignLinkSpec | None


@dataclass(frozen=True, slots=True)
class DesignExtrudeSpec:
    profile: DesignLinkSpec
    direction_kind: str
    direction_axis: DesignLinkSpec
    direction_vector: tuple[float, float, float] | None
    along_sketch_normal: bool
    extent_kind: str
    side1: ExtrudeSideSpec
    side2: ExtrudeSideSpec | None
    reversed: bool


def _side(document_uid: str, value: Mapping[str, Any]) -> ExtrudeSideSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Design Extrude side is invalid.")
    values = dict(value)
    kind = str(values.get("kind") or "")
    expected = _TERMINATION_FIELDS.get(kind)
    if expected is None:
        raise NativeModelError("That Design Extrude termination is unavailable.")
    if set(values) != expected:
        raise NativeModelError(
            "The Design Extrude termination fields do not match its kind."
        )
    target = None
    if kind == "up_to_face":
        target = face_spec(document_uid, values["target"])
    elif kind == "up_to_shape":
        target = shape_spec(document_uid, values["target"])
    numbers = (
        float(values.get("length_mm", 10.0)),
        float(values.get("taper_degrees", 0.0)),
        float(values.get("offset_mm", 0.0)),
    )
    if not all(math.isfinite(number) for number in numbers):
        raise NativeModelError("Design Extrude side parameters must be finite.")
    return ExtrudeSideSpec(kind, *numbers, target)


def prepare_design_extrude(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignExtrudeSpec:
    profile = profile_spec(document_uid, values["profile"])
    direction = values["direction"]
    extent = values["extent"]
    if not isinstance(direction, Mapping) or not isinstance(extent, Mapping):
        raise NativeModelError("Design Extrude direction or extent is invalid.")
    direction_kind = str(direction.get("kind") or "")
    direction_fields = _DIRECTION_FIELDS.get(direction_kind)
    if direction_fields is None:
        raise NativeModelError("That Design Extrude direction is unavailable.")
    if set(direction) != direction_fields:
        raise NativeModelError(
            "The Design Extrude direction fields do not match its kind."
        )
    direction_axis = (
        axis_spec(document_uid, direction["target"])
        if direction_kind == "reference_axis"
        else DesignLinkSpec(profile.object_ref, ("N_Axis",))
    )
    direction_vector = (
        vector_from_mapping(direction["vector"], label="Design Extrude direction")
        if direction_kind == "custom_vector"
        else None
    )
    extent_kind = str(extent.get("kind") or "")
    if extent_kind not in _SIDE_TYPES:
        raise NativeModelError("That Design Extrude side layout is unavailable.")
    if set(extent) != _EXTENT_FIELDS:
        raise NativeModelError(
            "The Design Extrude extent fields do not match its side layout."
        )
    raw_sides = extent["sides"]
    expected_count = 2 if extent_kind == "two_sides" else 1
    if not isinstance(raw_sides, list) or len(raw_sides) != expected_count:
        raise NativeModelError(
            f"A {extent_kind} Design Extrude requires {expected_count} side definition(s)."
        )
    sides = tuple(_side(document_uid, value) for value in raw_sides)
    side1 = sides[0]
    side2 = sides[1] if len(sides) == 2 else None
    reversed_value = bool(extent.get("reversed", False))
    if extent_kind == "symmetric" and side1.kind == "length" and reversed_value:
        raise NativeModelError("A symmetric length Design Extrude cannot be reversed.")
    return DesignExtrudeSpec(
        profile,
        direction_kind,
        direction_axis,
        direction_vector,
        bool(direction.get("along_sketch_normal", True)),
        extent_kind,
        side1,
        side2,
        reversed_value,
    )


def preflight_design_extrude(document: Any, spec: DesignExtrudeSpec) -> None:
    references = [spec.direction_axis, spec.side1.target]
    if spec.side2 is not None:
        references.append(spec.side2.target)
    preflight_profile_inputs(document, spec.profile, *references)


def _number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _set_shape_target(
    operation: Any,
    property_name: str,
    target: DesignLinkSpec,
) -> list[dict[str, Any]]:
    resolved = resolve_definition_link(operation, target)
    entry = resolved[0] if not resolved[1] else (resolved[0], resolved[1])
    setattr(operation, property_name, [entry])
    return [link_summary(resolved)]


def _configure_side(
    operation: Any,
    side: ExtrudeSideSpec,
    suffix: str,
) -> dict[str, Any]:
    setattr(operation, f"Type{suffix}", _TERMINATIONS[side.kind])
    target = None
    if side.kind == "length":
        setattr(operation, f"Length{suffix}", side.length)
        setattr(operation, f"TaperAngle{suffix}", side.taper)
    else:
        setattr(operation, f"Offset{suffix}", side.offset)
    if side.kind == "up_to_face":
        target = set_exact_link(operation, f"UpToFace{suffix}", side.target)
    elif side.kind == "up_to_shape":
        target = _set_shape_target(operation, f"UpToShape{suffix}", side.target)
    return {
        "kind": side.kind,
        "length_mm": side.length,
        "taper_degrees": side.taper,
        "offset_mm": side.offset,
        "target": target,
    }


def _verify_side(
    operation: Any,
    expected: Mapping[str, Any],
    suffix: str,
) -> None:
    if str(getattr(operation, f"Type{suffix}")) != _TERMINATIONS[expected["kind"]]:
        raise NativeModelError("Design Extrude side parameters changed before commit.")
    if expected["kind"] == "length":
        changed = (
            abs(_number(getattr(operation, f"Length{suffix}")) - expected["length_mm"])
            > 1.0e-8
            or abs(
                _number(getattr(operation, f"TaperAngle{suffix}"))
                - expected["taper_degrees"]
            )
            > 1.0e-8
        )
    else:
        changed = (
            abs(_number(getattr(operation, f"Offset{suffix}")) - expected["offset_mm"])
            > 1.0e-8
        )
    if changed:
        raise NativeModelError("Design Extrude side parameters changed before commit.")
    if expected["kind"] == "up_to_face":
        verify_exact_link(operation, f"UpToFace{suffix}", expected["target"])
    elif expected["kind"] == "up_to_shape":
        actual = property_link_list_summary(getattr(operation, f"UpToShape{suffix}"))
        if actual != expected["target"]:
            raise NativeModelError("Design Extrude up-to-shape target changed before commit.")


def create_design_extrude(
    document: Any,
    *,
    label: str,
    spec: DesignExtrudeSpec,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        import FreeCAD as App

        operation.SideType = _SIDE_TYPES[spec.extent_kind]
        axis = set_exact_axis_link(operation, "ReferenceAxis", spec.direction_axis)
        operation.UseCustomVector = spec.direction_kind == "custom_vector"
        if spec.direction_vector is not None:
            operation.Direction = App.Vector(*spec.direction_vector)
        operation.AlongSketchNormal = spec.along_sketch_normal
        if not (spec.extent_kind == "symmetric" and spec.side1.kind == "length"):
            operation.Reversed = spec.reversed
        side1 = _configure_side(operation, spec.side1, "")
        side2 = _configure_side(operation, spec.side2, "2") if spec.side2 else None
        return {
            "direction_kind": spec.direction_kind,
            "axis": axis,
            "direction_vector": spec.direction_vector,
            "along_sketch_normal": spec.along_sketch_normal,
            "extent_kind": spec.extent_kind,
            "reversed": spec.reversed,
            "side1": side1,
            "side2": side2,
        }

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        verify_exact_link(operation, "ReferenceAxis", expected["axis"])
        if (
            str(operation.SideType) != _SIDE_TYPES[expected["extent_kind"]]
            or bool(operation.UseCustomVector)
            is not (expected["direction_kind"] == "custom_vector")
            or bool(operation.AlongSketchNormal) is not expected["along_sketch_normal"]
            or bool(operation.Reversed) is not expected["reversed"]
        ):
            raise NativeModelError("Design Extrude controls changed before commit.")
        if expected["direction_vector"] is not None:
            actual = tuple(float(getattr(operation.Direction, name)) for name in ("x", "y", "z"))
            if any(
                abs(value - requested) > 1.0e-8
                for value, requested in zip(actual, expected["direction_vector"])
            ):
                raise NativeModelError("Design Extrude direction changed before commit.")
        _verify_side(operation, expected["side1"], "")
        if expected["side2"] is not None:
            _verify_side(operation, expected["side2"], "2")
        return {
            "direction": expected["direction_kind"],
            "extent": expected["extent_kind"],
            "reversed": bool(operation.Reversed),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignExtrude",
        base_name="Extrude",
        label=label,
        profile_spec=spec.profile,
        result_spec=result_spec,
        configure_specific=configure,
        verify_specific=verify,
    )
