# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Mirror preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    ALL_PART_SHAPE_TYPES,
    CurrentPartElement,
    CurrentPartSource,
    close_number,
    copy_part_visual,
    current_part_element_is_exact,
    current_part_source_is_exact,
    grouped_result_labels,
    link_sub,
    resolve_current_part_element,
    resolve_current_part_source,
    resolve_current_part_target,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
)


_DEFINITION_FIELDS = frozenset({"sources", "plane"})
_STANDARD_PLANES = frozenset({"xy", "xz", "yz"})
_REFERENCE_NAME = re.compile(r"^(?:Face|Edge)[1-9][0-9]*$")
_MAX_SOURCES = 32
_MAX_COORDINATE = 1_000_000.0
_PLANE_SURFACE = "Part::GeomPlane"
_CIRCLE_CURVE = "Part::GeomCircle"


@dataclass(frozen=True, slots=True)
class PartMirrorPlaneSpec:
    kind: str
    base: tuple[float, float, float] | None
    reference: NativeObjectRef | None
    subelement: str | None


@dataclass(frozen=True, slots=True)
class PartMirrorSpec:
    source_refs: tuple[NativeObjectRef, ...]
    plane: PartMirrorPlaneSpec


@dataclass(frozen=True, slots=True)
class ResolvedPartMirrorPlane:
    target: Any
    reference_kind: str
    element: CurrentPartElement | None
    placement_signature: tuple[float, ...] | None


@dataclass(frozen=True, slots=True)
class PreparedPartMirror:
    spec: PartMirrorSpec
    sources: tuple[CurrentPartSource, ...]
    plane: ResolvedPartMirrorPlane | None


def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Part Mirror {name} must be a number.") from exc
    if not math.isfinite(number) or abs(number) > _MAX_COORDINATE:
        raise NativeModelError(f"Part Mirror {name} is outside its finite range.")
    return number


def _vector(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError(f"A Part Mirror {name} vector is invalid.")
    return tuple(_number(value[axis], f"{name} {axis}") for axis in "xyz")


def _object_ref(document_uid: str, value: Any, *, name: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Part Mirror {name} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _source_refs(document_uid: str, value: Any) -> tuple[NativeObjectRef, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SOURCES:
        raise NativeModelError("Part Mirror requires 1 to 32 exact source objects.")
    refs = tuple(_object_ref(document_uid, item, name="source") for item in value)
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Mirror source objects must be unique.")
    return refs


def _plane(document_uid: str, value: Any) -> PartMirrorPlaneSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Part Mirror plane is invalid.")
    values = dict(value)
    kind = str(values.get("kind") or "").strip()
    if kind in _STANDARD_PLANES:
        if set(values) not in ({"kind"}, {"kind", "base_mm"}):
            raise NativeModelError("The Part Mirror plane fields do not match its kind.")
        return PartMirrorPlaneSpec(
            kind,
            _vector(
                values.get("base_mm", {"x": 0.0, "y": 0.0, "z": 0.0}),
                "plane base",
            ),
            None,
            None,
        )
    if kind != "reference" or set(values) != {"kind", "reference"}:
        raise NativeModelError("The Part Mirror plane fields do not match its kind.")
    reference = values["reference"]
    if not isinstance(reference, Mapping) or set(reference) not in (
        {"object_name"},
        {"object_name", "subelement"},
    ):
        raise NativeModelError("A Part Mirror plane reference is invalid.")
    subelement = (
        str(reference["subelement"] or "") if "subelement" in reference else None
    )
    if subelement is not None and _REFERENCE_NAME.fullmatch(subelement) is None:
        raise NativeModelError("A Part Mirror reference requires an exact FaceN or EdgeN.")
    return PartMirrorPlaneSpec(
        kind,
        None,
        NativeObjectRef(document_uid, str(reference["object_name"] or "")),
        subelement,
    )


def prepare_part_mirror(document_uid: str, value: Mapping[str, Any]) -> PartMirrorSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Mirror definition must contain its exact controls.")
    values = dict(value)
    return PartMirrorSpec(
        source_refs=_source_refs(document_uid, values["sources"]),
        plane=_plane(document_uid, values["plane"]),
    )


def _derived(target: Any, type_id: str) -> bool:
    check = getattr(target, "isDerivedFrom", None)
    try:
        return bool(check(type_id)) if callable(check) else False
    except Exception:
        return False


def _is_plane_object(target: Any) -> bool:
    return (
        _derived(target, "Part::Plane")
        or _derived(target, "App::Plane")
        or ("Plane" in str(getattr(target, "Name", "")) and _derived(target, "Part::Datum"))
    )


def _placement_signature(target: Any) -> tuple[float, ...]:
    placement = getattr(target, "Placement", None)
    if placement is None:
        raise NativeModelError("The Part Mirror plane object has no placement.")
    try:
        return (
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
            *(float(value) for value in placement.Rotation.Q),
        )
    except Exception as exc:
        raise NativeModelError("The Part Mirror plane placement is invalid.") from exc


def _validate_face(shape: Any) -> None:
    if str(shape.ShapeType) != "Face" or str(
        getattr(getattr(shape, "Surface", None), "TypeId", "")
    ) != _PLANE_SURFACE:
        raise NativeModelError("A Part Mirror face reference must be planar.")


def _validate_edge(shape: Any) -> None:
    if str(shape.ShapeType) != "Edge" or str(
        getattr(getattr(shape, "Curve", None), "TypeId", "")
    ) != _CIRCLE_CURVE:
        raise NativeModelError("A Part Mirror edge reference must be circular.")


def _resolve_plane(document: Any, spec: PartMirrorPlaneSpec) -> ResolvedPartMirrorPlane:
    if spec.reference is None:
        raise TypeError("A reference plane requires an exact object reference")
    _visible, target = resolve_current_part_target(
        document,
        spec.reference,
        operation="Part Mirror plane",
    )
    if _is_plane_object(target):
        if spec.subelement is not None:
            raise NativeModelError("A Part Mirror plane object cannot use a subelement.")
        return ResolvedPartMirrorPlane(
            target,
            "plane_object",
            None,
            _placement_signature(target),
        )
    if not (_derived(target, "Part::Feature") or _derived(target, "App::Link")):
        raise NativeModelError(
            "A Part Mirror reference must be a plane, planar face, or circular edge."
        )

    element = resolve_current_part_element(
        document,
        spec.reference,
        subelement=spec.subelement,
        operation="Part Mirror plane",
    )
    if spec.subelement:
        if spec.subelement.startswith("Face"):
            _validate_face(element.shape)
            reference_kind = "planar_face"
        else:
            _validate_edge(element.shape)
            reference_kind = "circular_edge"
        return ResolvedPartMirrorPlane(target, reference_kind, element, None)

    shape = element.shape
    faces = tuple(getattr(shape, "Faces", ()) or ())
    edges = tuple(getattr(shape, "Edges", ()) or ())
    if len(faces) == 1:
        _validate_face(faces[0])
        reference_kind = "planar_face"
    elif len(edges) == 1:
        _validate_edge(edges[0])
        reference_kind = "circular_edge"
    else:
        raise NativeModelError(
            "A whole-object Part Mirror reference needs exactly one planar face or circular edge."
        )
    return ResolvedPartMirrorPlane(target, reference_kind, element, None)


def preflight_part_mirror(document: Any, spec: PartMirrorSpec) -> PreparedPartMirror:
    if not isinstance(spec, PartMirrorSpec):
        raise TypeError("spec must be a PartMirrorSpec")
    sources = tuple(
        resolve_current_part_source(
            document,
            reference,
            operation="Part Mirror",
            allowed_types=ALL_PART_SHAPE_TYPES,
            reject_solid_compounds=False,
        )
        for reference in spec.source_refs
    )
    targets = tuple(source.target for source in sources)
    if len(targets) != len(set(targets)):
        raise NativeModelError("Part Mirror sources resolve to duplicate current shapes.")
    plane = _resolve_plane(document, spec.plane) if spec.plane.reference else None
    return PreparedPartMirror(spec, sources, plane)


def _plane_is_exact(document: Any, plane: ResolvedPartMirrorPlane) -> bool:
    name = str(getattr(plane.target, "Name", "") or "")
    if not name or document.getObject(name) is not plane.target:
        return False
    if plane.element is not None:
        return current_part_element_is_exact(document, plane.element)
    try:
        import PartGui

        return (
            PartGui.isModelingObjectActive(plane.target)
            and _is_plane_object(plane.target)
            and _placement_signature(plane.target) == plane.placement_signature
        )
    except Exception:
        return False


def _mirror_plane_link(plane: ResolvedPartMirrorPlane | None, spec: PartMirrorPlaneSpec) -> Any:
    if plane is None:
        return None
    if spec.subelement:
        return plane.target, [spec.subelement]
    return plane.target


def create_part_mirror(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartMirror,
) -> NativeMutationDraft:
    import FreeCAD as App
    import PartGui

    if any(
        not current_part_source_is_exact(document, source)
        for source in prepared.sources
    ):
        raise NativeModelError("A Part Mirror source changed after preflight.")
    if prepared.plane is not None and not _plane_is_exact(document, prepared.plane):
        raise NativeModelError("The Part Mirror plane changed after preflight.")

    spec = prepared.spec
    normals = {"xy": (0.0, 0.0, 1.0), "xz": (0.0, 1.0, 0.0), "yz": (1.0, 0.0, 0.0)}
    result_labels = grouped_result_labels(label, len(prepared.sources))
    results = []
    for source, result_label in zip(prepared.sources, result_labels, strict=True):
        result = document.addObject("Part::Mirroring", "Mirroring")
        if result is None or str(getattr(result, "TypeId", "")) != "Part::Mirroring":
            raise NativeModelError("The Part Mirror factory returned the wrong object type.")
        result.Label = result_label
        result.Source = source.target
        result.MirrorPlane = _mirror_plane_link(prepared.plane, spec.plane)
        if spec.plane.base is not None:
            result.Base = App.Vector(*spec.plane.base)
            result.Normal = App.Vector(*normals[spec.plane.kind])
        copy_part_visual(source.target, result, include_part_2d=True)
        results.append(result)

    recomputed = document.recompute(results, True, True)
    if recomputed is False:
        raise NativeModelError("Part Mirror results failed to recompute.")
    for result in results:
        shape = result.Shape
        if not result.isValid() or shape.isNull() or not shape.isValid():
            raise NativeModelError(
                str(result.getStatusString() or "Part Mirror produced an invalid shape.")
            )

    root = results[-1]
    presentations = []
    for source in prepared.sources:
        presentation = source.presentation
        if presentation is not None and presentation not in presentations:
            try:
                visible = bool(presentation.Visibility)
            except Exception:
                visible = False
            if visible:
                presentations.append(presentation)
    PartGui.publishDesignDefinitionBlock(tuple(results))
    if presentations:
        if not PartGui.setModelingReplacedInputs(root, tuple(presentations)):
            raise NativeModelError("Part Mirror could not retain its replaced inputs.")
        for presentation in presentations:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "result_labels": result_labels,
            "prepared": prepared,
            "results": tuple(results),
            "presentations": tuple(presentations),
        },
        recompute_targets=tuple(results),
        created=tuple(object_identity(result) for result in results),
    )


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(float(getattr(value, axis)) for axis in "xyz")


def verify_part_mirror(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    results = draft.value["results"]
    root = results[-1]
    expected_link = (
        (
            prepared.plane.target,
            (spec.plane.subelement,) if spec.plane.subelement else (),
        )
        if prepared.plane is not None
        else (None, ())
    )
    normals = {"xy": (0.0, 0.0, 1.0), "xz": (0.0, 1.0, 0.0), "yz": (1.0, 0.0, 0.0)}
    for index, (source, result) in enumerate(
        zip(prepared.sources, results, strict=True)
    ):
        shape = result.Shape
        expected_role = "operation" if result is root else "resource"
        owner = getattr(result, "VibeCADTimelineOwner", None)
        if document.getObject(result.Name) is not result or result.TypeId != "Part::Mirroring":
            raise NativeModelError(f"Part Mirror result {index + 1} lost its identity.")
        if str(result.Label) != draft.value["result_labels"][index]:
            raise NativeModelError(f"Part Mirror result {index + 1} changed its label.")
        if result.Source is not source.target or link_sub(result.MirrorPlane) != expected_link:
            raise NativeModelError(f"Part Mirror result {index + 1} changed its targets.")
        if spec.plane.base is not None:
            if any(
                not close_number(actual, expected)
                for actual, expected in zip(
                    _vector_tuple(result.Base),
                    spec.plane.base,
                    strict=True,
                )
            ) or any(
                not close_number(actual, expected)
                for actual, expected in zip(
                    _vector_tuple(result.Normal),
                    normals[spec.plane.kind],
                    strict=True,
                )
            ):
                raise NativeModelError("The explicit Part Mirror plane changed.")
        if (
            not result.isValid()
            or shape.isNull()
            or not shape.isValid()
            or result.getParentGeoFeatureGroup() is not None
        ):
            raise NativeModelError(f"Part Mirror result {index + 1} is not valid at root.")
        if (
            str(getattr(result, "VibeCADTimelineRole", "") or "") != expected_role
            or (result is root and owner is not None)
            or (result is not root and owner is not root)
        ):
            raise NativeModelError(f"Part Mirror result {index + 1} has invalid ownership.")
        if not current_part_source_is_exact(document, source):
            raise NativeModelError(f"Part Mirror source {index + 1} changed before commit.")

    if prepared.plane is not None and not _plane_is_exact(document, prepared.plane):
        raise NativeModelError("The Part Mirror plane changed before commit.")
    if (
        not str(getattr(root, "VibeCADDefinitionId", "") or "")
        or not str(getattr(root, "DesignId", "") or "")
    ):
        raise NativeModelError("The Part Mirror Design identity did not persist.")
    expected_presentations = tuple(draft.value["presentations"])
    actual_presentations = tuple(
        getattr(root, "VibeCADTimelineReplacedInputs", ()) or ()
    )
    if actual_presentations != expected_presentations:
        raise NativeModelError("The Part Mirror replaced-input set changed.")

    shapes = tuple(result.Shape for result in results)
    shape_types = tuple(dict.fromkeys(str(shape.ShapeType) for shape in shapes))
    return {
        "root": object_reference(root),
        "source_count": len(prepared.sources),
        "result_count": len(results),
        "resource_count": len(results) - 1,
        "plane_mode": spec.plane.kind,
        "reference_kind": prepared.plane.reference_kind if prepared.plane else None,
        "shape_types": list(shape_types),
        "total_length_mm": sum(float(shape.Length) for shape in shapes),
        "total_area_mm2": sum(float(shape.Area) for shape in shapes),
        "total_volume_mm3": sum(float(shape.Volume) for shape in shapes),
    }
