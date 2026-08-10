# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Screw coupling over explicit Slider/Revolute prerequisites."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable

from VibeCADNativeAssemblyCoupledJoint import (
    axes_collinear_directed,
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


MIN_ABS_THREAD_PITCH_MM = 1.0e-7
MAX_ABS_THREAD_PITCH_MM = 1_000_000.0


class NativeAssemblyScrewJointError(RuntimeError):
    """An exact Screw request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_SCREW_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class ScrewJointSpec:
    assembly_ref: NativeObjectRef
    slider_connector: JointConnectorSpec
    screw_connector: JointConnectorSpec
    slider_joint_ref: NativeObjectRef
    screw_revolute_joint_ref: NativeObjectRef
    label: str
    thread_pitch_mm: float
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


@dataclass(frozen=True, slots=True)
class PreparedScrewJoint:
    regular: PreparedRegularJoint
    slider_joint: Any
    screw_revolute_joint: Any
    slider_side: int
    screw_revolute_side: int


def thread_pitch_mm(value: Any, field: str = "thread_pitch_mm") -> float:
    """Return one finite, signed, safely non-zero thread pitch."""

    if isinstance(value, bool):
        raise NativeAssemblyScrewJointError(
            f"{field} must be a signed thread pitch in mm per revolution."
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyScrewJointError(
            f"{field} must be a signed thread pitch in mm per revolution."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_ABS_THREAD_PITCH_MM <= abs(number) <= MAX_ABS_THREAD_PITCH_MM
    ):
        raise NativeAssemblyScrewJointError(
            f"{field} magnitude must be from {MIN_ABS_THREAD_PITCH_MM:g} through "
            f"{MAX_ABS_THREAD_PITCH_MM:g} mm per revolution."
        )
    return number


def _regular_spec(spec: ScrewJointSpec) -> RegularJointSpec:
    if not isinstance(spec, ScrewJointSpec):
        raise TypeError("spec must be a ScrewJointSpec")
    pitch = thread_pitch_mm(spec.thread_pitch_mm)
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.slider_connector,
        second=spec.screw_connector,
        joint_type="Screw",
        type_index=10,
        label=spec.label,
        reverse=False,
        properties=(RegularJointPropertySpec("Distance", pitch),),
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
        raise NativeAssemblyScrewJointError(
            f"The exact {field} prerequisite changed; read current Assemble state "
            "and retry."
        ) from exc


def _validate_dependencies(
    prepared: PreparedRegularJoint,
    spec: ScrewJointSpec,
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
        raise NativeAssemblyScrewJointError(
            "Screw coupling requires exact active Slider and Revolute prerequisite "
            "joints in the human-active Assembly."
        )
    slider_component = prepared.first.component
    screw_component = prepared.second.component
    grounded = {
        getattr(joint, "ObjectToGround", None)
        for joint in prepared.grounded_joints_before
    }
    if slider_component in grounded or screw_component in grounded:
        raise NativeAssemblyScrewJointError(
            "Slider and screw components must retain their translational and "
            "rotational degrees of freedom rather than being grounded."
        )
    if screw_component in joint_components(
        slider_joint
    ) or slider_component in joint_components(revolute_joint):
        raise NativeAssemblyScrewJointError(
            "Screw prerequisite joints must constrain distinct translating and "
            "rotating components."
        )
    slider_side = matching_spec_side(
        slider_joint,
        slider_component,
        spec.slider_connector,
    )
    revolute_side = matching_spec_side(
        revolute_joint,
        screw_component,
        spec.screw_connector,
    )
    if not slider_side or not revolute_side:
        raise NativeAssemblyScrewJointError(
            "Slider and screw connectors must exactly reuse the named Slider and "
            "Revolute joint coordinate systems."
        )
    if not axes_collinear_directed(
        slider_joint,
        slider_side,
        revolute_joint,
        revolute_side,
    ):
        raise NativeAssemblyScrewJointError(
            "The Slider and screw Revolute axes must share one directed collinear axis."
        )
    return slider_side, revolute_side


def screw_dependency_summary(
    joint: Any,
    active_joints: Iterable[Any],
) -> dict[str, Any] | None:
    """Derive exact Screw prerequisite identities from persisted connectors."""

    candidates = tuple(item for item in active_joints if item is not joint)
    for slider_side, screw_side in ((1, 2), (2, 1)):
        sliders = [
            item
            for item in candidates
            if str(getattr(item, "JointType", "") or "") == "Slider"
            and any(sides_equal(joint, slider_side, item, side) for side in (1, 2))
        ]
        revolutes = [
            item
            for item in candidates
            if str(getattr(item, "JointType", "") or "") == "Revolute"
            and any(sides_equal(joint, screw_side, item, side) for side in (1, 2))
        ]
        if len(sliders) == 1 and len(revolutes) == 1:
            matching_slider_side = next(
                side
                for side in (1, 2)
                if sides_equal(joint, slider_side, sliders[0], side)
            )
            matching_revolute_side = next(
                side
                for side in (1, 2)
                if sides_equal(joint, screw_side, revolutes[0], side)
            )
            return {
                "slider_joint": object_reference(sliders[0]),
                "screw_revolute_joint": object_reference(revolutes[0]),
                "axes_collinear": axes_collinear_directed(
                    sliders[0],
                    matching_slider_side,
                    revolutes[0],
                    matching_revolute_side,
                ),
            }
    return None


def _screw_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyScrewJointError:
    return NativeAssemblyScrewJointError(str(exc))


def preflight_screw_joint(
    document: Any,
    spec: ScrewJointSpec,
    **kwargs: Any,
) -> PreparedScrewJoint:
    try:
        regular = preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _screw_failure(exc) from exc
    slider = _resolve_dependency(document, spec.slider_joint_ref, "Slider joint")
    revolute = _resolve_dependency(
        document,
        spec.screw_revolute_joint_ref,
        "screw Revolute joint",
    )
    slider_side, revolute_side = _validate_dependencies(
        regular,
        spec,
        slider,
        revolute,
    )
    return PreparedScrewJoint(
        regular,
        slider,
        revolute,
        slider_side,
        revolute_side,
    )


def apply_screw_joint(
    document: Any,
    spec: ScrewJointSpec,
    *,
    joint_factory: Callable[[Any, Any, ScrewJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    prepared = preflight_screw_joint(document, spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = lambda assembly, joint_group, _spec: joint_factory(
            assembly,
            joint_group,
            spec,
        )
    try:
        draft = apply_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _screw_failure(exc) from exc
    draft.value["slider_joint"] = prepared.slider_joint
    draft.value["screw_revolute_joint"] = prepared.screw_revolute_joint
    draft.value["slider_side"] = prepared.slider_side
    draft.value["screw_revolute_side"] = prepared.screw_revolute_side
    draft.value["screw_spec"] = spec
    return draft


def verify_screw_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _screw_failure(exc) from exc
    value = draft.value
    spec = value.get("screw_spec")
    joint = value["joint"]
    slider = value["slider_joint"]
    revolute = value["screw_revolute_joint"]
    if (
        not isinstance(spec, ScrewJointSpec)
        or document.getObject(str(slider.Name)) is not slider
        or document.getObject(str(revolute.Name)) is not revolute
        or str(getattr(slider, "JointType", "") or "") != "Slider"
        or str(getattr(revolute, "JointType", "") or "") != "Revolute"
        or not sides_equal(joint, 1, slider, value["slider_side"])
        or not sides_equal(joint, 2, revolute, value["screw_revolute_side"])
        or not axes_collinear_directed(
            slider,
            value["slider_side"],
            revolute,
            value["screw_revolute_side"],
        )
    ):
        raise NativeAssemblyScrewJointError(
            "The native Screw joint changed its exact prerequisite graph."
        )
    properties = result.pop("properties")
    pitch = float(properties["Distance"])
    connectors = result.pop("connectors")
    result.pop("reverse")
    result["slider_connector"] = connectors[0]
    result["screw_connector"] = connectors[1]
    result["slider_joint"] = object_reference(slider)
    result["screw_revolute_joint"] = object_reference(revolute)
    result["thread_pitch_mm"] = pitch
    result["relative_axial_advance_mm_per_revolution"] = pitch
    result["slider_travel_mm_per_screw_revolution"] = -pitch
    result["axes_collinear"] = True
    return result
