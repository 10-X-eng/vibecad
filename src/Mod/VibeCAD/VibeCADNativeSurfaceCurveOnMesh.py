# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Curve on Mesh preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from VibeCADNativeMeshState import mesh_geometry_sha256
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_FIELDS = frozenset(
    {
        "object_name",
        "anchors",
        "closed",
        "approximate",
        "maximum_degree",
        "continuity",
        "tolerance",
        "split_angle_degrees",
    }
)
_REQUIRED_FIELDS = frozenset({"object_name", "anchors"})
_ANCHOR_FIELDS = frozenset({"origin_mm", "direction"})
_CONTINUITIES = frozenset({"C0", "C1", "C2", "C3"})
_VECTOR_EPSILON = 1.0e-12
_WEIGHT_EPSILON = 1.0e-6


@dataclass(frozen=True, slots=True)
class SurfaceMeshPickRay:
    origin_mm: tuple[float, float, float]
    direction: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SurfaceCurveOnMeshSpec:
    object_ref: NativeObjectRef
    anchors: tuple[SurfaceMeshPickRay, ...]
    closed: bool
    approximate: bool
    maximum_degree: int
    continuity: str
    tolerance: float
    split_angle_degrees: float


@dataclass(frozen=True, slots=True)
class PreparedSurfaceCurveOnMesh:
    spec: SurfaceCurveOnMeshSpec
    source: Any
    facets: tuple[int, ...]
    weights: tuple[tuple[float, float, float], ...]
    projection_directions: tuple[tuple[float, float, float], ...]
    intersections_mm: tuple[tuple[float, float, float], ...]
    source_fingerprint: str


def _number(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise NativeModelError(f"Curve on Mesh {name} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise NativeModelError(f"Curve on Mesh {name} is outside its finite range.")
    return number


def _boolean(value: Any, *, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Curve on Mesh {name} must be boolean.")
    return value


def _vector(value: Any, *, name: str, normalize: bool) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NativeModelError(f"Curve on Mesh {name} must contain three numbers.")
    if any(type(component) not in (int, float) for component in value):
        raise NativeModelError(f"Curve on Mesh {name} must contain three numbers.")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise NativeModelError(f"Curve on Mesh {name} must be finite.")
    length = math.sqrt(sum(component * component for component in result))
    if normalize:
        if length <= _VECTOR_EPSILON:
            raise NativeModelError(f"Curve on Mesh {name} must be nonzero.")
        result = tuple(component / length for component in result)
    return result


def prepare_surface_curve_on_mesh(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceCurveOnMeshSpec:
    if (
        not isinstance(value, Mapping)
        or not _REQUIRED_FIELDS <= set(value)
        or not set(value) <= _FIELDS
    ):
        raise NativeModelError(
            "Curve on Mesh requires one exact mesh, ordered anchors, and known controls."
        )
    raw_anchors = value["anchors"]
    if not isinstance(raw_anchors, (list, tuple)) or not 2 <= len(raw_anchors) <= 64:
        raise NativeModelError("Curve on Mesh requires 2 to 64 ordered pick rays.")
    anchors = []
    for index, raw in enumerate(raw_anchors):
        if not isinstance(raw, Mapping) or set(raw) != _ANCHOR_FIELDS:
            raise NativeModelError(
                "Every Curve on Mesh anchor requires only origin_mm and direction."
            )
        anchors.append(
            SurfaceMeshPickRay(
                _vector(raw["origin_mm"], name=f"anchor {index + 1} origin", normalize=False),
                _vector(raw["direction"], name=f"anchor {index + 1} direction", normalize=True),
            )
        )
    maximum_degree = value.get("maximum_degree", 5)
    if type(maximum_degree) is not int or not 1 <= maximum_degree <= 8:
        raise NativeModelError("Curve on Mesh maximum_degree must be an integer from 1 to 8.")
    continuity = str(value.get("continuity", "C2") or "")
    if continuity not in _CONTINUITIES:
        raise NativeModelError("Curve on Mesh continuity must be C0, C1, C2, or C3.")
    return SurfaceCurveOnMeshSpec(
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
        tuple(anchors),
        _boolean(value.get("closed", False), name="closed"),
        _boolean(value.get("approximate", True), name="approximate"),
        maximum_degree,
        continuity,
        _number(value.get("tolerance", 0.2), name="tolerance", minimum=0.001, maximum=10.0),
        _number(
            value.get("split_angle_degrees", 45.0),
            name="split_angle_degrees",
            minimum=5.0,
            maximum=180.0,
        ),
    )


def _tuple3(value: Any) -> tuple[float, float, float]:
    try:
        return float(value.x), float(value.y), float(value.z)
    except AttributeError:
        return tuple(float(component) for component in value)


def _subtract(
    left: Sequence[float],
    right: Sequence[float],
) -> tuple[float, float, float]:
    return tuple(float(a) - float(b) for a, b in zip(left, right, strict=True))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def _barycentric(
    point: Sequence[float],
    triangle: Sequence[Sequence[float]],
) -> tuple[float, float, float]:
    first, second, third = triangle
    edge0 = _subtract(second, first)
    edge1 = _subtract(third, first)
    offset = _subtract(point, first)
    dot00 = _dot(edge0, edge0)
    dot01 = _dot(edge0, edge1)
    dot11 = _dot(edge1, edge1)
    dot20 = _dot(offset, edge0)
    dot21 = _dot(offset, edge1)
    denominator = dot00 * dot11 - dot01 * dot01
    if not math.isfinite(denominator) or abs(denominator) <= _VECTOR_EPSILON:
        raise NativeModelError("Curve on Mesh selected a degenerate source facet.")
    second_weight = (dot11 * dot20 - dot01 * dot21) / denominator
    third_weight = (dot00 * dot21 - dot01 * dot20) / denominator
    weights = (1.0 - second_weight - third_weight, second_weight, third_weight)
    if any(
        not math.isfinite(weight)
        or weight < -_WEIGHT_EPSILON
        or weight > 1.0 + _WEIGHT_EPSILON
        for weight in weights
    ):
        raise NativeModelError("Curve on Mesh resolved an anchor outside its source facet.")
    clamped = tuple(min(1.0, max(0.0, weight)) for weight in weights)
    total = sum(clamped)
    return tuple(weight / total for weight in clamped)


def _local_direction(mesh: Any, direction: Sequence[float]) -> tuple[float, float, float]:
    import FreeCAD as App

    try:
        local = mesh.Placement.inverse().Rotation.multVec(App.Vector(*direction))
        return _vector(_tuple3(local), name="local projection direction", normalize=True)
    except Exception as exc:
        raise NativeModelError(
            "Curve on Mesh could not transform a projection direction into mesh coordinates."
        ) from exc


def _is_active_mesh(source: Any) -> bool:
    import MeshGui

    mesh = getattr(source, "Mesh", None)
    try:
        return (
            bool(source.isDerivedFrom("Mesh::Feature"))
            and bool(MeshGui.isNativeMeshInputActive(source))
            and int(mesh.CountFacets) > 0
        )
    except Exception:
        return False


def _mesh_fingerprint(mesh: Any) -> str:
    """Hash complete world-space topology and segment membership."""
    try:
        return mesh_geometry_sha256(mesh)
    except Exception as exc:
        raise NativeModelError("Curve on Mesh could not fingerprint its exact source mesh.") from exc


def preflight_surface_curve_on_mesh(
    document: Any,
    spec: SurfaceCurveOnMeshSpec,
) -> PreparedSurfaceCurveOnMesh:
    if not isinstance(spec, SurfaceCurveOnMeshSpec):
        raise TypeError("spec must be a SurfaceCurveOnMeshSpec")
    source = resolve_object(document, spec.object_ref, expected_types=("Mesh::Feature",))
    if not _is_active_mesh(source):
        raise NativeModelError("Curve on Mesh requires one nonempty current-History mesh.")
    mesh = source.Mesh
    source_fingerprint = _mesh_fingerprint(mesh)
    facets = []
    weights = []
    intersections = []
    for index, anchor in enumerate(spec.anchors):
        try:
            hit = dict(mesh.nearestFacetOnRay(anchor.origin_mm, anchor.direction))
        except Exception as exc:
            raise NativeModelError(
                f"Curve on Mesh anchor {index + 1} could not be projected onto the mesh."
            ) from exc
        if len(hit) != 1:
            raise NativeModelError(
                f"Curve on Mesh anchor {index + 1} does not intersect the source mesh."
            )
        facet_index, raw_point = next(iter(hit.items()))
        facet_index = int(facet_index)
        if not 0 <= facet_index < int(mesh.CountFacets):
            raise NativeModelError("Curve on Mesh resolved an invalid source facet.")
        point = _tuple3(raw_point)
        travel = _subtract(point, anchor.origin_mm)
        if _dot(travel, anchor.direction) < -_WEIGHT_EPSILON:
            raise NativeModelError(
                f"Curve on Mesh anchor {index + 1} intersects only behind its pick ray."
            )
        triangle = tuple(_tuple3(value) for value in mesh.Facets[facet_index].Points)
        facets.append(facet_index)
        weights.append(_barycentric(point, triangle))
        intersections.append(point)
    for first, second in zip(intersections, intersections[1:], strict=False):
        if math.dist(first, second) <= _WEIGHT_EPSILON:
            raise NativeModelError("Curve on Mesh anchors must resolve to distinct points.")
    if spec.closed and math.dist(intersections[-1], intersections[0]) <= _WEIGHT_EPSILON:
        raise NativeModelError(
            "A closed Curve on Mesh must not repeat its first anchor at the end."
        )
    connection_rays = spec.anchors[1:] + ((spec.anchors[0],) if spec.closed else ())
    directions = tuple(_local_direction(mesh, anchor.direction) for anchor in connection_rays)
    return PreparedSurfaceCurveOnMesh(
        spec,
        source,
        tuple(facets),
        tuple(weights),
        directions,
        tuple(intersections),
        source_fingerprint,
    )


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceCurveOnMesh) -> bool:
    try:
        return (
            document.getObject(prepared.source.Name) is prepared.source
            and prepared.source.Document is document
            and _is_active_mesh(prepared.source)
            and all(
                0 <= facet < int(prepared.source.Mesh.CountFacets)
                for facet in prepared.facets
            )
            and _mesh_fingerprint(prepared.source.Mesh) == prepared.source_fingerprint
        )
    except Exception:
        return False


def _vectors(values: Any) -> tuple[tuple[float, float, float], ...]:
    return tuple(_tuple3(value) for value in values)


def _close_vectors(
    actual: Sequence[Sequence[float]],
    expected: Sequence[Sequence[float]],
) -> bool:
    return len(actual) == len(expected) and all(
        math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=1.0e-7)
        for actual_vector, expected_vector in zip(actual, expected, strict=True)
        for left, right in zip(actual_vector, expected_vector, strict=True)
    )


def create_surface_curve_on_mesh(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceCurveOnMesh,
) -> NativeMutationDraft:
    import FreeCAD as App
    import MeshPart  # noqa: F401 - registers MeshPart::CurveOnMesh

    if not isinstance(prepared, PreparedSurfaceCurveOnMesh):
        raise TypeError("prepared must be a PreparedSurfaceCurveOnMesh")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("The Curve on Mesh source changed after preflight.")
    spec = prepared.spec
    result = document.addObject("MeshPart::CurveOnMesh", "CurveOnMesh")
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::CurveOnMesh":
        raise NativeModelError("The Curve on Mesh factory returned the wrong type.")
    result.Label = label
    result.Source = prepared.source
    result.AnchorFacets = list(prepared.facets)
    result.AnchorWeights = [App.Vector(*value) for value in prepared.weights]
    result.ProjectionDirections = [
        App.Vector(*value) for value in prepared.projection_directions
    ]
    result.Closed = spec.closed
    result.Approximate = spec.approximate
    result.MaximumDegree = spec.maximum_degree
    result.Continuity = spec.continuity
    result.Tolerance = spec.tolerance
    result.SplitAngle = spec.split_angle_degrees
    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Edges) < 1
    ):
        status = str(result.getStatusString() or "")
        raise NativeModelError(
            status if status and status != "Valid" else "Curve on Mesh produced no valid curve."
        )
    document.publishProvisionalTimelineOperationBlock(result, (), ())
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_surface_curve_on_mesh(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSurfaceCurveOnMesh = draft.value["prepared"]
    result = draft.value["result"]
    spec = prepared.spec
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "MeshPart::CurveOnMesh"
        or str(result.Label) != draft.value["label"]
        or result.Source is not prepared.source
        or tuple(int(value) for value in result.AnchorFacets) != prepared.facets
        or not _close_vectors(_vectors(result.AnchorWeights), prepared.weights)
        or not _close_vectors(
            _vectors(result.ProjectionDirections), prepared.projection_directions
        )
        or bool(result.Closed) is not spec.closed
        or bool(result.Approximate) is not spec.approximate
        or int(result.MaximumDegree) != spec.maximum_degree
        or str(result.Continuity) != spec.continuity
        or not math.isclose(float(result.Tolerance), spec.tolerance)
        or not math.isclose(float(result.SplitAngle), spec.split_angle_degrees)
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Edges) < 1
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError("Curve on Mesh failed its retained postcondition.")
    timeline = document.getObject("VibeCADTimeline")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or timeline is None
        or list(timeline.Operations).count(result) != 1
    ):
        raise NativeModelError("Curve on Mesh lost its History identity.")
    return {
        "root": object_reference(result),
        "source": object_reference(prepared.source),
        "anchor_count": len(prepared.facets),
        "closed": spec.closed,
        "approximate": spec.approximate,
        "curve_edges": len(shape.Edges),
        "length_mm": float(shape.Length),
        "continuity": spec.continuity,
        "maximum_degree": spec.maximum_degree,
        "tolerance": spec.tolerance,
        "split_angle_degrees": spec.split_angle_degrees,
    }
