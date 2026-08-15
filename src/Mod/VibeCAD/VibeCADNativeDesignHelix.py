# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reusable-profile implementation for Design Helix."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignProfileBase import (
    create_profile_design_operation,
    set_exact_axis_link,
    verify_exact_link,
)
from VibeCADNativeDesignProfileInput import (
    axis_spec,
    preflight_profile_inputs,
    profile_spec,
)
from VibeCADNativeDesignReferences import DesignLinkSpec
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_MODES = {
    "pitch_height_angle": "pitch-height-angle",
    "pitch_turns_angle": "pitch-turns-angle",
    "height_turns_angle": "height-turns-angle",
    "height_turns_growth": "height-turns-growth",
}
_MODE_PROPERTIES = {
    "pitch_height_angle": ("Pitch", "Height", "Angle"),
    "pitch_turns_angle": ("Pitch", "Turns", "Angle"),
    "height_turns_angle": ("Height", "Turns", "Angle"),
    "height_turns_growth": ("Height", "Turns", "Growth"),
}
_INPUT_NAMES = {
    "pitch_mm": "Pitch",
    "height_mm": "Height",
    "turns": "Turns",
    "angle_degrees": "Angle",
    "growth_mm": "Growth",
}
_MODE_INPUT_FIELDS = {
    "pitch_height_angle": frozenset(
        {"kind", "pitch_mm", "height_mm", "angle_degrees"}
    ),
    "pitch_turns_angle": frozenset(
        {"kind", "pitch_mm", "turns", "angle_degrees"}
    ),
    "height_turns_angle": frozenset(
        {"kind", "height_mm", "turns", "angle_degrees"}
    ),
    "height_turns_growth": frozenset(
        {"kind", "height_mm", "turns", "growth_mm"}
    ),
}


@dataclass(frozen=True, slots=True)
class DesignHelixSpec:
    profile: DesignLinkSpec
    axis: DesignLinkSpec
    mode: str
    inputs: tuple[tuple[str, float], ...]
    left_handed: bool
    reversed: bool
    outside: bool
    tolerance: float


def prepare_design_helix(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignHelixSpec:
    definition = values["definition"]
    if not isinstance(definition, Mapping):
        raise NativeModelError("A Design Helix definition is invalid.")
    mode = str(definition.get("kind") or "")
    if mode not in _MODES:
        raise NativeModelError("That Design Helix definition is unavailable.")
    if set(definition) != _MODE_INPUT_FIELDS[mode]:
        raise NativeModelError("A Design Helix definition has inconsistent inputs.")
    inputs = tuple(
        (_INPUT_NAMES[name], float(value))
        for name, value in definition.items()
        if name != "kind"
    )
    if {name for name, _value in inputs} != set(_MODE_PROPERTIES[mode]):
        raise NativeModelError("A Design Helix definition has inconsistent inputs.")
    tolerance = float(values["tolerance"])
    if not all(math.isfinite(value) for _name, value in inputs) or not math.isfinite(
        tolerance
    ):
        raise NativeModelError("Design Helix parameters must be finite.")
    return DesignHelixSpec(
        profile_spec(document_uid, values["profile"]),
        axis_spec(document_uid, values["axis"]),
        mode,
        inputs,
        bool(values["left_handed"]),
        bool(values["reversed"]),
        bool(values["outside"]),
        tolerance,
    )


def preflight_design_helix(document: Any, spec: DesignHelixSpec) -> None:
    preflight_profile_inputs(document, spec.profile, spec.axis)


def _number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def create_design_helix(
    document: Any,
    *,
    label: str,
    spec: DesignHelixSpec,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        axis = set_exact_axis_link(operation, "ReferenceAxis", spec.axis)
        operation.Mode = _MODES[spec.mode]
        for property_name, value in spec.inputs:
            setattr(operation, property_name, value)
        operation.LeftHanded = spec.left_handed
        operation.Reversed = spec.reversed
        operation.Outside = spec.outside
        operation.Tolerance = spec.tolerance
        operation.HasBeenEdited = True
        return {
            "axis": axis,
            "mode": spec.mode,
            "inputs": dict(spec.inputs),
            "left_handed": spec.left_handed,
            "reversed": spec.reversed,
            "outside": spec.outside,
            "tolerance": spec.tolerance,
        }

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        verify_exact_link(operation, "ReferenceAxis", expected["axis"])
        if (
            str(operation.Mode) != _MODES[expected["mode"]]
            or bool(operation.LeftHanded) is not expected["left_handed"]
            or bool(operation.Reversed) is not expected["reversed"]
            or bool(operation.Outside) is not expected["outside"]
            or abs(_number(operation.Tolerance) - expected["tolerance"]) > 1.0e-8
            or not bool(operation.HasBeenEdited)
        ):
            raise NativeModelError("Design Helix parameters changed before commit.")
        for property_name, value in expected["inputs"].items():
            if abs(_number(getattr(operation, property_name)) - value) > 1.0e-8:
                raise NativeModelError("A Design Helix input changed before commit.")
        return {
            "axis": dict(expected["axis"]),
            "mode": expected["mode"],
            "pitch_mm": _number(operation.Pitch),
            "height_mm": _number(operation.Height),
            "turns": _number(operation.Turns),
            "angle_degrees": _number(operation.Angle),
            "growth_mm": _number(operation.Growth),
            "left_handed": bool(operation.LeftHanded),
            "reversed": bool(operation.Reversed),
            "outside": bool(operation.Outside),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignHelix",
        base_name="Helix",
        label=label,
        profile_spec=spec.profile,
        result_spec=result_spec,
        configure_specific=configure,
        verify_specific=verify,
    )
