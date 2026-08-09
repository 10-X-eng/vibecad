# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Surface Filling preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    flatten_link_sub_list,
    link_sub,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset(
    {
        "constraints",
        "initial_face",
        "degree",
        "points_on_curve",
        "iterations",
        "anisotropy",
        "tolerance_2d",
        "tolerance_3d",
        "angular_tolerance",
        "curvature_tolerance",
        "maximum_degree",
        "maximum_segments",
    }
)
_REQUIRED_DEFINITION_FIELDS = frozenset({"constraints"})
_CONSTRAINT_BASE_FIELDS = frozenset({"kind", "object_name", "subelement"})
_EDGE_FIELDS = _CONSTRAINT_BASE_FIELDS | frozenset({"support_face", "continuity"})
_FACE_FIELDS = _CONSTRAINT_BASE_FIELDS | frozenset({"continuity"})
_INITIAL_FACE_FIELDS = frozenset({"object_name", "face"})
_ELEMENT_NAME = re.compile(r"^(Vertex|Edge|Face)[1-9][0-9]*$")
_FACE_NAME = re.compile(r"^Face[1-9][0-9]*$")
_CONTINUITY = {"C0": 0, "G1": 1, "G2": 2}
_DEFAULTS = {
    "degree": 3,
    "points_on_curve": 15,
    "iterations": 2,
    "anisotropy": False,
    "tolerance_2d": 1.0e-5,
    "tolerance_3d": 1.0e-4,
    "angular_tolerance": 0.01,
    "curvature_tolerance": 0.1,
    "maximum_degree": 8,
    "maximum_segments": 9,
}
_INTEGER_BOUNDS = {
    "degree": (2, 25),
    "points_on_curve": (2, 1_000),
    "iterations": (1, 1_000),
    "maximum_degree": (2, 25),
    "maximum_segments": (1, 10_000),
}
_NUMBER_BOUNDS = {
    "tolerance_2d": (0.0, 1_000.0),
    "tolerance_3d": (0.0, 1_000.0),
    "angular_tolerance": (0.0, math.pi),
    "curvature_tolerance": (0.0, 1_000.0),
}


@dataclass(frozen=True, slots=True)
class SurfaceFillingConstraint:
    kind: str
    object_ref: NativeObjectRef
    subelement: str
    support_face: str | None
    continuity: str


@dataclass(frozen=True, slots=True)
class SurfaceFillingSpec:
    constraints: tuple[SurfaceFillingConstraint, ...]
    initial_face: tuple[NativeObjectRef, str] | None
    degree: int
    points_on_curve: int
    iterations: int
    anisotropy: bool
    tolerance_2d: float
    tolerance_3d: float
    angular_tolerance: float
    curvature_tolerance: float
    maximum_degree: int
    maximum_segments: int


@dataclass(frozen=True, slots=True)
class PreparedSurfaceFillingConstraint:
    spec: SurfaceFillingConstraint
    element: CurrentPartElement
    support: CurrentPartElement | None


@dataclass(frozen=True, slots=True)
class PreparedSurfaceFilling:
    spec: SurfaceFillingSpec
    constraints: tuple[PreparedSurfaceFillingConstraint, ...]
    initial_face: CurrentPartElement | None


def _object_ref(document_uid: str, value: Any) -> NativeObjectRef:
    return NativeObjectRef(document_uid, str(value or ""))


def _integer(value: Any, *, field: str) -> int:
    minimum, maximum = _INTEGER_BOUNDS[field]
    if type(value) is not int or not minimum <= value <= maximum:
        raise NativeModelError(
            f"Surface Filling {field} must be an integer from {minimum} to {maximum}."
        )
    return value


def _number(value: Any, *, field: str) -> float:
    minimum, maximum = _NUMBER_BOUNDS[field]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum < float(value) <= maximum
    ):
        raise NativeModelError(
            f"Surface Filling {field} must be greater than zero and at most {maximum}."
        )
    return float(value)


def _constraint(
    document_uid: str,
    value: Any,
    *,
    index: int,
) -> SurfaceFillingConstraint:
    if not isinstance(value, Mapping):
        raise NativeModelError(f"Surface Filling constraint {index} is invalid.")
    kind = str(value.get("kind") or "")
    fields = set(value)
    allowed = {
        "boundary_edge": _EDGE_FIELDS,
        "curve_edge": _EDGE_FIELDS,
        "face": _FACE_FIELDS,
        "point": _CONSTRAINT_BASE_FIELDS,
    }.get(kind)
    if allowed is None or not _CONSTRAINT_BASE_FIELDS <= fields or not fields <= allowed:
        raise NativeModelError(
            f"Surface Filling constraint {index} has invalid fields for its kind."
        )
    subelement = str(value["subelement"] or "")
    match = _ELEMENT_NAME.fullmatch(subelement)
    expected = {
        "boundary_edge": "Edge",
        "curve_edge": "Edge",
        "face": "Face",
        "point": "Vertex",
    }[kind]
    if match is None or match.group(1) != expected:
        raise NativeModelError(
            f"Surface Filling constraint {index} requires one exact {expected}N subelement."
        )
    continuity = str(value.get("continuity", "C0") or "")
    if continuity not in _CONTINUITY:
        raise NativeModelError(
            f"Surface Filling constraint {index} has an invalid continuity."
        )
    support_face = value.get("support_face")
    if support_face is not None:
        support_face = str(support_face or "")
        if _FACE_NAME.fullmatch(support_face) is None:
            raise NativeModelError(
                f"Surface Filling constraint {index} has an invalid support face."
            )
    if kind in {"boundary_edge", "curve_edge"} and support_face is None and continuity != "C0":
        raise NativeModelError(
            f"Surface Filling constraint {index} needs a support face for G1 or G2."
        )
    return SurfaceFillingConstraint(
        kind,
        _object_ref(document_uid, value["object_name"]),
        subelement,
        support_face,
        continuity,
    )


def prepare_surface_filling(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceFillingSpec:
    if (
        not isinstance(value, Mapping)
        or not _REQUIRED_DEFINITION_FIELDS <= set(value)
        or not set(value) <= _DEFINITION_FIELDS
    ):
        raise NativeModelError(
            "A Surface Filling definition requires exact constraints and known controls."
        )
    raw_constraints = value["constraints"]
    if not isinstance(raw_constraints, list) or not 1 <= len(raw_constraints) <= 256:
        raise NativeModelError("Surface Filling requires 1 to 256 exact constraints.")
    constraints = tuple(
        _constraint(document_uid, item, index=index)
        for index, item in enumerate(raw_constraints, start=1)
    )
    if not any(item.kind == "boundary_edge" for item in constraints):
        raise NativeModelError("Surface Filling requires at least one boundary edge.")
    identities = tuple(
        (item.kind, item.object_ref.object_name, item.subelement)
        for item in constraints
    )
    if len(identities) != len(set(identities)):
        raise NativeModelError("Surface Filling constraints must be distinct within each kind.")

    initial_face = None
    if "initial_face" in value:
        raw_initial = value["initial_face"]
        if not isinstance(raw_initial, Mapping) or set(raw_initial) != _INITIAL_FACE_FIELDS:
            raise NativeModelError("A Surface Filling initial face target is invalid.")
        face = str(raw_initial["face"] or "")
        if _FACE_NAME.fullmatch(face) is None:
            raise NativeModelError("A Surface Filling initial face must be exact FaceN.")
        initial_face = (_object_ref(document_uid, raw_initial["object_name"]), face)

    controls = dict(_DEFAULTS)
    for field in _INTEGER_BOUNDS:
        if field in value:
            controls[field] = _integer(value[field], field=field)
    for field in _NUMBER_BOUNDS:
        if field in value:
            controls[field] = _number(value[field], field=field)
    if "anisotropy" in value:
        if type(value["anisotropy"]) is not bool:
            raise NativeModelError("Surface Filling anisotropy must be boolean.")
        controls["anisotropy"] = value["anisotropy"]
    if controls["degree"] > controls["maximum_degree"]:
        raise NativeModelError(
            "Surface Filling degree must not exceed maximum_degree."
        )
    return SurfaceFillingSpec(
        constraints=constraints,
        initial_face=initial_face,
        **controls,
    )


def _resolve_element(
    document: Any,
    reference: NativeObjectRef,
    subelement: str,
    *,
    role: str,
) -> CurrentPartElement:
    from VibeCADNativePartHistory import resolve_current_part_element

    element = resolve_current_part_element(
        document,
        reference,
        subelement=subelement,
        operation=f"Surface Filling {role}",
    )
    derived = getattr(element.target, "isDerivedFrom", None)
    if not callable(derived) or not derived("Part::Feature"):
        raise NativeModelError(
            f"A Surface Filling {role} must resolve to an exact Part feature state."
        )
    return element


def _edge_has_support(edge: CurrentPartElement, face: CurrentPartElement) -> bool:
    try:
        edge_shape = edge.raw_shape or edge.shape
        face_shape = face.raw_shape or face.shape
        return any(
            candidate.isSame(edge_shape) or candidate.isPartner(edge_shape)
            for candidate in face_shape.Edges
        )
    except Exception:
        return False


def _closed_boundary(constraints: tuple[PreparedSurfaceFillingConstraint, ...]) -> bool:
    import Part

    edges = [item.element.shape for item in constraints if item.spec.kind == "boundary_edge"]
    try:
        wire = Part.Wire(edges)
        return (
            wire is not None
            and not wire.isNull()
            and wire.isValid()
            and len(wire.Edges) == len(edges)
            and wire.isClosed()
        )
    except Exception:
        return False


def preflight_surface_filling(
    document: Any,
    spec: SurfaceFillingSpec,
) -> PreparedSurfaceFilling:
    if not isinstance(spec, SurfaceFillingSpec):
        raise TypeError("spec must be a SurfaceFillingSpec")
    prepared = []
    resolved_identities = []
    for index, constraint in enumerate(spec.constraints, start=1):
        element = _resolve_element(
            document,
            constraint.object_ref,
            constraint.subelement,
            role=f"constraint {index}",
        )
        support = None
        if constraint.support_face is not None:
            support = _resolve_element(
                document,
                constraint.object_ref,
                constraint.support_face,
                role=f"constraint {index} support",
            )
            if support.target is not element.target or not _edge_has_support(element, support):
                raise NativeModelError(
                    f"Surface Filling constraint {index} support is not adjacent to its edge."
                )
        prepared.append(PreparedSurfaceFillingConstraint(constraint, element, support))
        resolved_identities.append((constraint.kind, element.target, constraint.subelement))
    if len(resolved_identities) != len(set(resolved_identities)):
        raise NativeModelError(
            "Surface Filling inputs resolve to duplicate current-History constraints."
        )
    prepared_tuple = tuple(prepared)
    if not _closed_boundary(prepared_tuple):
        raise NativeModelError(
            "Surface Filling boundary edges must form one connected closed loop in order."
        )

    initial = None
    if spec.initial_face is not None:
        initial = _resolve_element(
            document,
            spec.initial_face[0],
            spec.initial_face[1],
            role="initial face",
        )
    return PreparedSurfaceFilling(spec, prepared_tuple, initial)


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceFilling) -> bool:
    elements = []
    for constraint in prepared.constraints:
        elements.append(constraint.element)
        if constraint.support is not None:
            elements.append(constraint.support)
    if prepared.initial_face is not None:
        elements.append(prepared.initial_face)
    return all(current_part_element_is_exact(document, element) for element in elements)


def _link_values(
    constraints: tuple[PreparedSurfaceFillingConstraint, ...],
    kind: str,
) -> list[tuple[Any, list[str]]]:
    return [
        (item.element.target, [item.spec.subelement])
        for item in constraints
        if item.spec.kind == kind
    ]


def _edge_metadata(
    constraints: tuple[PreparedSurfaceFillingConstraint, ...],
    kind: str,
) -> tuple[list[str], list[int]]:
    selected = tuple(item for item in constraints if item.spec.kind == kind)
    return (
        [item.spec.support_face or "" for item in selected],
        [_CONTINUITY[item.spec.continuity] for item in selected],
    )


def create_surface_filling(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceFilling,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedSurfaceFilling):
        raise TypeError("prepared must be a PreparedSurfaceFilling")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("A Surface Filling input changed after preflight.")

    result = document.addObject("Surface::Filling", "Surface")
    if result is None or str(getattr(result, "TypeId", "")) != "Surface::Filling":
        raise NativeModelError("The Surface Filling factory returned the wrong object type.")
    result.Label = label
    constraints = prepared.constraints
    result.BoundaryEdges = _link_values(constraints, "boundary_edge")
    result.BoundaryFaces, result.BoundaryOrder = _edge_metadata(
        constraints, "boundary_edge"
    )
    result.UnboundEdges = _link_values(constraints, "curve_edge")
    result.UnboundFaces, result.UnboundOrder = _edge_metadata(
        constraints, "curve_edge"
    )
    face_constraints = tuple(item for item in constraints if item.spec.kind == "face")
    result.FreeFaces = [
        (item.element.target, [item.spec.subelement]) for item in face_constraints
    ]
    result.FreeOrder = [_CONTINUITY[item.spec.continuity] for item in face_constraints]
    result.Points = _link_values(constraints, "point")
    if prepared.initial_face is not None:
        result.InitialFace = (
            prepared.initial_face.target,
            [prepared.initial_face.subelement],
        )

    spec = prepared.spec
    result.Degree = spec.degree
    result.PointsOnCurve = spec.points_on_curve
    result.Iterations = spec.iterations
    result.Anisotropy = spec.anisotropy
    result.Tolerance2d = spec.tolerance_2d
    result.Tolerance3d = spec.tolerance_3d
    result.TolAngular = spec.angular_tolerance
    result.TolCurvature = spec.curvature_tolerance
    result.MaximumDegree = spec.maximum_degree
    result.MaximumSegments = spec.maximum_segments

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
    ):
        status = str(result.getStatusString() or "")
        raise NativeModelError(
            status if status and status != "Valid" else "Surface Filling produced no valid face."
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def _expected_links(
    constraints: tuple[PreparedSurfaceFillingConstraint, ...],
    kind: str,
) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    return tuple(
        (item.element.target, (item.spec.subelement,))
        for item in constraints
        if item.spec.kind == kind
    )


def _controls_are_exact(result: Any, spec: SurfaceFillingSpec) -> bool:
    return (
        int(result.Degree) == spec.degree
        and int(result.PointsOnCurve) == spec.points_on_curve
        and int(result.Iterations) == spec.iterations
        and bool(result.Anisotropy) is spec.anisotropy
        and int(result.MaximumDegree) == spec.maximum_degree
        and int(result.MaximumSegments) == spec.maximum_segments
        and all(
            math.isclose(float(actual), expected, rel_tol=1.0e-12, abs_tol=1.0e-12)
            for actual, expected in (
                (result.Tolerance2d, spec.tolerance_2d),
                (result.Tolerance3d, spec.tolerance_3d),
                (result.TolAngular, spec.angular_tolerance),
                (result.TolCurvature, spec.curvature_tolerance),
            )
        )
    )


def verify_surface_filling(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedSurfaceFilling = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    constraints = prepared.constraints
    boundary_faces, boundary_orders = _edge_metadata(constraints, "boundary_edge")
    curve_faces, curve_orders = _edge_metadata(constraints, "curve_edge")
    face_constraints = tuple(item for item in constraints if item.spec.kind == "face")
    initial_target, initial_subs = link_sub(result.InitialFace)
    expected_initial = (
        (None, ())
        if prepared.initial_face is None
        else (prepared.initial_face.target, (prepared.initial_face.subelement,))
    )
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Surface::Filling"
        or str(result.Label) != draft.value["label"]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
        or flatten_link_sub_list(result.BoundaryEdges)
        != _expected_links(constraints, "boundary_edge")
        or tuple(result.BoundaryFaces) != tuple(boundary_faces)
        or tuple(int(item) for item in result.BoundaryOrder) != tuple(boundary_orders)
        or flatten_link_sub_list(result.UnboundEdges)
        != _expected_links(constraints, "curve_edge")
        or tuple(result.UnboundFaces) != tuple(curve_faces)
        or tuple(int(item) for item in result.UnboundOrder) != tuple(curve_orders)
        or flatten_link_sub_list(result.FreeFaces)
        != _expected_links(constraints, "face")
        or tuple(int(item) for item in result.FreeOrder)
        != tuple(_CONTINUITY[item.spec.continuity] for item in face_constraints)
        or flatten_link_sub_list(result.Points) != _expected_links(constraints, "point")
        or (initial_target, initial_subs) != expected_initial
        or not _controls_are_exact(result, prepared.spec)
    ):
        raise NativeModelError("Surface Filling failed its exact retained postcondition.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError("Surface Filling lost its History or Design identity.")
    PartDesign.validateDesign(result)

    counts = {
        kind: sum(item.spec.kind == kind for item in constraints)
        for kind in ("boundary_edge", "curve_edge", "face", "point")
    }
    return {
        "root": object_reference(result),
        "boundary_edge_count": counts["boundary_edge"],
        "curve_constraint_count": counts["curve_edge"],
        "face_constraint_count": counts["face"],
        "point_constraint_count": counts["point"],
        "has_initial_face": prepared.initial_face is not None,
        "degree": prepared.spec.degree,
        "maximum_degree": prepared.spec.maximum_degree,
        "maximum_segments": prepared.spec.maximum_segments,
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
    }
