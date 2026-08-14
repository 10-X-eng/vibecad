# SPDX-License-Identifier: LGPL-2.1-or-later

"""Retained Mesh plane trims, plane sections, and parallel cross-sections."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_geometry_sha256, mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    PreparedPlaneTarget,
    is_live,
    mesh_target_still_exact,
    plane_target_still_exact,
    prepare_mesh_target,
    prepare_mesh_targets,
    prepare_plane_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference


@dataclass(frozen=True, slots=True)
class PreparedMeshPlaneTrim:
    operation: str
    target: PreparedMeshTarget
    plane: PreparedPlaneTarget
    sides: tuple[str, ...]
    labels: tuple[str, ...]
    expected_result_sha256: tuple[str, ...]
    result_mode: str


@dataclass(frozen=True, slots=True)
class PreparedMeshPlaneSection:
    operation: str
    target: PreparedMeshTarget
    plane: PreparedPlaneTarget
    result_label: str
    minimum_length_mm: float
    connect_edges: bool


@dataclass(frozen=True, slots=True)
class PreparedMeshCrossSections:
    operation: str
    targets: tuple[PreparedMeshTarget, ...]
    normal: tuple[float, float, float]
    positions_mm: tuple[float, ...]
    epsilon_mm: float
    connect_edges: bool


PreparedMeshPlaneOperation = (
    PreparedMeshPlaneTrim | PreparedMeshPlaneSection | PreparedMeshCrossSections
)


def _label(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError(f"{field} must contain 1 to 160 visible characters.")
    return result


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMeshError(f"{field} must be one finite number.")
    return result


def _plane_trim_spec(value: Any) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise NativeMeshError("result must identify one published plane trim mode.")
    mode = str(value.get("mode") or "")
    if mode in {"keep_below", "keep_above"}:
        if set(value) != {"mode", "result_label"}:
            raise NativeMeshError(f"{mode} requires only mode and result_label.")
        side = "Below" if mode == "keep_below" else "Above"
        return mode, (side,), (_label(value["result_label"], "result_label"),)
    if mode == "split":
        if set(value) != {"mode", "below_result_label", "above_result_label"}:
            raise NativeMeshError(
                "split requires mode, below_result_label, and above_result_label."
            )
        return (
            mode,
            ("Below", "Above"),
            (
                _label(value["below_result_label"], "below_result_label"),
                _label(value["above_result_label"], "above_result_label"),
            ),
        )
    raise NativeMeshError("result.mode must be keep_below, keep_above, or split.")


def _plane_vectors(plane: Any) -> tuple[Any, Any]:
    import FreeCAD as App

    placement = plane.Placement
    base = App.Vector(placement.Base)
    normal = placement.Rotation.multVec(App.Vector(0.0, 0.0, 1.0))
    if float(normal.Length) <= 1.0e-12:
        raise NativeMeshError("The exact datum plane has an invalid normal.")
    normal.normalize()
    return base, normal


def _prepare_plane_trim(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> PreparedMeshPlaneTrim:
    target = prepare_mesh_target(
        document, document_uid, values["target"], require_label=False
    )
    plane = prepare_plane_target(document, document_uid, values["plane"])
    mode, sides, labels = _plane_trim_spec(values["result"])
    base, normal = _plane_vectors(plane.plane)
    expected = []
    for side, label in zip(sides, labels):
        trial = target.source.Mesh.copy()
        direction = normal if side == "Below" else -normal
        try:
            trial.trimByPlane(base, direction)
        except Exception as exc:
            raise NativeMeshError("The datum plane could not trim the exact Mesh.") from exc
        if int(trial.CountFacets) < 1:
            raise NativeMeshError(f"The datum plane leaves no usable {label} result.")
        digest = mesh_geometry_sha256(trial)
        if digest == target.source_geometry_sha256:
            raise NativeMeshError(
                f"The datum plane does not change the requested {label} result.",
                error_code="NATIVE_MESH_OPERATION_NO_CHANGE",
            )
        expected.append(digest)
    if len(set(expected)) != len(expected):
        raise NativeMeshError("The datum plane did not produce two distinct split sides.")
    return PreparedMeshPlaneTrim(
        "trim_by_plane", target, plane, sides, labels, tuple(expected), mode
    )


def _settings(value: Any, required: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeMeshError(f"{name} must contain exactly {', '.join(sorted(required))}.")
    return value


def _intersections(
    mesh: Any,
    planes: list[tuple[Any, Any]],
    epsilon: float,
    connect_edges: bool,
) -> list[Any]:
    try:
        return list(mesh.crossSections(planes, epsilon, connect_edges))
    except Exception as exc:
        raise NativeMeshError("The configured section planes could not be evaluated.") from exc


def _prepare_plane_section(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> PreparedMeshPlaneSection:
    target = prepare_mesh_target(
        document, document_uid, values["target"], require_label=False
    )
    plane = prepare_plane_target(document, document_uid, values["plane"])
    settings = _settings(
        values["settings"], {"minimum_length_mm", "connect_edges"}, "settings"
    )
    minimum = _finite(settings["minimum_length_mm"], "minimum_length_mm")
    connect = settings["connect_edges"]
    if minimum < 0.0 or type(connect) is not bool:
        raise NativeMeshError(
            "minimum_length_mm must be non-negative and connect_edges must be boolean."
        )
    base, normal = _plane_vectors(plane.plane)
    sections = _intersections(target.source.Mesh, [(base, normal)], minimum, connect)
    if not sections or not any(list(section or []) for section in sections):
        raise NativeMeshError("The exact datum plane does not intersect the exact Mesh.")
    return PreparedMeshPlaneSection(
        "section_by_plane",
        target,
        plane,
        _label(values["result_label"], "result_label"),
        minimum,
        connect,
    )


def _normal(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeMeshError("normal must contain only x, y, and z.")
    components = tuple(_finite(value[axis], f"normal.{axis}") for axis in ("x", "y", "z"))
    length = math.sqrt(sum(component * component for component in components))
    if length <= 1.0e-12:
        raise NativeMeshError("normal must be finite and nonzero.")
    return tuple(component / length for component in components)


def _prepare_cross_sections(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> PreparedMeshCrossSections:
    targets = prepare_mesh_targets(document, document_uid, values["targets"])
    planes = _settings(values["planes"], {"normal", "positions_mm"}, "planes")
    normal = _normal(planes["normal"])
    raw_positions = planes["positions_mm"]
    if not isinstance(raw_positions, list) or not 1 <= len(raw_positions) <= 256:
        raise NativeMeshError("positions_mm must contain 1 to 256 signed distances.")
    positions = tuple(
        _finite(value, f"positions_mm[{index}]")
        for index, value in enumerate(raw_positions)
    )
    if len(positions) != len(set(positions)):
        raise NativeMeshError("positions_mm must not repeat a section plane.")
    settings = _settings(values["settings"], {"epsilon_mm", "connect_edges"}, "settings")
    epsilon = _finite(settings["epsilon_mm"], "epsilon_mm")
    connect = settings["connect_edges"]
    if not 0.0 <= epsilon <= 1.0e6 or type(connect) is not bool:
        raise NativeMeshError(
            "epsilon_mm must be between 0 and 1000000 and connect_edges must be boolean."
        )
    import FreeCAD as App

    direction = App.Vector(*normal)
    section_planes = [(direction * position, direction) for position in positions]
    for target in targets:
        sections = _intersections(target.source.Mesh, section_planes, epsilon, connect)
        if not sections or not any(list(section or []) for section in sections):
            raise NativeMeshError(
                f"The configured planes do not intersect exact Mesh {target.source.Name!r}."
            )
    return PreparedMeshCrossSections(
        "cross_sections", targets, normal, positions, epsilon, connect
    )


def prepare_mesh_plane_operation(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshPlaneOperation:
    if operation == "trim_by_plane":
        return _prepare_plane_trim(document, document_uid, values)
    if operation == "section_by_plane":
        return _prepare_plane_section(document, document_uid, values)
    if operation == "cross_sections":
        return _prepare_cross_sections(document, document_uid, values)
    raise NativeMeshError("The requested Mesh plane operation is unavailable.")


def _exact_plane_inputs(
    document: Any,
    target: PreparedMeshTarget,
    plane: PreparedPlaneTarget,
) -> None:
    if not mesh_target_still_exact(document, target) or not plane_target_still_exact(
        document, plane
    ):
        raise NativeMeshError(
            "A Mesh or datum plane changed after section preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _create_plane_trim(document: Any, prepared: PreparedMeshPlaneTrim) -> NativeMutationDraft:
    _exact_plane_inputs(document, prepared.target, prepared.plane)
    import Mesh  # noqa: F401 - registers Mesh::TrimByPlane
    import MeshGui

    results = []
    for side, label in zip(prepared.sides, prepared.labels):
        result = document.addObject(
            "Mesh::TrimByPlane", document.getUniqueObjectName("TrimByPlane")
        )
        if result is None or str(getattr(result, "TypeId", "")) != "Mesh::TrimByPlane":
            raise NativeMeshError("The retained plane trim could not be created.")
        result.Label = label
        result.Source = prepared.target.source
        result.Plane = prepared.plane.plane
        result.Side = side
        results.append(result)
    group = MeshGui.publishReplacingOutputs(
        str(document.Name),
        [prepared.target.source] * len(results),
        results,
        "PlaneSplit",
        "Split Mesh by Plane" if len(results) > 1 else "Trim Mesh by Plane",
        "Plane trim",
    )
    created = [*results, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"prepared": prepared, "results": tuple(results), "group": group},
        recompute_targets=tuple(created),
        created=tuple(object_identity(obj) for obj in created),
        replaced=(object_identity(prepared.target.source),),
    )


def _create_plane_section(
    document: Any, prepared: PreparedMeshPlaneSection
) -> NativeMutationDraft:
    _exact_plane_inputs(document, prepared.target, prepared.plane)
    import MeshPart  # noqa: F401 - registers MeshPart::SectionByPlane
    import MeshGui

    result = document.addObject(
        "MeshPart::SectionByPlane", document.getUniqueObjectName("MeshPlaneSection")
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::SectionByPlane":
        raise NativeMeshError("The retained Mesh plane section could not be created.")
    result.Label = prepared.result_label
    result.Source = prepared.target.source
    result.Plane = prepared.plane.plane
    result.MinimumLength = prepared.minimum_length_mm
    result.ConnectEdges = prepared.connect_edges
    MeshGui.publishSourcePreservingOutputs(
        str(document.Name),
        [prepared.target.source, prepared.plane.plane],
        [result],
        "MeshPlaneSections",
        "Mesh Plane Sections",
        "Section mesh with plane",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "results": (result,), "group": None},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def _create_cross_sections(
    document: Any, prepared: PreparedMeshCrossSections
) -> NativeMutationDraft:
    if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "An exact Mesh changed after cross-section preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import FreeCAD as App
    import MeshPart  # noqa: F401 - registers MeshPart::CrossSections
    import MeshGui

    results = []
    for target in prepared.targets:
        result = document.addObject(
            "MeshPart::CrossSections", document.getUniqueObjectName("MeshCrossSections")
        )
        if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::CrossSections":
            raise NativeMeshError("The retained Mesh cross-sections could not be created.")
        result.Label = target.label
        result.Source = target.source
        result.PlaneNormal = App.Vector(*prepared.normal)
        result.PlanePositions = list(prepared.positions_mm)
        result.Epsilon = prepared.epsilon_mm
        result.ConnectEdges = prepared.connect_edges
        results.append(result)
    group = MeshGui.publishSourcePreservingOutputs(
        str(document.Name),
        [target.source for target in prepared.targets],
        results,
        "MeshCrossSections",
        "Mesh Cross-Sections",
        "Create mesh cross-sections",
    )
    created = [*results, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"prepared": prepared, "results": tuple(results), "group": group},
        recompute_targets=tuple(created),
        created=tuple(object_identity(obj) for obj in created),
    )


def create_mesh_plane_operation(
    document: Any,
    prepared: PreparedMeshPlaneOperation,
) -> NativeMutationDraft:
    if isinstance(prepared, PreparedMeshPlaneTrim):
        return _create_plane_trim(document, prepared)
    if isinstance(prepared, PreparedMeshPlaneSection):
        return _create_plane_section(document, prepared)
    if isinstance(prepared, PreparedMeshCrossSections):
        return _create_cross_sections(document, prepared)
    raise TypeError("prepared is not a Mesh plane operation")


def _shape_summary(result: Any) -> dict[str, Any]:
    shape = result.Shape
    return {
        **object_reference(result),
        "type_id": str(result.TypeId),
        "wire_count": len(shape.Wires),
        "edge_count": len(shape.Edges),
        "vertex_count": len(shape.Vertexes),
        "length_mm": float(shape.Length),
    }


def _replacement_history(
    document: Any,
    target: PreparedMeshTarget,
    results: tuple[Any, ...],
    group: Any | None,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    replaced = (target.source,) if target.source_visible else ()
    if group is None:
        return (
            len(results) == 1
            and operations.count(results[0]) == 1
            and str(getattr(results[0], "VibeCADTimelineRole", "") or "") == "operation"
            and tuple(getattr(results[0], "VibeCADTimelineReplacedInputs", ()) or ())
            == replaced
        )
    return (
        str(getattr(group, "TypeId", "")) == "Mesh::OutputGroup"
        and operations.count(group) == 1
        and tuple(getattr(group, "Sources", ()) or ()) == (target.source,)
        and tuple(getattr(group, "Group", ()) or ()) == results
        and str(getattr(group, "InputMode", "") or "") == "Replacement"
        and str(getattr(group, "OperationKind", "") or "") == "Plane trim"
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ()) == replaced
        and all(
            str(getattr(result, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(result, "VibeCADTimelineOwner", None) is group
            for result in results
        )
    )


def _source_preserving_history(
    document: Any,
    sources: tuple[Any, ...],
    results: tuple[Any, ...],
    group: Any | None,
    operation_kind: str,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    if group is None:
        return (
            len(results) == 1
            and operations.count(results[0]) == 1
            and str(getattr(results[0], "VibeCADTimelineRole", "") or "") == "operation"
            and not tuple(getattr(results[0], "VibeCADTimelineReplacedInputs", ()) or ())
        )
    return (
        str(getattr(group, "TypeId", "")) == "Mesh::OutputGroup"
        and operations.count(group) == 1
        and tuple(getattr(group, "Sources", ()) or ()) == sources
        and tuple(getattr(group, "Group", ()) or ()) == results
        and str(getattr(group, "InputMode", "") or "") == "Source preserving"
        and str(getattr(group, "OperationKind", "") or "") == operation_kind
        and not tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ())
        and all(
            str(getattr(result, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(result, "VibeCADTimelineOwner", None) is group
            for result in results
        )
    )


def _verify_plane_trim(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    results = draft.value["results"]
    group = draft.value["group"]
    if len(results) != len(prepared.sides) or not _replacement_history(
        document, prepared.target, results, group
    ):
        raise NativeMeshError("The plane trim failed its exact History postcondition.")
    outputs = []
    for result, side, label, expected in zip(
        results, prepared.sides, prepared.labels, prepared.expected_result_sha256
    ):
        status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
        if (
            not is_live(document, result)
            or str(result.TypeId) != "Mesh::TrimByPlane"
            or result.Source is not prepared.target.source
            or result.Plane is not prepared.plane.plane
            or str(result.Side) != side
            or str(result.Label) != label
            or not bool(result.isValid())
            or int(result.Mesh.CountFacets) < 1
            or mesh_geometry_sha256(result.Mesh) != expected
        ):
            raise NativeMeshError(
                status if not bool(result.isValid()) else "A plane trim result failed verification."
            )
        outputs.append(mesh_object_state(result))
    _exact_plane_inputs(document, prepared.target, prepared.plane)
    if bool(prepared.target.source.Visibility) or bool(prepared.plane.plane.Visibility) != bool(
        prepared.plane.source_visible
    ):
        raise NativeMeshError("The plane trim changed source presentation unexpectedly.")
    response = {
        "operation": "trim_by_plane",
        "result_mode": prepared.result_mode,
        "source": object_reference(prepared.target.source),
        "plane": object_reference(prepared.plane.plane),
        "outputs": outputs,
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    return response


def _verify_plane_section(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["results"][0]
    if not _source_preserving_history(
        document,
        (prepared.target.source, prepared.plane.plane),
        (result,),
        None,
        "Section mesh with plane",
    ):
        raise NativeMeshError("The plane section failed its exact History postcondition.")
    shape = result.Shape
    status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
    if (
        str(result.TypeId) != "MeshPart::SectionByPlane"
        or result.Source is not prepared.target.source
        or result.Plane is not prepared.plane.plane
        or str(result.Label) != prepared.result_label
        or not math.isclose(float(result.MinimumLength.Value), prepared.minimum_length_mm)
        or bool(result.ConnectEdges) is not prepared.connect_edges
        or not bool(result.isValid())
        or shape.isNull()
        or not shape.isValid()
        or not shape.Edges
    ):
        raise NativeMeshError(
            status if not bool(result.isValid()) else "The plane section failed verification."
        )
    _exact_plane_inputs(document, prepared.target, prepared.plane)
    if bool(prepared.target.source.Visibility) != prepared.target.source_visible or bool(
        prepared.plane.plane.Visibility
    ) != prepared.plane.source_visible:
        raise NativeMeshError("The plane section changed source visibility.")
    return {
        "operation": "section_by_plane",
        "source": object_reference(prepared.target.source),
        "plane": object_reference(prepared.plane.plane),
        "result": _shape_summary(result),
        "settings": {
            "minimum_length_mm": prepared.minimum_length_mm,
            "connect_edges": prepared.connect_edges,
        },
    }


def _verify_cross_sections(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    results = draft.value["results"]
    group = draft.value["group"]
    sources = tuple(target.source for target in prepared.targets)
    if len(results) != len(sources) or not _source_preserving_history(
        document, sources, results, group, "Create mesh cross-sections"
    ):
        raise NativeMeshError("The cross-sections failed their exact History postcondition.")
    outputs = []
    for target, result in zip(prepared.targets, results):
        shape = result.Shape
        vector = result.PlaneNormal
        actual_normal = (float(vector.x), float(vector.y), float(vector.z))
        if (
            str(result.TypeId) != "MeshPart::CrossSections"
            or result.Source is not target.source
            or str(result.Label) != target.label
            or any(
                not math.isclose(actual, expected, rel_tol=1.0e-12, abs_tol=1.0e-12)
                for actual, expected in zip(actual_normal, prepared.normal)
            )
            or tuple(float(value) for value in result.PlanePositions)
            != prepared.positions_mm
            or not math.isclose(float(result.Epsilon.Value), prepared.epsilon_mm)
            or bool(result.ConnectEdges) is not prepared.connect_edges
            or not bool(result.isValid())
            or shape.isNull()
            or not shape.isValid()
            or not shape.Edges
            or not mesh_target_still_exact(document, target)
            or bool(target.source.Visibility) != target.source_visible
        ):
            status = str(getattr(result, "getStatusString", lambda: "")() or "").strip()
            raise NativeMeshError(
                status if not bool(result.isValid()) else "A cross-section result failed verification."
            )
        outputs.append(_shape_summary(result))
    response = {
        "operation": "cross_sections",
        "sources": [object_reference(target.source) for target in prepared.targets],
        "outputs": outputs,
        "planes": {
            "normal": list(prepared.normal),
            "positions_mm": list(prepared.positions_mm),
        },
        "settings": {
            "epsilon_mm": prepared.epsilon_mm,
            "connect_edges": prepared.connect_edges,
        },
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    return response


def verify_mesh_plane_operation(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value.get("prepared")
    if isinstance(prepared, PreparedMeshPlaneTrim):
        return _verify_plane_trim(document, draft)
    if isinstance(prepared, PreparedMeshPlaneSection):
        return _verify_plane_section(document, draft)
    if isinstance(prepared, PreparedMeshCrossSections):
        return _verify_cross_sections(document, draft)
    raise NativeMeshError("The Mesh plane operation lost its prepared state.")
