# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact profile/path implementation for Design Sweep."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDesignProfileBase import (
    create_profile_design_operation,
    set_exact_link,
    set_exact_link_list,
    verify_exact_link,
)
from VibeCADNativeDesignProfileInput import (
    path_spec,
    preflight_profile_inputs,
    profile_spec,
    vector_from_mapping,
)
from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    property_link_list_summary,
)
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_ORIENTATIONS = {
    "standard": "Standard",
    "fixed": "Fixed",
    "frenet": "Frenet",
    "auxiliary": "Auxiliary",
    "binormal": "Binormal",
}
_TRANSITIONS = {
    "transformed": "Transformed",
    "right_corner": "Right corner",
    "round_corner": "Round corner",
}
_TRANSFORMATIONS = {
    "constant": "Constant",
    "multisection": "Multisection",
    "linear": "Linear",
    "s_shape": "S-shape",
    "interpolation": "Interpolation",
}
_OPTION_FIELDS = frozenset(
    {"spine_tangent", "orientation", "transition", "transformation", "sections"}
)
_ORIENTATION_FIELDS = {
    "standard": frozenset({"kind"}),
    "fixed": frozenset({"kind"}),
    "frenet": frozenset({"kind"}),
    "auxiliary": frozenset({"kind", "spine", "tangent", "curvilinear"}),
    "binormal": frozenset({"kind", "vector"}),
}


@dataclass(frozen=True, slots=True)
class DesignSweepSpec:
    profile: DesignLinkSpec
    path: DesignLinkSpec
    spine_tangent: bool
    orientation: str
    auxiliary_spine: DesignLinkSpec | None
    auxiliary_tangent: bool
    auxiliary_curvilinear: bool
    binormal: tuple[float, float, float] | None
    transition: str
    transformation: str
    sections: tuple[DesignLinkSpec, ...]


def prepare_design_sweep(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignSweepSpec:
    options = values["options"]
    if not isinstance(options, Mapping) or set(options) != _OPTION_FIELDS:
        raise NativeModelError("Design Sweep options are invalid.")
    orientation = options["orientation"]
    if not isinstance(orientation, Mapping):
        raise NativeModelError("Design Sweep orientation is invalid.")
    orientation_kind = str(orientation.get("kind") or "")
    orientation_fields = _ORIENTATION_FIELDS.get(orientation_kind)
    if orientation_fields is None:
        raise NativeModelError("That Design Sweep orientation is unavailable.")
    if set(orientation) != orientation_fields:
        raise NativeModelError(
            "The Design Sweep orientation fields do not match its kind."
        )
    transformation = str(options["transformation"])
    if transformation not in _TRANSFORMATIONS:
        raise NativeModelError("That Design Sweep transformation is unavailable.")
    raw_sections = options["sections"]
    if not isinstance(raw_sections, list) or len(raw_sections) > 32:
        raise NativeModelError("Design Sweep sections are invalid.")
    sections = tuple(profile_spec(document_uid, value) for value in raw_sections)
    if transformation == "multisection" and not sections:
        raise NativeModelError("A multisection Design Sweep requires exact sections.")
    if transformation != "multisection" and sections:
        raise NativeModelError("Only a multisection Design Sweep accepts extra sections.")
    auxiliary = (
        path_spec(document_uid, orientation["spine"])
        if orientation_kind == "auxiliary"
        else None
    )
    binormal = (
        vector_from_mapping(orientation["vector"], label="Design Sweep binormal")
        if orientation_kind == "binormal"
        else None
    )
    transition = str(options["transition"])
    if transition not in _TRANSITIONS:
        raise NativeModelError("That Design Sweep transition is unavailable.")
    return DesignSweepSpec(
        profile_spec(document_uid, values["profile"]),
        path_spec(document_uid, values["path"]),
        bool(options["spine_tangent"]),
        orientation_kind,
        auxiliary,
        bool(orientation.get("tangent", False)),
        bool(orientation.get("curvilinear", True)),
        binormal,
        transition,
        transformation,
        sections,
    )


def preflight_design_sweep(document: Any, spec: DesignSweepSpec) -> None:
    preflight_profile_inputs(
        document,
        spec.profile,
        spec.path,
        spec.auxiliary_spine,
        *spec.sections,
    )


def create_design_sweep(
    document: Any,
    *,
    label: str,
    spec: DesignSweepSpec,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        import FreeCAD as App

        path = set_exact_link(operation, "Spine", spec.path)
        sections = set_exact_link_list(
            operation,
            "Sections",
            spec.sections,
            expected_types=("Part::Part2DObject",),
        )
        operation.SpineTangent = spec.spine_tangent
        operation.Mode = _ORIENTATIONS[spec.orientation]
        operation.Transition = _TRANSITIONS[spec.transition]
        operation.Transformation = _TRANSFORMATIONS[spec.transformation]
        auxiliary = None
        if spec.auxiliary_spine is not None:
            auxiliary = set_exact_link(
                operation,
                "AuxiliarySpine",
                spec.auxiliary_spine,
            )
            operation.AuxiliarySpineTangent = spec.auxiliary_tangent
            operation.AuxiliaryCurvilinear = spec.auxiliary_curvilinear
        if spec.binormal is not None:
            operation.Binormal = App.Vector(*spec.binormal)
        return {
            "path": path,
            "sections": sections,
            "spine_tangent": spec.spine_tangent,
            "orientation": spec.orientation,
            "auxiliary_spine": auxiliary,
            "auxiliary_tangent": spec.auxiliary_tangent,
            "auxiliary_curvilinear": spec.auxiliary_curvilinear,
            "binormal": spec.binormal,
            "transition": spec.transition,
            "transformation": spec.transformation,
        }

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        verify_exact_link(operation, "Spine", expected["path"])
        if expected["auxiliary_spine"] is not None:
            verify_exact_link(operation, "AuxiliarySpine", expected["auxiliary_spine"])
        sections = property_link_list_summary(operation.Sections)
        vector = tuple(
            float(getattr(operation.Binormal, name))
            for name in ("x", "y", "z")
        )
        if (
            sections != expected["sections"]
            or bool(operation.SpineTangent) is not expected["spine_tangent"]
            or str(operation.Mode) != _ORIENTATIONS[expected["orientation"]]
            or str(operation.Transition) != _TRANSITIONS[expected["transition"]]
            or str(operation.Transformation)
            != _TRANSFORMATIONS[expected["transformation"]]
            or (
                expected["auxiliary_spine"] is not None
                and (
                    bool(operation.AuxiliarySpineTangent)
                    is not expected["auxiliary_tangent"]
                    or bool(operation.AuxiliaryCurvilinear)
                    is not expected["auxiliary_curvilinear"]
                )
            )
            or (
                expected["binormal"] is not None
                and any(
                    abs(actual - requested) > 1.0e-8
                    for actual, requested in zip(vector, expected["binormal"])
                )
            )
        ):
            raise NativeModelError("Design Sweep parameters changed before commit.")
        return {
            "path": dict(expected["path"]),
            "orientation": expected["orientation"],
            "transition": expected["transition"],
            "transformation": expected["transformation"],
            "section_count": len(sections),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignSweep",
        base_name="Sweep",
        label=label,
        profile_spec=spec.profile,
        result_spec=result_spec,
        configure_specific=configure,
        verify_specific=verify,
    )
