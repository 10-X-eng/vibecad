# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Shape Builder preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_ELEMENT = {
    "edge_from_vertices": ("Vertex", re.compile(r"^Vertex[1-9][0-9]*$")),
    "wire_from_edges": ("Edge", re.compile(r"^Edge[1-9][0-9]*$")),
    "face_from_vertices": ("Vertex", re.compile(r"^Vertex[1-9][0-9]*$")),
    "face_from_edges": ("Edge", re.compile(r"^Edge[1-9][0-9]*$")),
    "shell_from_faces": ("Face", re.compile(r"^Face[1-9][0-9]*$")),
}
_FIELDS_BY_KIND = {
    "edge_from_vertices": frozenset({"kind", "inputs"}),
    "wire_from_edges": frozenset({"kind", "inputs"}),
    "face_from_vertices": frozenset({"kind", "inputs", "planar"}),
    "face_from_edges": frozenset({"kind", "inputs", "planar"}),
    "shell_from_faces": frozenset({"kind", "inputs", "all_faces", "refine"}),
    "solid_from_shell": frozenset({"kind", "source", "refine"}),
}
_RESULT_INFO = {
    "edge_from_vertices": ("Edge", "Edge"),
    "wire_from_edges": ("Wire", "Wire"),
    "face_from_vertices": ("Face", "Face"),
    "face_from_edges": ("Face", "Face"),
    "shell_from_faces": ("Shell", "Shell"),
    "solid_from_shell": ("Solid", "Solid"),
}
_MAX_INPUT_GROUPS = 32
_MAX_ELEMENTS_PER_GROUP = 64
_MAX_SELECTED_ELEMENTS = 256
_MAX_EXPANDED_FACES = 512
_TOLERANCE = 1.0e-9


@dataclass(frozen=True, slots=True)
class PartBuilderInputSpec:
    object_ref: NativeObjectRef
    subelements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PartBuilderSpec:
    kind: str
    inputs: tuple[PartBuilderInputSpec, ...] = ()
    source_ref: NativeObjectRef | None = None
    planar: bool | None = None
    refine: bool | None = None
    all_faces: bool | None = None


@dataclass(frozen=True, slots=True)
class PreparedPartBuilderShape:
    spec: PartBuilderSpec
    shape: Any
    source_count: int
    selected_element_count: int


def part_builder_definition_fields() -> dict[str, frozenset[str]]:
    return dict(_FIELDS_BY_KIND)


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Shape Builder {field} must be true or false.")
    return value


def _input_specs(
    document_uid: str,
    value: Any,
    *,
    kind: str,
) -> tuple[PartBuilderInputSpec, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_INPUT_GROUPS:
        raise NativeModelError("Shape Builder requires 1 to 32 exact input objects.")
    _shape_type, pattern = _ELEMENT[kind]
    result = []
    object_names = set()
    total = 0
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "object_name",
            "subelements",
        }:
            raise NativeModelError("A Shape Builder input is invalid.")
        object_ref = NativeObjectRef(document_uid, str(item["object_name"] or ""))
        if object_ref.object_name in object_names:
            raise NativeModelError("Shape Builder inputs must group each object exactly once.")
        object_names.add(object_ref.object_name)
        subelements = item["subelements"]
        if not isinstance(subelements, list) or not 1 <= len(subelements) <= 64:
            raise NativeModelError(
                "Each Shape Builder input requires 1 to 64 exact subelements."
            )
        names = tuple(str(name) for name in subelements)
        if len(names) != len(set(names)) or any(pattern.fullmatch(name) is None for name in names):
            raise NativeModelError(
                f"Shape Builder {kind} inputs contain the wrong subelement type."
            )
        total += len(names)
        result.append(PartBuilderInputSpec(object_ref, names))
    if total > _MAX_SELECTED_ELEMENTS:
        raise NativeModelError("Shape Builder accepts at most 256 selected subelements.")
    return tuple(result)


def prepare_part_builder(document_uid: str, value: Mapping[str, Any]) -> PartBuilderSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Shape Builder definition must be an object.")
    values = dict(value)
    kind = str(values.get("kind") or "").strip()
    expected = _FIELDS_BY_KIND.get(kind)
    if expected is None or set(values) != expected:
        raise NativeModelError("The Shape Builder definition does not match its kind.")
    if kind == "solid_from_shell":
        source = values["source"]
        if not isinstance(source, Mapping) or set(source) != {"object_name"}:
            raise NativeModelError("A Shape Builder solid requires one exact shell object.")
        return PartBuilderSpec(
            kind=kind,
            source_ref=NativeObjectRef(document_uid, str(source["object_name"] or "")),
            refine=_boolean(values["refine"], "refine"),
        )

    inputs = _input_specs(document_uid, values["inputs"], kind=kind)
    element_count = sum(len(item.subelements) for item in inputs)
    if kind == "edge_from_vertices" and element_count != 2:
        raise NativeModelError("An edge requires exactly two vertices.")
    if kind == "face_from_vertices" and element_count < 3:
        raise NativeModelError("A face from vertices requires at least three vertices.")
    if kind == "shell_from_faces":
        all_faces = _boolean(values["all_faces"], "all_faces")
        if not all_faces and element_count < 2:
            raise NativeModelError("A shell requires at least two faces.")
        return PartBuilderSpec(
            kind=kind,
            inputs=inputs,
            refine=_boolean(values["refine"], "refine"),
            all_faces=all_faces,
        )
    if kind in {"face_from_vertices", "face_from_edges"}:
        return PartBuilderSpec(
            kind=kind,
            inputs=inputs,
            planar=_boolean(values["planar"], "planar"),
        )
    return PartBuilderSpec(kind=kind, inputs=inputs)


def _resolve_shape(document: Any, reference: NativeObjectRef) -> tuple[Any, Any]:
    import PartGui

    visible = resolve_object(document, reference)
    is_derived = getattr(visible, "isDerivedFrom", None)
    if not callable(is_derived) or not is_derived("Part::Feature"):
        raise NativeModelError("A Shape Builder source must be a Part shape object.")
    if not PartGui.isModelingObjectActive(visible):
        raise NativeModelError("A Shape Builder source is not active in current History.")
    source = PartGui.resolveModelingObject(visible)
    if source is None or getattr(source, "Document", None) is not document:
        raise NativeModelError("A Shape Builder source has no current modeling state.")
    shape = getattr(source, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeModelError("A Shape Builder source has no valid current shape.")
    return source, shape


def _selected_elements(
    document: Any,
    spec: PartBuilderSpec,
) -> tuple[list[Any], tuple[Any, ...]]:
    expected_type = _ELEMENT[spec.kind][0]
    elements = []
    sources = []
    canonical = set()
    for input_spec in spec.inputs:
        source, shape = _resolve_shape(document, input_spec.object_ref)
        if source not in sources:
            sources.append(source)
        for name in input_spec.subelements:
            try:
                element = shape.getElement(name)
            except Exception as exc:
                raise NativeModelError(
                    "An exact Shape Builder subelement no longer exists."
                ) from exc
            key = (str(getattr(source, "Name", "")), name)
            if key in canonical or element.ShapeType != expected_type:
                raise NativeModelError("A Shape Builder input is duplicated or has changed type.")
            canonical.add(key)
            elements.append(element)
    return elements, tuple(sources)


def _build_shape(document: Any, spec: PartBuilderSpec) -> tuple[Any, int, int]:
    import Part

    if spec.kind == "solid_from_shell":
        source, source_shape = _resolve_shape(document, spec.source_ref)
        if source_shape.ShapeType != "Shell":
            raise NativeModelError("A Shape Builder solid source must be exactly one shell.")
        shape = Part.Solid(source_shape.copy())
        if spec.refine:
            shape = shape.removeSplitter()
        return shape, 1, 0

    elements, sources = _selected_elements(document, spec)
    selected_count = len(elements)
    try:
        if spec.kind == "edge_from_vertices":
            first, second = (element.Point for element in elements)
            if first.distanceToPoint(second) <= _TOLERANCE:
                raise NativeModelError("An edge requires two geometrically distinct vertices.")
            shape = Part.makeLine(first, second)
        elif spec.kind == "wire_from_edges":
            shape = Part.Wire(Part.__sortEdges__(elements))
        elif spec.kind == "face_from_vertices":
            polygon = Part.makePolygon([element.Point for element in elements], True)
            shape = Part.Face(polygon) if spec.planar else Part.makeFilledFace(polygon.Edges)
        elif spec.kind == "face_from_edges":
            sorted_edges = Part.__sortEdges__(elements)
            shape = (
                Part.Face(Part.Wire(sorted_edges))
                if spec.planar
                else Part.makeFilledFace(sorted_edges)
            )
        else:
            faces = elements
            if spec.all_faces:
                faces = []
                for source in sources:
                    faces.extend(list(source.Shape.Faces))
                if len(faces) > _MAX_EXPANDED_FACES:
                    raise NativeModelError(
                        "Shape Builder all_faces expands to more than 512 faces."
                    )
            if len(faces) < 2:
                raise NativeModelError("A shell requires at least two faces.")
            shape = Part.Shell(faces)
            if spec.refine:
                shape = shape.removeSplitter()
    except NativeModelError:
        raise
    except Exception as exc:
        raise NativeModelError("The selected Shape Builder geometry cannot form that shape.") from exc
    return shape, len(sources), selected_count


def preflight_part_builder(document: Any, spec: PartBuilderSpec) -> PreparedPartBuilderShape:
    shape, source_count, selected_count = _build_shape(document, spec)
    expected_type = _RESULT_INFO[spec.kind][1]
    if shape.isNull() or not shape.isValid() or shape.ShapeType != expected_type:
        raise NativeModelError("Shape Builder did not produce its expected valid shape.")
    if expected_type in {"Edge", "Wire"} and float(shape.Length) <= _TOLERANCE:
        raise NativeModelError("Shape Builder produced zero-length geometry.")
    if expected_type in {"Face", "Shell"} and float(shape.Area) <= _TOLERANCE:
        raise NativeModelError("Shape Builder produced zero-area geometry.")
    if expected_type == "Solid" and float(shape.Volume) <= _TOLERANCE:
        raise NativeModelError("Shape Builder produced a zero-volume solid.")
    return PreparedPartBuilderShape(
        spec=spec,
        shape=shape,
        source_count=source_count,
        selected_element_count=selected_count,
    )


def create_part_builder_shape(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartBuilderShape,
) -> NativeMutationDraft:
    import PartDesign

    base_name, _shape_type = _RESULT_INFO[prepared.spec.kind]
    obj = document.addObject("Part::Feature", base_name)
    if obj is None or str(getattr(obj, "TypeId", "")) != "Part::Feature":
        raise NativeModelError("The Shape Builder factory returned the wrong object type.")
    obj.Label = label
    obj.Shape = prepared.shape
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    recomputed = document.recompute([obj], True, True)
    if recomputed is False or not obj.isValid():
        raise NativeModelError(str(obj.getStatusString() or "The built Part shape is invalid."))
    PartDesign.finalizeDesignDefinition(obj)
    return NativeMutationDraft(
        value={"object": obj, "label": label, "prepared": prepared},
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def verify_part_builder_shape(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    obj = draft.value["object"]
    prepared = draft.value["prepared"]
    shape = obj.Shape
    expected_type = _RESULT_INFO[prepared.spec.kind][1]
    if (
        document.getObject(obj.Name) is not obj
        or obj.TypeId != "Part::Feature"
        or str(obj.Label) != draft.value["label"]
        or obj.getParentGeoFeatureGroup() is not None
        or str(getattr(obj, "VibeCADTimelineRole", "") or "") != "operation"
        or not str(getattr(obj, "VibeCADDefinitionId", "") or "")
        or not str(getattr(obj, "DesignId", "") or "")
        or not obj.isValid()
        or shape.isNull()
        or not shape.isValid()
        or shape.ShapeType != expected_type
        or not bool(shape.isPartner(prepared.shape))
        or shape.Placement != prepared.shape.Placement
        or str(shape.Orientation) != str(prepared.shape.Orientation)
    ):
        raise NativeModelError("The built Part shape failed its exact postcondition.")
    result: dict[str, Any] = {
        "object": object_reference(obj),
        "builder_kind": prepared.spec.kind,
        "shape_type": shape.ShapeType,
        "source_count": prepared.source_count,
        "selected_element_count": prepared.selected_element_count,
        "vertex_count": len(shape.Vertexes),
        "edge_count": len(shape.Edges),
        "face_count": len(shape.Faces),
    }
    if shape.ShapeType in {"Edge", "Wire"}:
        result["length_mm"] = float(shape.Length)
    elif shape.ShapeType in {"Face", "Shell"}:
        result["area_mm2"] = float(shape.Area)
    else:
        result["volume_mm3"] = float(shape.Volume)
    return result
