# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reusable-profile implementation for Design Revolve."""

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
)
from VibeCADNativeDesignReferences import DesignLinkSpec
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_NATIVE_TYPES = {
    "angle": "Angle",
    "up_to_last": "UpToLast",
    "up_to_first": "UpToFirst",
    "up_to_face": "UpToFace",
    "two_angles": "TwoAngles",
}
_EXTENT_FIELDS = {
    "angle": frozenset({"kind", "angle_degrees", "symmetric", "reversed"}),
    "up_to_last": frozenset({"kind"}),
    "up_to_first": frozenset({"kind", "reversed"}),
    "up_to_face": frozenset({"kind", "target", "reversed"}),
    "two_angles": frozenset(
        {"kind", "angle1_degrees", "angle2_degrees", "reversed"}
    ),
}


@dataclass(frozen=True, slots=True)
class DesignRevolveSpec:
    profile: DesignLinkSpec
    axis: DesignLinkSpec
    extent_kind: str
    angle1: float
    angle2: float
    symmetric: bool
    reversed: bool
    up_to_face: DesignLinkSpec | None


def prepare_design_revolve(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignRevolveSpec:
    extent = values["extent"]
    if not isinstance(extent, Mapping):
        raise NativeModelError("A Design Revolve extent is invalid.")
    kind = str(extent.get("kind") or "")
    extent_fields = _EXTENT_FIELDS.get(kind)
    if extent_fields is None:
        raise NativeModelError("That Design Revolve extent is unavailable.")
    if set(extent) != extent_fields:
        raise NativeModelError("The Design Revolve extent fields do not match its kind.")
    symmetric = bool(extent.get("symmetric", False))
    reversed_value = bool(extent.get("reversed", False))
    if symmetric and reversed_value:
        raise NativeModelError("A symmetric Design Revolve cannot also be reversed.")
    angle1 = float(extent.get("angle_degrees", extent.get("angle1_degrees", 360.0)))
    angle2 = float(extent.get("angle2_degrees", 0.0))
    if not math.isfinite(angle1) or not math.isfinite(angle2):
        raise NativeModelError("Design Revolve angles must be finite.")
    face = (
        face_spec(document_uid, extent["target"])
        if kind == "up_to_face"
        else None
    )
    return DesignRevolveSpec(
        profile_spec(document_uid, values["profile"]),
        axis_spec(document_uid, values["axis"]),
        kind,
        angle1,
        angle2,
        symmetric,
        reversed_value,
        face,
    )


def preflight_design_revolve(document: Any, spec: DesignRevolveSpec) -> None:
    preflight_profile_inputs(document, spec.profile, spec.axis, spec.up_to_face)


def _number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def create_design_revolve(
    document: Any,
    *,
    label: str,
    spec: DesignRevolveSpec,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        axis = set_exact_axis_link(operation, "ReferenceAxis", spec.axis)
        operation.Type = _NATIVE_TYPES[spec.extent_kind]
        operation.Angle = spec.angle1
        operation.Angle2 = spec.angle2
        operation.Midplane = spec.symmetric
        operation.Reversed = spec.reversed
        face = (
            set_exact_link(operation, "UpToFace", spec.up_to_face)
            if spec.up_to_face is not None
            else None
        )
        return {
            "axis": axis,
            "extent": spec.extent_kind,
            "angle1_degrees": spec.angle1,
            "angle2_degrees": spec.angle2,
            "symmetric": spec.symmetric,
            "reversed": spec.reversed,
            "up_to_face": face,
        }

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        verify_exact_link(operation, "ReferenceAxis", expected["axis"])
        if expected["up_to_face"] is not None:
            verify_exact_link(operation, "UpToFace", expected["up_to_face"])
        if (
            str(operation.Type) != _NATIVE_TYPES[expected["extent"]]
            or abs(_number(operation.Angle) - expected["angle1_degrees"]) > 1.0e-8
            or abs(_number(operation.Angle2) - expected["angle2_degrees"]) > 1.0e-8
            or bool(operation.Midplane) is not expected["symmetric"]
            or bool(operation.Reversed) is not expected["reversed"]
        ):
            raise NativeModelError("Design Revolve parameters changed before commit.")
        return {
            "axis": dict(expected["axis"]),
            "extent": expected["extent"],
            "angle1_degrees": _number(operation.Angle),
            "angle2_degrees": _number(operation.Angle2),
            "symmetric": bool(operation.Midplane),
            "reversed": bool(operation.Reversed),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignRevolve",
        base_name="Revolve",
        label=label,
        profile_spec=spec.profile,
        result_spec=result_spec,
        configure_specific=configure,
        verify_specific=verify,
    )
