# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained shape/Mesh conversion creation and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact, prepare_mesh_target
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference, resolve_object


_FACE = re.compile(r"^Face([1-9][0-9]*)$")
_FACETED_REPRESENTATION = {
    "Face": "faceted_face",
    "Shell": "faceted_shell",
    "Solid": "faceted_solid",
    "CompSolid": "faceted_compsolid",
    "Compound": "faceted_compound",
}


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
    make_solid: bool


def _shape_for_tessellation(source: Any) -> Any:
    import Part

    try:
        shape = Part.getShape(source, transform=True)
    except Exception as exc:
        raise NativeMeshError(
            "The exact source shape could not be detached for tessellation.",
            error_code="NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
        ) from exc
    if shape is None or shape.isNull():
        raise NativeMeshError(
            "shape_to_mesh requires one current-History shape.",
            error_code="NATIVE_MESH_TESSELLATION_SOURCE_INVALID",
        )
    return shape


def _shape_signature(source: Any) -> dict[str, Any]:
    source_shape = getattr(source, "Shape", None)
    placement = (
        source.getGlobalPlacement()
        if callable(getattr(source, "getGlobalPlacement", None))
        else getattr(source, "Placement", None)
    )
    matrix = getattr(placement, "Matrix", None)
    return {
        "shape_hash": int(source_shape.hashCode()),
        "placement": [float(value) for value in matrix.A] if matrix is not None else [],
    }


def shape_tessellation_source_still_exact(document: Any, request: Any) -> bool:
    source = getattr(request, "source", None)
    if not _live(document, source) or not _active(source):
        return False
    try:
        return _shape_signature(source) == dict(request.source_signature)
    except Exception:
        return False


def _tessellation_settings(value: Mapping[str, Any]) -> dict[str, Any]:
    settings = dict(value)
    method = str(settings.get("method") or "")
    expected = {
        "standard": {
            "method",
            "linear_deflection_mm",
            "angular_deflection_radians",
            "relative",
            "segments",
        },
        "mefisto": {"method", "maximum_edge_length_mm"},
        "netgen": {
            "method",
            "fineness",
            "growth_rate",
            "segments_per_edge",
            "segments_per_radius",
            "second_order",
            "optimize",
            "quad_dominated",
        },
        "gmsh": {
            "method",
            "algorithm",
            "minimum_size_mm",
            "maximum_size_mm",
            "geometry_tolerance_mm",
            "element_order",
            "optimize",
            "executable",
            "timeout_seconds",
        },
    }
    if method not in expected or set(settings) != expected[method]:
        raise NativeMeshError("Tessellation settings must match one published method.")
    if method == "standard":
        settings["linear_deflection_mm"] = _positive_number(
            settings["linear_deflection_mm"], "linear_deflection_mm", 1_000_000.0
        )
        settings["angular_deflection_radians"] = _positive_number(
            settings["angular_deflection_radians"], "angular_deflection_radians", math.pi
        )
        if type(settings["relative"]) is not bool or type(settings["segments"]) is not bool:
            raise NativeMeshError("relative and segments must each be true or false.")
    elif method == "mefisto":
        value = settings["maximum_edge_length_mm"]
        if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) < 0.0:
            raise NativeMeshError("maximum_edge_length_mm must be one finite non-negative number.")
        settings["maximum_edge_length_mm"] = float(value)
    elif method == "netgen":
        if type(settings["fineness"]) is not int or not 0 <= settings["fineness"] <= 5:
            raise NativeMeshError("fineness must be an integer from 0 through 5.")
        for name in ("growth_rate", "segments_per_edge", "segments_per_radius"):
            value = settings[name]
            if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) < 0.0:
                raise NativeMeshError(f"{name} must be one finite non-negative number.")
            settings[name] = float(value)
        for name in ("second_order", "optimize", "quad_dominated"):
            if type(settings[name]) is not bool:
                raise NativeMeshError(f"{name} must be true or false.")
    else:
        if type(settings["algorithm"]) is not int or settings["algorithm"] not in {
            1,
            2,
            5,
            6,
            7,
            8,
            9,
            11,
        }:
            raise NativeMeshError("algorithm must identify one supported Gmsh algorithm.")
        for name in ("minimum_size_mm", "maximum_size_mm", "geometry_tolerance_mm"):
            value = settings[name]
            if type(value) not in {int, float} or not math.isfinite(float(value)):
                raise NativeMeshError(f"{name} must be one finite number.")
            settings[name] = float(value)
        if (
            settings["minimum_size_mm"] < 0.0
            or settings["maximum_size_mm"] < 0.0
            or settings["geometry_tolerance_mm"] <= 0.0
            or (
                settings["maximum_size_mm"] > 0.0
                and settings["minimum_size_mm"] > settings["maximum_size_mm"]
            )
        ):
            raise NativeMeshError("The Gmsh size and tolerance settings are invalid.")
        if type(settings["element_order"]) is not int or settings["element_order"] not in {1, 2}:
            raise NativeMeshError("element_order must be 1 or 2.")
        if type(settings["optimize"]) is not bool:
            raise NativeMeshError("optimize must be true or false.")
        if not isinstance(settings["executable"], str) or not settings["executable"].strip():
            raise NativeMeshError("executable must identify the configured Gmsh executable.")
        configured = settings["executable"].strip()
        resolved = shutil.which(configured)
        if resolved is None or not Path(resolved).is_file():
            raise NativeMeshError(
                "The configured Gmsh executable is unavailable.",
                error_code="NATIVE_MESH_TESSELLATION_GMSH_UNAVAILABLE",
            )
        settings["executable"] = str(Path(resolved).resolve())
        if type(settings["timeout_seconds"]) is not int or not 1 <= settings["timeout_seconds"] <= 86_400:
            raise NativeMeshError("timeout_seconds must be between 1 and 86400.")
    return settings


def capture_shape_tessellation(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    subelements: Any,
    label: Any,
    settings: Mapping[str, Any],
) -> Any:
    reference = _source_reference(document_uid, source)
    obj = resolve_object(document, reference)
    if not _active(obj):
        raise NativeMeshError(
            "The exact shape is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    if not isinstance(subelements, list) or len(subelements) > 256:
        raise NativeMeshError("subelements must be an ordered list of at most 256 FaceN names.")
    names = tuple(str(value or "") for value in subelements)
    if len(names) != len(set(names)) or any(_FACE.fullmatch(name) is None for name in names):
        raise NativeMeshError("subelements must contain distinct FaceN names.")
    shape = _shape_for_tessellation(obj)
    signature = _shape_signature(obj)
    from VibeCADMeshTessellationJob import make_request

    return make_request(
        source=obj,
        subelements=names,
        source_shape=shape.copy(),
        source_signature=signature,
        label=_label(label),
        settings=_tessellation_settings(settings),
    )


def capture_mesh_conversion(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    expected_state_sha256: Any,
    label: Any,
    tolerance_mm: Any,
    sew_adjacent_faces: Any,
    make_solid: Any,
    source_topology: str = "closed",
) -> Any:
    """Capture one exact detached Mesh without performing BREP work."""

    if type(sew_adjacent_faces) is not bool or type(make_solid) is not bool:
        raise NativeMeshError("sew_adjacent_faces and make_solid must each be true or false.")
    if make_solid and not sew_adjacent_faces:
        raise NativeMeshError("mesh_to_solid requires sewn adjacent faces.")
    if not isinstance(source, Mapping) or set(source) != {"object_name"}:
        raise NativeMeshError("source must contain one exact object_name.")
    target = prepare_mesh_target(
        document,
        document_uid,
        {
            "object_name": str(source["object_name"]),
            "expected_state_sha256": str(expected_state_sha256 or ""),
        },
        require_label=False,
    )
    state = mesh_object_state(target.source)
    from VibeCADMeshConversionJob import make_request

    return make_request(
        target=target,
        detached_mesh=target.source_mesh,
        source_placement=dict(state.get("placement") or {}),
        label=_label(label),
        tolerance_mm=_positive_number(tolerance_mm, "tolerance_mm", 10.0),
        sew_adjacent_faces=sew_adjacent_faces,
        make_solid=make_solid,
        source_topology=source_topology,
    )


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


def _apply_tessellation_settings(result: Any, settings: Mapping[str, Any]) -> None:
    method = str(settings["method"])
    if method == "standard":
        result.Method = "Standard"
        result.LinearDeflection = settings["linear_deflection_mm"]
        result.AngularDeflection = settings["angular_deflection_radians"]
        result.Relative = settings["relative"]
        result.Segments = settings["segments"]
    elif method == "mefisto":
        result.Method = "Mefisto"
        result.MaximumEdgeLength = settings["maximum_edge_length_mm"]
    elif method == "netgen":
        result.Method = "Netgen"
        result.Fineness = settings["fineness"]
        result.GrowthRate = settings["growth_rate"]
        result.SegmentsPerEdge = settings["segments_per_edge"]
        result.SegmentsPerRadius = settings["segments_per_radius"]
        result.SecondOrder = settings["second_order"]
        result.Optimize = settings["optimize"]
        result.QuadDominated = settings["quad_dominated"]
    elif method == "gmsh":
        result.Method = "Gmsh"
        result.GmshAlgorithm = settings["algorithm"]
        result.GmshMinimumSize = settings["minimum_size_mm"]
        result.GmshMaximumSize = settings["maximum_size_mm"]
        result.GmshGeometryTolerance = settings["geometry_tolerance_mm"]
        result.GmshElementOrder = settings["element_order"]
        result.GmshOptimize = settings["optimize"]
        result.GmshExecutable = settings["executable"]
        result.GmshTimeoutSeconds = settings["timeout_seconds"]
    else:
        raise NativeMeshError("The prepared tessellation method is unavailable.")


def commit_shape_tessellation(
    document: Any,
    prepared: Any,
    *,
    publish: bool = True,
) -> NativeMutationDraft:
    """Publish one authenticated worker result without tessellating its BREP again."""

    from VibeCADMeshTessellationJob import PreparedShapeTessellation

    if not isinstance(prepared, PreparedShapeTessellation):
        raise TypeError("prepared must be a PreparedShapeTessellation")
    request = prepared.request
    if not shape_tessellation_source_still_exact(document, request):
        raise NativeMeshError(
            "The exact shape changed while tessellation was running; no stale Mesh was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    output = prepared.mesh
    if (
        int(getattr(output, "CountPoints", 0) or 0) != prepared.points
        or int(getattr(output, "CountFacets", 0) or 0) != prepared.facets
        or int(output.countSegments()) != prepared.segments
    ):
        raise NativeMeshError(
            "The verified tessellation artifact does not match its authenticated metadata.",
            error_code="NATIVE_MESH_TESSELLATION_ARTIFACT_INVALID",
        )

    import MeshGui
    import MeshPart  # noqa: F401 - registers MeshPart::MeshFromShape

    result = document.addObject(
        "MeshPart::MeshFromShape",
        document.getUniqueObjectName("MeshFromShape"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::MeshFromShape":
        raise NativeMeshError("The tessellated Mesh could not be published.")
    result.Label = request.label
    result.UpdateFromSource = False
    result.Source = (request.source, list(request.subelements))
    _apply_tessellation_settings(result, request.settings)
    result.Mesh = output
    if publish:
        MeshGui.publishSourcePreservingOutputs(
            str(document.Name),
            [request.source],
            [result],
            "MeshesFromGeometry",
            "Meshes From Geometry",
            "Mesh from geometry",
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_shape_tessellation(
    document: Any,
    draft: NativeMutationDraft,
    *,
    require_operation: bool = True,
) -> dict[str, Any]:
    from VibeCADMeshTessellationJob import PreparedShapeTessellation

    prepared = draft.value["prepared"]
    result = draft.value["result"]
    if not isinstance(prepared, PreparedShapeTessellation):
        raise NativeMeshError("The shape tessellation lost its prepared result.")
    request = prepared.request
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
        or str(getattr(result, "Label", "")) != request.label
        or linked is not request.source
        or linked_subelements != request.subelements
        or bool(result.UpdateFromSource) is not False
        or int(getattr(mesh, "CountPoints", 0) or 0) != prepared.points
        or int(getattr(mesh, "CountFacets", 0) or 0) != prepared.facets
        or int(mesh.countSegments()) != prepared.segments
        or not bool(result.isValid())
        or not shape_tessellation_source_still_exact(document, request)
        or (require_operation and not _history_is_exact(document, result))
    ):
        raise NativeMeshError("The background shape tessellation failed its exact postcondition.")
    return {
        "created": mesh_object_state(result),
        "source": object_reference(request.source),
        "subelements": list(request.subelements),
        "settings": dict(request.settings),
        "tessellation": {
            "background": True,
            "cache_hit": prepared.cache_hit,
            "source_brep_sha256": prepared.source_brep_sha256,
            "artifact_sha256": prepared.artifact_sha256,
            "segments_sha256": prepared.segments_sha256,
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
    make_solid: Any,
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
    if type(sew_adjacent_faces) is not bool or type(make_solid) is not bool:
        raise NativeMeshError("sew_adjacent_faces and make_solid must each be true or false.")
    if make_solid:
        try:
            solid = bool(obj.Mesh.isSolid())
            self_intersections = bool(obj.Mesh.hasSelfIntersections())
        except Exception as exc:
            raise NativeMeshError(
                "The exact Mesh solid topology could not be evaluated.",
                error_code="NATIVE_MESH_SOLID_EVALUATION_FAILED",
            ) from exc
        if not solid:
            raise NativeMeshError(
                "mesh_to_solid requires one closed manifold Mesh.",
                error_code="NATIVE_MESH_SOLID_REQUIRED",
            )
        if self_intersections:
            raise NativeMeshError(
                "mesh_to_solid requires a Mesh without self-intersections.",
                error_code="NATIVE_MESH_SELF_INTERSECTIONS",
            )
    return PreparedMeshToShape(
        obj,
        expected,
        _label(label),
        _positive_number(tolerance_mm, "tolerance_mm", 10.0),
        sew_adjacent_faces,
        make_solid,
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
    result.MakeSolid = prepared.make_solid
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
            "solids": len(shape.Solids),
            "shells": len(shape.Shells),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "vertices": len(shape.Vertexes),
        }
        shape_type = str(shape.ShapeType)
    except Exception:
        shape_valid = False
        topology = {}
        shape_type = ""
    if (
        not _live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::ShapeFromMesh"
        or str(getattr(result, "Label", "")) != prepared.label
        or getattr(result, "Source", None) is not prepared.source
        or not math.isclose(float(result.Tolerance), prepared.tolerance_mm)
        or bool(result.SewShape) is not prepared.sew_adjacent_faces
        or bool(result.MakeSolid) is not prepared.make_solid
        or not bool(result.isValid())
        or not shape_valid
        or not _live(document, prepared.source)
        or not _active(prepared.source)
        or mesh_object_state(prepared.source).get("state_sha256")
        != prepared.expected_state_sha256
        or not _history_is_exact(document, result)
    ):
        raise NativeMeshError("The linked shape-from-Mesh result failed its exact postcondition.")
    if prepared.make_solid and (shape_type != "Solid" or topology.get("solids") != 1):
        raise NativeMeshError(
            "mesh_to_solid requires exactly one solid volume; separate disconnected components first.",
            error_code="NATIVE_MESH_SINGLE_SOLID_REQUIRED",
        )
    return {
        "created": object_reference(result),
        "source": object_reference(prepared.source),
        "shape_type": shape_type,
        "representation": _FACETED_REPRESENTATION.get(shape_type, "faceted_shape"),
        "topology": topology,
        "settings": {
            "tolerance_mm": prepared.tolerance_mm,
            "sew_adjacent_faces": prepared.sew_adjacent_faces,
            "make_solid": prepared.make_solid,
        },
    }


def commit_mesh_conversion(
    document: Any,
    prepared: Any,
    *,
    publish: bool = True,
) -> NativeMutationDraft:
    """Publish one worker-verified BREP without rebuilding it in the document."""

    from VibeCADMeshConversionJob import PreparedMeshConversion

    if not isinstance(prepared, PreparedMeshConversion):
        raise TypeError("prepared must be a PreparedMeshConversion")
    request = prepared.request
    target = request.target
    if not mesh_target_still_exact(document, target):
        raise NativeMeshError(
            "The exact Mesh changed while its BREP was being prepared; the stale result was not applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    shape = load_verified_mesh_conversion_shape(prepared)

    import MeshGui
    import MeshPart  # noqa: F401 - registers MeshPart::ShapeFromMesh

    result = document.addObject(
        "MeshPart::ShapeFromMesh",
        document.getUniqueObjectName(f"{target.source.Name}_shape"),
    )
    if result is None or str(getattr(result, "TypeId", "")) != "MeshPart::ShapeFromMesh":
        raise NativeMeshError("The converted Mesh shape could not be published.")
    view = getattr(result, "ViewObject", None)
    if view is not None:
        # The source Mesh is the exact lightweight visual representation of
        # this faceted BREP.  Keep the BREP feature hidden while assigning its
        # Shape so the Part view provider does not synchronously tessellate
        # tens of thousands of planar faces on the GUI thread.
        view.Visibility = False
    result.Label = request.label
    result.UpdateFromSource = False
    result.Source = target.source
    result.Tolerance = request.tolerance_mm
    result.SewShape = request.sew_adjacent_faces
    result.MakeSolid = request.make_solid
    result.Shape = shape
    if publish:
        MeshGui.publishSourcePreservingOutputs(
            str(document.Name),
            [target.source],
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


def load_verified_mesh_conversion_shape(prepared: Any) -> Any:
    """Load one authenticated worker artifact without repeating OCC validation."""

    from VibeCADMeshConversionJob import PreparedMeshConversion

    if not isinstance(prepared, PreparedMeshConversion):
        raise TypeError("prepared must be a PreparedMeshConversion")
    try:
        import Part

        shape = Part.Shape()
        shape.importBrep(prepared.artifact_path)
    except Exception as exc:
        raise NativeMeshError(
            "The verified Mesh conversion BREP could not be imported.",
            error_code="NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
        ) from exc
    # The isolated worker already performed full BREP validation and the
    # background job authenticated this exact artifact. Repeating OCC's
    # traversal on the document thread can freeze the application.
    if shape.isNull() or str(shape.ShapeType) != prepared.shape_type:
        raise NativeMeshError(
            "The verified Mesh conversion BREP is invalid at publication.",
            error_code="NATIVE_MESH_CONVERSION_ARTIFACT_INVALID",
        )
    return shape


def verify_committed_mesh_conversion(
    document: Any,
    draft: NativeMutationDraft,
    *,
    require_operation: bool = True,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    request = prepared.request
    target = request.target
    result = draft.value["result"]
    shape = getattr(result, "Shape", None)
    try:
        shape_type = str(shape.ShapeType)
        shape_present = not shape.isNull()
    except Exception:
        shape_type = ""
        shape_present = False
    topology = dict(prepared.topology)
    if (
        not _live(document, result)
        or str(getattr(result, "TypeId", "")) != "MeshPart::ShapeFromMesh"
        or str(getattr(result, "Label", "")) != request.label
        or getattr(result, "Source", None) is not target.source
        or bool(result.UpdateFromSource) is not False
        or not math.isclose(float(result.Tolerance), request.tolerance_mm)
        or bool(result.SewShape) is not request.sew_adjacent_faces
        or bool(result.MakeSolid) is not request.make_solid
        or not bool(result.isValid())
        or not shape_present
        or shape_type != prepared.shape_type
        or not mesh_target_still_exact(document, target)
        or (require_operation and not _history_is_exact(document, result))
    ):
        raise NativeMeshError("The detached Mesh conversion failed its exact postcondition.")
    return {
        "created": object_reference(result),
        "source": object_reference(target.source),
        "shape_type": shape_type,
        "representation": prepared.representation,
        "topology": topology,
        "settings": {
            "tolerance_mm": request.tolerance_mm,
            "sew_adjacent_faces": request.sew_adjacent_faces,
            "make_solid": request.make_solid,
        },
        "conversion": {
            "background": True,
            "cache_hit": prepared.cache_hit,
            "artifact_sha256": prepared.artifact_sha256,
        },
        "display": {
            "source_mesh_visible": bool(getattr(target.source, "Visibility", False)),
            "converted_shape_visible": bool(getattr(result, "Visibility", False)),
        },
    }
