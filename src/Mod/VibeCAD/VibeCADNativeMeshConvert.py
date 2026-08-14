# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained shape/Mesh conversion creation and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference, resolve_object


_FACE = re.compile(r"^Face([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class PreparedShapeToMesh:
    source: Any
    subelements: tuple[str, ...]
    label: str
    linear_deflection_mm: float
    angular_deflection_degrees: float
    relative: bool
    segments: bool


@dataclass(frozen=True, slots=True)
class PreparedMeshToShape:
    source: Any
    expected_state_sha256: str
    label: str
    tolerance_mm: float
    sew_adjacent_faces: bool


def _active(obj: Any) -> bool:
    import MeshGui

    return bool(MeshGui.isNativeMeshInputActive(obj))


def _live(document: Any, obj: Any) -> bool:
    return (
        getattr(obj, "Document", None) is document
        and document.getObject(str(getattr(obj, "Name", "") or "")) is obj
    )


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeMeshError("label must contain 1 to 160 visible characters.")
    return result


def _positive_number(value: Any, field: str, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise NativeMeshError(f"{field} must be one finite positive number.")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0 or result > maximum:
        raise NativeMeshError(f"{field} must be greater than zero and no more than {maximum:g}.")
    return result


def _source_reference(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeMeshError("source must contain one exact object_name.")
    try:
        return NativeObjectRef(document_uid, str(value["object_name"]))
    except Exception as exc:
        raise NativeMeshError("source.object_name must identify one exact document object.") from exc


def _history_is_exact(document: Any, result: Any) -> bool:
    timeline = getattr(document, "VibeCADTimeline", None)
    return (
        str(getattr(result, "VibeCADTimelineRole", "") or "") == "operation"
        and getattr(result, "VibeCADTimelineOwner", None) is None
        and timeline is not None
        and list(getattr(timeline, "Operations", ()) or ()).count(result) == 1
    )


def prepare_shape_to_mesh(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    subelements: Any,
    label: Any,
    linear_deflection_mm: Any,
    angular_deflection_degrees: Any,
    relative: Any,
    segments: Any,
) -> PreparedShapeToMesh:
    reference = _source_reference(document_uid, source)
    obj = resolve_object(document, reference)
    if not _active(obj):
        raise NativeMeshError(
            "The exact shape is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    shape = getattr(obj, "Shape", None)
    try:
        usable = shape is not None and not shape.isNull() and shape.isValid()
        face_count = len(shape.Faces) if usable else 0
    except Exception:
        usable = False
        face_count = 0
    if not usable or face_count < 1:
        raise NativeMeshError(
            "shape_to_mesh requires one current-History object with a valid shape containing faces."
        )
    if not isinstance(subelements, list) or len(subelements) > 256:
        raise NativeMeshError("subelements must be an ordered list of at most 256 FaceN names.")
    names = tuple(str(value or "") for value in subelements)
    if len(names) != len(set(names)):
        raise NativeMeshError("subelements must not repeat a face.")
    for name in names:
        match = _FACE.fullmatch(name)
        if match is None or int(match.group(1)) > face_count:
            raise NativeMeshError(
                f"{name or 'The requested subelement'} is not a face on {reference.object_name}."
            )
        try:
            selected = shape.getElement(name)
            if selected.isNull() or not selected.isValid() or str(selected.ShapeType) != "Face":
                raise ValueError
        except Exception as exc:
            raise NativeMeshError(
                f"{name} is not a valid current face on {reference.object_name}."
            ) from exc
    if type(relative) is not bool or type(segments) is not bool:
        raise NativeMeshError("relative and segments must each be true or false.")
    return PreparedShapeToMesh(
        obj,
        names,
        _label(label),
        _positive_number(linear_deflection_mm, "linear_deflection_mm", 1_000_000.0),
        _positive_number(angular_deflection_degrees, "angular_deflection_degrees", 180.0),
        relative,
        segments,
    )


def create_shape_to_mesh(document: Any, prepared: PreparedShapeToMesh) -> NativeMutationDraft:
    import MeshGui
    import MeshPart  # noqa: F401 - registers MeshPart::MeshFromShape

    if not isinstance(prepared, PreparedShapeToMesh):
        raise TypeError("prepared must be a PreparedShapeToMesh")
    if not _live(document, prepared.source) or not _active(prepared.source):
        raise NativeMeshError("The exact shape changed after conversion preflight.")
    result = document.addObject(
        "MeshPart::MeshFromShape",
        document.getUniqueObjectName("MeshFromShape"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::MeshFromShape":
        raise NativeMeshError("The linked Mesh-from-shape feature could not be created.")
    result.Label = prepared.label
    result.Source = (prepared.source, list(prepared.subelements))
    result.Method = "Standard"
    result.LinearDeflection = prepared.linear_deflection_mm
    result.AngularDeflection = math.radians(prepared.angular_deflection_degrees)
    result.Relative = prepared.relative
    result.Segments = prepared.segments
    MeshGui.publishSourcePreservingOutputs(
        str(document.Name),
        [prepared.source],
        [result],
        "MeshesFromGeometry",
        "Meshes From Geometry",
        "Mesh from geometry",
    )
    return NativeMutationDraft(
        value={"result": result, "prepared": prepared},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_shape_to_mesh(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    source_link = getattr(result, "Source", None)
    linked = source_link[0] if isinstance(source_link, tuple) and source_link else None
    linked_subelements = (
        tuple(str(value) for value in source_link[1])
        if isinstance(source_link, tuple) and len(source_link) > 1
        else ()
    )
    mesh = getattr(result, "Mesh", None)
    if (
        not _live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::MeshFromShape"
        or str(getattr(result, "Label", "")) != prepared.label
        or linked is not prepared.source
        or linked_subelements != prepared.subelements
        or str(getattr(result, "Method", "")) != "Standard"
        or not math.isclose(float(result.LinearDeflection), prepared.linear_deflection_mm)
        or not math.isclose(
            float(result.AngularDeflection),
            math.radians(prepared.angular_deflection_degrees),
        )
        or bool(result.Relative) is not prepared.relative
        or bool(result.Segments) is not prepared.segments
        or int(getattr(mesh, "CountFacets", 0) or 0) < 1
        or not bool(result.isValid())
        or not _live(document, prepared.source)
        or not _active(prepared.source)
        or not _history_is_exact(document, result)
    ):
        raise NativeMeshError("The linked Mesh-from-shape result failed its exact postcondition.")
    return {
        "created": mesh_object_state(result),
        "source": object_reference(prepared.source),
        "subelements": list(prepared.subelements),
        "method": "Standard",
        "settings": {
            "linear_deflection_mm": prepared.linear_deflection_mm,
            "angular_deflection_degrees": prepared.angular_deflection_degrees,
            "relative": prepared.relative,
            "segments": prepared.segments,
        },
    }


def prepare_mesh_to_shape(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    expected_state_sha256: Any,
    label: Any,
    tolerance_mm: Any,
    sew_adjacent_faces: Any,
) -> PreparedMeshToShape:
    reference = _source_reference(document_uid, source)
    obj = resolve_object(document, reference, expected_types=("Mesh::Feature",))
    if not _active(obj):
        raise NativeMeshError(
            "The exact Mesh is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(obj)
    expected = str(expected_state_sha256 or "")
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The exact Mesh changed after the provider read its state.",
            error_code="NATIVE_MESH_STATE_STALE",
            repair={
                "source": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
                "current_topology": state.get("topology"),
            },
        )
    if int(dict(state.get("topology") or {}).get("facets", 0) or 0) < 1:
        raise NativeMeshError("mesh_to_shape requires one nonempty Mesh.")
    if type(sew_adjacent_faces) is not bool:
        raise NativeMeshError("sew_adjacent_faces must be true or false.")
    return PreparedMeshToShape(
        obj,
        expected,
        _label(label),
        _positive_number(tolerance_mm, "tolerance_mm", 10.0),
        sew_adjacent_faces,
    )


def create_mesh_to_shape(document: Any, prepared: PreparedMeshToShape) -> NativeMutationDraft:
    import MeshGui
    import MeshPart  # noqa: F401 - registers MeshPart::ShapeFromMesh

    if not isinstance(prepared, PreparedMeshToShape):
        raise TypeError("prepared must be a PreparedMeshToShape")
    if (
        not _live(document, prepared.source)
        or not _active(prepared.source)
        or mesh_object_state(prepared.source).get("state_sha256")
        != prepared.expected_state_sha256
    ):
        raise NativeMeshError("The exact Mesh changed after conversion preflight.")
    result = document.addObject(
        "MeshPart::ShapeFromMesh",
        document.getUniqueObjectName(f"{prepared.source.Name}_shape"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::ShapeFromMesh":
        raise NativeMeshError("The linked shape-from-Mesh feature could not be created.")
    result.Label = prepared.label
    result.Source = prepared.source
    result.Tolerance = prepared.tolerance_mm
    result.SewShape = prepared.sew_adjacent_faces
    MeshGui.publishSourcePreservingOutputs(
        str(document.Name),
        [prepared.source],
        [result],
        "ConvertedMeshShapes",
        "Converted Mesh Shapes",
        "Convert mesh to shape",
    )
    return NativeMutationDraft(
        value={"result": result, "prepared": prepared},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_mesh_to_shape(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    shape = getattr(result, "Shape", None)
    try:
        shape_valid = not shape.isNull() and shape.isValid()
        topology = {
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "vertices": len(shape.Vertexes),
        }
    except Exception:
        shape_valid = False
        topology = {}
    if (
        not _live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::ShapeFromMesh"
        or str(getattr(result, "Label", "")) != prepared.label
        or getattr(result, "Source", None) is not prepared.source
        or not math.isclose(float(result.Tolerance), prepared.tolerance_mm)
        or bool(result.SewShape) is not prepared.sew_adjacent_faces
        or not bool(result.isValid())
        or not shape_valid
        or not _live(document, prepared.source)
        or not _active(prepared.source)
        or mesh_object_state(prepared.source).get("state_sha256")
        != prepared.expected_state_sha256
        or not _history_is_exact(document, result)
    ):
        raise NativeMeshError("The linked shape-from-Mesh result failed its exact postcondition.")
    return {
        "created": object_reference(result),
        "source": object_reference(prepared.source),
        "topology": topology,
        "settings": {
            "tolerance_mm": prepared.tolerance_mm,
            "sew_adjacent_faces": prepared.sew_adjacent_faces,
        },
    }
