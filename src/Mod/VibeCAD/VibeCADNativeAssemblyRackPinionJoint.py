# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Rack-and-Pinion coupling over explicit Slider/Revolute prerequisites."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable

from VibeCADNativeAssemblyCoupledJoint import (
    axes_perpendicular,
    joint_components,
    matching_spec_side,
    sides_equal,
)
from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeAssemblyRegularJoint import (
    NativeAssemblyRegularJointError,
    PreparedRegularJoint,
    RegularJointPropertySpec,
    RegularJointSpec,
    apply_regular_joint,
    preflight_regular_joint,
    verify_regular_joint,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    NativeTargetError,
    object_reference,
    resolve_object,
)


MIN_ABS_PITCH_RADIUS_MM = 1.0e-7
MAX_ABS_PITCH_RADIUS_MM = 1_000_000.0


class NativeAssemblyRackPinionJointError(RuntimeError):
    """An exact Rack-and-Pinion request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_RACK_PINION_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RackPinionJointSpec:
    assembly_ref: NativeObjectRef
    rack_connector: JointConnectorSpec
    pinion_connector: JointConnectorSpec
    rack_slider_joint_ref: NativeObjectRef
    pinion_revolute_joint_ref: NativeObjectRef
    label: str
    pitch_radius_mm: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


@dataclass(frozen=True, slots=True)
class PreparedRackPinionJoint:
    regular: PreparedRegularJoint
    rack_slider_joint: Any
    pinion_revolute_joint: Any
    rack_slider_side: int
    pinion_revolute_side: int


def pitch_radius_mm(value: Any, field: str = "pitch_radius_mm") -> float:
    """Return one finite, signed, safely non-zero rack pitch radius."""

    if isinstance(value, bool):
        raise NativeAssemblyRackPinionJointError(
            f"{field} must be a signed pitch radius in mm."
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyRackPinionJointError(
            f"{field} must be a signed pitch radius in mm."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_ABS_PITCH_RADIUS_MM <= abs(number) <= MAX_ABS_PITCH_RADIUS_MM
    ):
        raise NativeAssemblyRackPinionJointError(
            f"{field} magnitude must be from {MIN_ABS_PITCH_RADIUS_MM:g} through "
            f"{MAX_ABS_PITCH_RADIUS_MM:g} mm."
        )
    return number


def _regular_spec(spec: RackPinionJointSpec) -> RegularJointSpec:
    if not isinstance(spec, RackPinionJointSpec):
        raise TypeError("spec must be a RackPinionJointSpec")
    radius = pitch_radius_mm(spec.pitch_radius_mm)
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.rack_connector,
        second=spec.pinion_connector,
        joint_type="RackPinion",
        type_index=9,
        label=spec.label,
        reverse=False,
        properties=(RegularJointPropertySpec("Distance", radius),),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _resolve_dependency(
    document: Any,
    reference: NativeObjectRef,
    field: str,
) -> Any:
    try:
        return resolve_object(document, reference)
    except NativeTargetError as exc:
        raise NativeAssemblyRackPinionJointError(
            f"The exact {field} prerequisite changed; read current Assemble state "
            "and retry."
        ) from exc


def _validate_dependencies(
    prepared: PreparedRegularJoint,
    spec: RackPinionJointSpec,
    slider_joint: Any,
    revolute_joint: Any,
) -> tuple[int, int]:
    active = set(prepared.regular_joints_before)
    if (
        slider_joint is revolute_joint
        or slider_joint not in active
        or revolute_joint not in active
        or str(getattr(slider_joint, "JointType", "") or "") != "Slider"
        or str(getattr(revolute_joint, "JointType", "") or "") != "Revolute"
    ):
        raise NativeAssemblyRackPinionJointError(
            "Rack-and-Pinion requires exact active Slider and Revolute prerequisite "
            "joints in the human-active Assembly."
        )
    rack = prepared.first.component
    pinion = prepared.second.component
    grounded = {
        getattr(joint, "ObjectToGround", None)
        for joint in prepared.grounded_joints_before
    }
    if rack in grounded or pinion in grounded:
        raise NativeAssemblyRackPinionJointError(
            "Rack and pinion components must retain their Slider and Revolute "
            "degrees of freedom rather than being grounded."
        )
    if pinion in joint_components(slider_joint) or rack in joint_components(
        revolute_joint
    ):
        raise NativeAssemblyRackPinionJointError(
            "Rack-and-Pinion prerequisite joints must constrain distinct rack and "
            "pinion components."
        )
    slider_side = matching_spec_side(
        slider_joint,
        rack,
        spec.rack_connector,
    )
    revolute_side = matching_spec_side(
        revolute_joint,
        pinion,
        spec.pinion_connector,
    )
    if not slider_side or not revolute_side:
        raise NativeAssemblyRackPinionJointError(
            "Rack and pinion connectors must exactly reuse the named Slider and "
            "Revolute joint coordinate systems."
        )
    if not axes_perpendicular(
        slider_joint,
        slider_side,
        revolute_joint,
        revolute_side,
    ):
        raise NativeAssemblyRackPinionJointError(
            "The rack Slider axis must be perpendicular to the pinion Revolute axis."
        )
    return slider_side, revolute_side


def rack_pinion_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    """Derive exact prerequisite identities from persisted connector equality."""

    candidates = tuple(item for item in active_joints if item is not joint)
    for rack_side, pinion_side in ((1, 2), (2, 1)):
        sliders = [
            item
            for item in candidates
            if str(getattr(item, "JointType", "") or "") == "Slider"
            and any(sides_equal(joint, rack_side, item, side) for side in (1, 2))
        ]
        revolutes = [
            item
            for item in candidates
            if str(getattr(item, "JointType", "") or "") == "Revolute"
            and any(sides_equal(joint, pinion_side, item, side) for side in (1, 2))
        ]
        if len(sliders) == 1 and len(revolutes) == 1:
            slider_side = next(
                side
                for side in (1, 2)
                if sides_equal(joint, rack_side, sliders[0], side)
            )
            revolute_side = next(
                side
                for side in (1, 2)
                if sides_equal(joint, pinion_side, revolutes[0], side)
            )
            return {
                "rack_slider_joint": object_reference(sliders[0]),
                "pinion_revolute_joint": object_reference(revolutes[0]),
                "axes_perpendicular": axes_perpendicular(
                    sliders[0],
                    slider_side,
                    revolutes[0],
                    revolute_side,
                ),
            }
    return None


def _rack_pinion_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyRackPinionJointError:
    return NativeAssemblyRackPinionJointError(str(exc))


def preflight_rack_pinion_joint(
    document: Any,
    spec: RackPinionJointSpec,
    **kwargs: Any,
) -> PreparedRackPinionJoint:
    try:
        regular = preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _rack_pinion_failure(exc) from exc
    slider = _resolve_dependency(
        document,
        spec.rack_slider_joint_ref,
        "rack Slider joint",
    )
    revolute = _resolve_dependency(
        document,
        spec.pinion_revolute_joint_ref,
        "pinion Revolute joint",
    )
    slider_side, revolute_side = _validate_dependencies(
        regular,
        spec,
        slider,
        revolute,
    )
    return PreparedRackPinionJoint(
        regular,
        slider,
        revolute,
        slider_side,
        revolute_side,
    )


def apply_rack_pinion_joint(
    document: Any,
    spec: RackPinionJointSpec,
    *,
    joint_factory: Callable[[Any, Any, RackPinionJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    prepared = preflight_rack_pinion_joint(document, spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = (
            lambda assembly, joint_group, _spec: joint_factory(
                assembly,
                joint_group,
                spec,
            )
        )
    try:
        draft = apply_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _rack_pinion_failure(exc) from exc
    draft.value["rack_slider_joint"] = prepared.rack_slider_joint
    draft.value["pinion_revolute_joint"] = prepared.pinion_revolute_joint
    draft.value["rack_slider_side"] = prepared.rack_slider_side
    draft.value["pinion_revolute_side"] = prepared.pinion_revolute_side
    draft.value["rack_pinion_spec"] = spec
    return draft


def verify_rack_pinion_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _rack_pinion_failure(exc) from exc
    value = draft.value
    spec = value.get("rack_pinion_spec")
    joint = value["joint"]
    slider = value["rack_slider_joint"]
    revolute = value["pinion_revolute_joint"]
    if (
        not isinstance(spec, RackPinionJointSpec)
        or document.getObject(str(slider.Name)) is not slider
        or document.getObject(str(revolute.Name)) is not revolute
        or str(getattr(slider, "JointType", "") or "") != "Slider"
        or str(getattr(revolute, "JointType", "") or "") != "Revolute"
        or not sides_equal(joint, 1, slider, value["rack_slider_side"])
        or not sides_equal(joint, 2, revolute, value["pinion_revolute_side"])
        or not axes_perpendicular(
            slider,
            value["rack_slider_side"],
            revolute,
            value["pinion_revolute_side"],
        )
    ):
        raise NativeAssemblyRackPinionJointError(
            "The native Rack-and-Pinion joint changed its exact prerequisite graph."
        )
    properties = result.pop("properties")
    radius = float(properties["Distance"])
    connectors = result.pop("connectors")
    result.pop("reverse")
    result["rack_connector"] = connectors[0]
    result["pinion_connector"] = connectors[1]
    result["rack_slider_joint"] = object_reference(slider)
    result["pinion_revolute_joint"] = object_reference(revolute)
    result["pitch_radius_mm"] = radius
    result["rack_travel_mm_per_pinion_radian"] = -radius
    result["axes_perpendicular"] = True
    return result
