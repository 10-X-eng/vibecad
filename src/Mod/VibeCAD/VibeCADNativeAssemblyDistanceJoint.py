# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact geometry-aware Distance-joint contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import (
    JointConnectorSpec,
    ResolvedJointConnector,
)
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
from VibeCADNativeTargets import NativeObjectRef


MIN_DISTANCE_MM = -1_000_000.0
MAX_DISTANCE_MM = 1_000_000.0

_FACE_KINDS = ("plane", "cylinder", "cone", "torus", "sphere")
_FACE_MODES = frozenset(
    f"{first}_{second}"
    for index, first in enumerate(_FACE_KINDS)
    for second in _FACE_KINDS[index:]
)
DISTANCE_MODES = frozenset(
    {
        "point_point",
        "line_line",
        "line_circle",
        "circle_circle",
        "point_plane",
        "point_cylinder",
        "point_cone",
        "point_torus",
        "point_sphere",
        "line_plane",
        "line_cylinder",
        "line_cone",
        "line_torus",
        "line_sphere",
        "curve_plane",
        "curve_cylinder",
        "curve_cone",
        "curve_torus",
        "curve_sphere",
        "point_line",
        "point_curve",
        "other",
        *_FACE_MODES,
    }
)


class NativeAssemblyDistanceJointError(RuntimeError):
    """An exact Distance-joint request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_DISTANCE_JOINT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class DistanceJointSpec:
    assembly_ref: NativeObjectRef
    first: JointConnectorSpec
    second: JointConnectorSpec
    label: str
    reverse: bool
    distance_mm: float
    expected_distance_mode: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int
    expected_solve_on_creation: bool


@dataclass(frozen=True, slots=True)
class PreparedDistanceJoint:
    regular: PreparedRegularJoint
    canonical_spec: DistanceJointSpec
    distance_mode: str


def distance_mm(value: Any, field: str = "distance_mm") -> float:
    """Return one finite signed Distance value in the Native envelope."""

    if isinstance(value, bool):
        raise NativeAssemblyDistanceJointError(f"{field} must be a distance in mm.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyDistanceJointError(
            f"{field} must be a distance in mm."
        ) from exc
    if not (
        math.isfinite(number)
        and MIN_DISTANCE_MM <= number <= MAX_DISTANCE_MM
    ):
        raise NativeAssemblyDistanceJointError(
            f"{field} must be from {MIN_DISTANCE_MM:g} through "
            f"{MAX_DISTANCE_MM:g} mm."
        )
    return number


def _distance_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if mode not in DISTANCE_MODES:
        raise NativeAssemblyDistanceJointError(
            "expected_distance_mode is not a supported exact Distance geometry mode."
        )
    return mode


def _regular_spec(spec: DistanceJointSpec) -> RegularJointSpec:
    if not isinstance(spec, DistanceJointSpec):
        raise TypeError("spec must be a DistanceJointSpec")
    value = distance_mm(spec.distance_mm)
    _distance_mode(spec.expected_distance_mode)
    return RegularJointSpec(
        assembly_ref=spec.assembly_ref,
        first=spec.first,
        second=spec.second,
        joint_type="Distance",
        type_index=5,
        label=spec.label,
        reverse=spec.reverse,
        properties=(RegularJointPropertySpec("Distance", value),),
        expected_component_count=spec.expected_component_count,
        expected_grounded_count=spec.expected_grounded_count,
        expected_joint_count=spec.expected_joint_count,
        expected_solve_on_creation=spec.expected_solve_on_creation,
    )


def _element_kind(element: Any | None) -> tuple[str, str]:
    shape_type = str(getattr(element, "ShapeType", "") or "")
    if shape_type == "Vertex":
        return "point", "point"
    if shape_type == "Edge":
        curve_type = str(getattr(getattr(element, "Curve", None), "TypeId", "") or "")
        if curve_type == "Part::GeomLine":
            return "edge", "line"
        if curve_type == "Part::GeomCircle":
            return "edge", "circle"
        return "edge", "curve"
    if shape_type == "Face":
        surface_type = str(
            getattr(getattr(element, "Surface", None), "TypeId", "") or ""
        )
        surfaces = {
            "Part::GeomPlane": "plane",
            "Part::GeomCylinder": "cylinder",
            "Part::GeomCone": "cone",
            "Part::GeomTorus": "torus",
            "Part::GeomSphere": "sphere",
        }
        return "face", surfaces.get(surface_type, "surface")
    return "other", "other"


def _mode_and_swap(
    first: tuple[str, str],
    second: tuple[str, str],
) -> tuple[str, bool]:
    first_topology, first_kind = first
    second_topology, second_kind = second
    if first_topology == second_topology == "point":
        return "point_point", False
    if first_topology == second_topology == "edge":
        if "line" in {first_kind, second_kind}:
            swap = first_kind != "line"
            other = first_kind if swap else second_kind
            if other == "line":
                return "line_line", swap
            if other == "circle":
                return "line_circle", swap
            return "other", swap
        if "circle" in {first_kind, second_kind}:
            swap = first_kind != "circle"
            other = first_kind if swap else second_kind
            if other == "circle":
                return "circle_circle", swap
            return "other", swap
        return "other", False
    if first_topology == second_topology == "face":
        for preferred in _FACE_KINDS:
            if preferred not in {first_kind, second_kind}:
                continue
            swap = first_kind != preferred
            other = first_kind if swap else second_kind
            if other in _FACE_KINDS:
                return f"{preferred}_{other}", swap
            return "other", swap
        return "other", False
    topologies = {first_topology, second_topology}
    if topologies == {"point", "face"}:
        face_kind = first_kind if first_topology == "face" else second_kind
        mode = f"point_{face_kind}" if face_kind in _FACE_KINDS else "other"
        return mode, first_topology == "point"
    if topologies == {"edge", "face"}:
        edge_kind = first_kind if first_topology == "edge" else second_kind
        face_kind = first_kind if first_topology == "face" else second_kind
        prefix = "line" if edge_kind == "line" else "curve"
        mode = f"{prefix}_{face_kind}" if face_kind in _FACE_KINDS else "other"
        return mode, first_topology == "edge"
    if topologies == {"point", "edge"}:
        edge_kind = first_kind if first_topology == "edge" else second_kind
        mode = "point_line" if edge_kind == "line" else "point_curve"
        return mode, first_topology == "point"
    return "other", False


def distance_mode_for_resolved(
    first: ResolvedJointConnector,
    second: ResolvedJointConnector,
) -> tuple[str, bool]:
    """Return the live C++ Distance mode and whether connectors need canonical order."""

    return _mode_and_swap(
        _element_kind(first.selected_element),
        _element_kind(second.selected_element),
    )


def _reference_element(reference: Any) -> Any | None:
    try:
        import UtilsAssembly

        obj = UtilsAssembly.getObject(reference)
        path = str(reference[1][0] or "")
        element_name = path.rstrip(".").rsplit(".", 1)[-1] if path else ""
        if obj is None or not element_name:
            return None
        return obj.Shape.getElement(element_name)
    except Exception:
        return None


def distance_mode_from_joint(joint: Any) -> str:
    """Read a Distance mode without invoking the mutating C++ canonicalizer."""

    mode, _swap = _mode_and_swap(
        _element_kind(_reference_element(joint.Reference1)),
        _element_kind(_reference_element(joint.Reference2)),
    )
    return mode


def _distance_failure(
    exc: NativeAssemblyRegularJointError,
) -> NativeAssemblyDistanceJointError:
    return NativeAssemblyDistanceJointError(str(exc))


def preflight_distance_joint(
    document: Any,
    spec: DistanceJointSpec,
    **kwargs: Any,
) -> PreparedDistanceJoint:
    try:
        regular = preflight_regular_joint(document, _regular_spec(spec), **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _distance_failure(exc) from exc
    mode, swap = distance_mode_for_resolved(regular.first, regular.second)
    if mode != spec.expected_distance_mode:
        raise NativeAssemblyDistanceJointError(
            "The exact Distance connector geometry mode changed; read current "
            "geometry state and retry."
        )
    canonical = replace(spec, first=spec.second, second=spec.first) if swap else spec
    return PreparedDistanceJoint(regular, canonical, mode)


def apply_distance_joint(
    document: Any,
    spec: DistanceJointSpec,
    *,
    joint_factory: Callable[[Any, Any, DistanceJointSpec], Any] | None = None,
) -> NativeMutationDraft:
    prepared = preflight_distance_joint(document, spec)
    regular = _regular_spec(prepared.canonical_spec)
    kwargs: dict[str, Any] = {}
    if joint_factory is not None:
        kwargs["joint_factory"] = (
            lambda assembly, joint_group, _spec: joint_factory(
                assembly,
                joint_group,
                prepared.canonical_spec,
            )
        )
    try:
        draft = apply_regular_joint(document, regular, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _distance_failure(exc) from exc
    draft.value["distance_mode"] = prepared.distance_mode
    return draft


def verify_distance_joint(
    document: Any,
    draft: NativeMutationDraft,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = verify_regular_joint(document, draft, **kwargs)
    except NativeAssemblyRegularJointError as exc:
        raise _distance_failure(exc) from exc
    mode = distance_mode_from_joint(draft.value["joint"])
    if mode != draft.value["distance_mode"]:
        raise NativeAssemblyDistanceJointError(
            "The native Distance joint changed its exact geometry mode."
        )
    properties = result.pop("properties")
    result["distance_mm"] = float(properties["Distance"])
    result["distance_mode"] = mode
    return result
