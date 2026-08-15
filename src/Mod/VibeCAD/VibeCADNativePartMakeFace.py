# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Face From Wires preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    ALL_PART_SHAPE_TYPES,
    CurrentPartSource,
    current_part_source_is_exact,
    resolve_current_part_source,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
)


_DEFINITION_FIELDS = frozenset({"sources"})
_MAX_SOURCES = 32
_FACE_MAKER = "Part::FaceMakerUnified"


@dataclass(frozen=True, slots=True)
class PartMakeFaceSpec:
    source_refs: tuple[NativeObjectRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedPartMakeFace:
    spec: PartMakeFaceSpec
    sources: tuple[CurrentPartSource, ...]


def _object_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError("A Face From Wires source target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _source_refs(document_uid: str, value: Any) -> tuple[NativeObjectRef, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SOURCES:
        raise NativeModelError("Face From Wires requires 1 to 32 exact source objects.")
    refs = tuple(_object_ref(document_uid, item) for item in value)
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Face From Wires source objects must be unique.")
    return refs


def prepare_part_make_face(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartMakeFaceSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError(
            "A Face From Wires definition must contain its exact sources."
        )
    return PartMakeFaceSpec(_source_refs(document_uid, value["sources"]))


def _validate_face_source(source: CurrentPartSource) -> None:
    shape = source.shape
    if tuple(getattr(shape, "Faces", ()) or ()):
        raise NativeModelError("A Face From Wires source cannot already contain faces.")
    wires = tuple(getattr(shape, "Wires", ()) or ())
    if not wires:
        raise NativeModelError("A Face From Wires source must contain a closed wire.")
    if any(not bool(wire.isClosed()) for wire in wires):
        raise NativeModelError("Every wire in a Face From Wires source must be closed.")


def preflight_part_make_face(
    document: Any,
    spec: PartMakeFaceSpec,
) -> PreparedPartMakeFace:
    if not isinstance(spec, PartMakeFaceSpec):
        raise TypeError("spec must be a PartMakeFaceSpec")
    sources = tuple(
        resolve_current_part_source(
            document,
            reference,
            operation="Face From Wires",
            allowed_types=ALL_PART_SHAPE_TYPES,
            reject_solid_compounds=False,
        )
        for reference in spec.source_refs
    )
    targets = tuple(source.target for source in sources)
    if len(targets) != len(set(targets)):
        raise NativeModelError(
            "Face From Wires sources resolve to duplicate current shapes."
        )
    for source in sources:
        _validate_face_source(source)
    return PreparedPartMakeFace(spec, sources)


def _visible_presentations(sources: tuple[CurrentPartSource, ...]) -> tuple[Any, ...]:
    presentations = []
    for source in sources:
        presentation = source.presentation
        if presentation is None or presentation in presentations:
            continue
        try:
            visible = bool(presentation.Visibility)
        except Exception:
            visible = False
        if visible:
            presentations.append(presentation)
    return tuple(presentations)


def create_part_make_face(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartMakeFace,
) -> NativeMutationDraft:
    import PartGui

    if any(
        not current_part_source_is_exact(document, source)
        for source in prepared.sources
    ):
        raise NativeModelError("A Face From Wires source changed after preflight.")

    result = document.addObject("Part::Face", "Face")
    if result is None or str(getattr(result, "TypeId", "")) != "Part::Face":
        raise NativeModelError("The Face From Wires factory returned the wrong object type.")
    result.Label = label
    result.FaceMakerClass = _FACE_MAKER
    result.Sources = tuple(source.target for source in prepared.sources)

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or tuple(shape.Solids)
    ):
        message = str(
            result.getStatusString()
            or "Face From Wires did not produce valid face geometry."
        )
        raise NativeModelError(message)

    PartGui.publishDesignDefinitionBlock((result,))
    presentations = _visible_presentations(prepared.sources)
    if presentations:
        if not PartGui.setModelingReplacedInputs(result, presentations):
            raise NativeModelError("Face From Wires could not retain its replaced inputs.")
        for presentation in presentations:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "result": result,
            "presentations": presentations,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_make_face(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    sources = tuple(source.target for source in prepared.sources)
    shape = result.Shape
    if document.getObject(result.Name) is not result or result.TypeId != "Part::Face":
        raise NativeModelError("The Face From Wires result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Face From Wires result changed its label.")
    if tuple(result.Sources) != sources or str(result.FaceMakerClass) != _FACE_MAKER:
        raise NativeModelError("The Face From Wires result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or tuple(shape.Solids)
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Face From Wires result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
    ):
        raise NativeModelError("The Face From Wires Design identity did not persist.")
    if tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ()) != tuple(
        draft.value["presentations"]
    ):
        raise NativeModelError("The Face From Wires replaced-input set changed.")
    for index, source in enumerate(prepared.sources):
        if not current_part_source_is_exact(document, source):
            raise NativeModelError(
                f"Face From Wires source {index + 1} changed before commit."
            )
        _validate_face_source(source)

    return {
        "root": object_reference(result),
        "source_count": len(sources),
        "shape_type": str(shape.ShapeType),
        "face_count": len(shape.Faces),
        "area_mm2": float(shape.Area),
    }
