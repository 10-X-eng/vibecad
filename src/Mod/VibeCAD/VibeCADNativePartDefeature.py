# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Defeaturing preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    grouped_result_labels,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"sources"})
_SOURCE_FIELDS = frozenset({"object_name", "faces"})
_FACE_NAME = re.compile(r"^Face[1-9][0-9]*$")
_MAX_SOURCES = 32
_MAX_FACES_PER_SOURCE = 64
_MAX_TOTAL_FACES = 256


@dataclass(frozen=True, slots=True)
class PartDefeatureSourceSpec:
    object_ref: NativeObjectRef
    faces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PartDefeatureSpec:
    sources: tuple[PartDefeatureSourceSpec, ...]


@dataclass(frozen=True, slots=True)
class PreparedPartDefeatureSource:
    spec: PartDefeatureSourceSpec
    element: CurrentPartElement
    presentation: Any
    presentation_was_visible: bool
    output_shape: Any
    output_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class PreparedPartDefeature:
    spec: PartDefeatureSpec
    sources: tuple[PreparedPartDefeatureSource, ...]


def _object_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != _SOURCE_FIELDS:
        raise NativeModelError("A Part Defeaturing source target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def prepare_part_defeature(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartDefeatureSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError(
            "A Part Defeaturing definition must contain its exact sources."
        )
    raw_sources = value["sources"]
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= _MAX_SOURCES:
        raise NativeModelError("Part Defeaturing requires 1 to 32 exact source selections.")

    sources = []
    total_faces = 0
    for raw_source in raw_sources:
        reference = _object_ref(document_uid, raw_source)
        raw_faces = raw_source["faces"]
        if not isinstance(raw_faces, list) or not 1 <= len(raw_faces) <= _MAX_FACES_PER_SOURCE:
            raise NativeModelError(
                "Each Part Defeaturing source requires 1 to 64 selected faces."
            )
        faces = tuple(str(item or "") for item in raw_faces)
        if (
            len(faces) != len(set(faces))
            or any(_FACE_NAME.fullmatch(item) is None for item in faces)
        ):
            raise NativeModelError(
                "Part Defeaturing faces must be distinct exact FaceN names."
            )
        total_faces += len(faces)
        sources.append(PartDefeatureSourceSpec(reference, faces))

    names = tuple(source.object_ref.object_name for source in sources)
    if len(names) != len(set(names)):
        raise NativeModelError(
            "Each Part Defeaturing object must appear once with all selected faces."
        )
    if total_faces > _MAX_TOTAL_FACES:
        raise NativeModelError("Part Defeaturing accepts at most 256 selected faces.")
    return PartDefeatureSpec(tuple(sources))


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def _shape_fingerprint(shape: Any) -> str | None:
    try:
        return hashlib.sha256(shape.exportBrepToString().encode("utf-8")).hexdigest()
    except Exception:
        return None


def _shape_is_exact(current: Any, expected: Any, fingerprint: str | None) -> bool:
    try:
        if (
            current is not None
            and not current.isNull()
            and current.isPartner(expected)
            and current.Placement == expected.Placement
            and str(current.Orientation) == str(expected.Orientation)
        ):
            return True
        return fingerprint is not None and _shape_fingerprint(current) == fingerprint
    except Exception:
        return False


def _defeature_shape(shape: Any, names: tuple[str, ...]) -> Any:
    selected = []
    for name in names:
        try:
            face = shape.getElement(name)
        except Exception as exc:
            raise NativeModelError(
                f"Part Defeaturing can no longer resolve {name}."
            ) from exc
        if (
            face is None
            or face.isNull()
            or not face.isValid()
            or str(face.ShapeType) != "Face"
        ):
            raise NativeModelError(
                "Part Defeaturing selections must resolve to exact valid faces."
            )
        selected.append(face)
    try:
        output = shape.defeaturing(selected)
    except Exception as exc:
        raise NativeModelError(
            "The selected faces cannot be removed and healed into a valid shape."
        ) from exc
    if (
        output is None
        or output.isNull()
        or not output.isValid()
        or (
            output.isPartner(shape)
            and output.Placement == shape.Placement
            and str(output.Orientation) == str(shape.Orientation)
        )
    ):
        raise NativeModelError("Part Defeaturing did not remove the selected feature faces.")
    return output


def preflight_part_defeature(
    document: Any,
    spec: PartDefeatureSpec,
) -> PreparedPartDefeature:
    import PartGui

    if not isinstance(spec, PartDefeatureSpec):
        raise TypeError("spec must be a PartDefeatureSpec")
    prepared = []
    targets = []
    for source in spec.sources:
        element = resolve_current_part_element(
            document,
            source.object_ref,
            subelement=None,
            operation="Part Defeaturing source",
        )
        shape = element.shape
        if not tuple(getattr(shape, "Faces", ()) or ()):
            raise NativeModelError("A Part Defeaturing source has no removable faces.")
        presentation = PartGui.resolveModelingPresentationObject(element.target)
        if presentation is None:
            presentation = element.target
        output = _defeature_shape(shape, source.faces)
        prepared.append(
            PreparedPartDefeatureSource(
                source,
                element,
                presentation,
                _visible(presentation),
                output,
                _shape_fingerprint(output),
            )
        )
        targets.append(element.target)
    if len(targets) != len(set(targets)):
        raise NativeModelError(
            "Part Defeaturing sources resolve to duplicate current modeling shapes."
        )
    return PreparedPartDefeature(spec, tuple(prepared))


def _sources_are_exact(document: Any, prepared: PreparedPartDefeature) -> bool:
    return all(
        current_part_element_is_exact(document, source.element)
        for source in prepared.sources
    )


def create_part_defeature(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartDefeature,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedPartDefeature):
        raise TypeError("prepared must be a PreparedPartDefeature")
    if not _sources_are_exact(document, prepared):
        raise NativeModelError("A Part Defeaturing source changed after preflight.")

    labels = grouped_result_labels(label, len(prepared.sources))
    results = []
    for source, result_label in zip(prepared.sources, labels, strict=True):
        result = document.addObject("Part::Feature", "Defeatured")
        if result is None or str(getattr(result, "TypeId", "")) != "Part::Feature":
            raise NativeModelError(
                "The Part Defeaturing factory returned the wrong object type."
            )
        result.Label = result_label
        result.Shape = source.output_shape
        results.append(result)

    recomputed = document.recompute(results, True, True)
    if recomputed is False:
        raise NativeModelError("Part Defeaturing results failed to recompute.")
    for source, result in zip(prepared.sources, results, strict=True):
        shape = result.Shape
        if not result.isValid():
            raise NativeModelError(
                str(
                    result.getStatusString()
                    or "Part Defeaturing produced an invalid healed shape."
                )
            )
        if shape.isNull() or not shape.isValid():
            raise NativeModelError(
                "Part Defeaturing produced an invalid healed shape."
            )
        if not _shape_is_exact(
            shape,
            source.output_shape,
            source.output_fingerprint,
        ):
            raise NativeModelError(
                "Part Defeaturing did not preserve its exact preflight shape."
            )

    PartGui.publishDesignDefinitionBlock(tuple(results))
    root = results[-1]
    replaced = []
    hidden_presentations = []
    for source in prepared.sources:
        if not source.presentation_was_visible:
            continue
        if source.element.target not in replaced:
            replaced.append(source.element.target)
        if source.presentation not in hidden_presentations:
            hidden_presentations.append(source.presentation)
    if replaced:
        if not PartGui.setModelingReplacedInputs(root, tuple(replaced)):
            raise NativeModelError(
                "Part Defeaturing could not retain its exact replaced source states."
            )
        for presentation in hidden_presentations:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "labels": labels,
            "prepared": prepared,
            "results": tuple(results),
            "replaced": tuple(replaced),
            "hidden_presentations": tuple(hidden_presentations),
        },
        recompute_targets=tuple(results),
        created=tuple(object_identity(result) for result in results),
        replaced=tuple(object_identity(item) for item in replaced),
    )


def verify_part_defeature(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedPartDefeature = draft.value["prepared"]
    results = tuple(draft.value["results"])
    root = results[-1]
    for index, (source, result) in enumerate(
        zip(prepared.sources, results, strict=True),
        start=1,
    ):
        shape = result.Shape
        expected_role = "operation" if result is root else "resource"
        owner = getattr(result, "VibeCADTimelineOwner", None)
        if (
            document.getObject(result.Name) is not result
            or result.TypeId != "Part::Feature"
            or str(result.Label) != draft.value["labels"][index - 1]
            or result.getParentGeoFeatureGroup() is not None
            or not result.isValid()
            or shape.isNull()
            or not shape.isValid()
            or not _shape_is_exact(
                shape,
                source.output_shape,
                source.output_fingerprint,
            )
        ):
            raise NativeModelError(
                f"Part Defeaturing result {index} failed its exact shape postcondition."
            )
        if (
            str(getattr(result, "VibeCADTimelineRole", "") or "") != expected_role
            or (result is root and owner is not None)
            or (result is not root and owner is not root)
        ):
            raise NativeModelError(
                f"Part Defeaturing result {index} has invalid timeline ownership."
            )
        if not current_part_element_is_exact(document, source.element):
            raise NativeModelError(
                f"Part Defeaturing source {index} changed before commit."
            )
        if _visible(source.presentation):
            raise NativeModelError(
                f"Part Defeaturing source presentation {index} was not replaced."
            )

    if (
        not str(getattr(root, "VibeCADDefinitionId", "") or "")
        or not str(getattr(root, "DesignId", "") or "")
        or tuple(getattr(root, "VibeCADTimelineReplacedInputs", ()) or ())
        != draft.value["replaced"]
    ):
        raise NativeModelError(
            "The Part Defeaturing root lost its Design identity or replaced inputs."
        )

    shapes = tuple(result.Shape for result in results)
    return {
        "root": object_reference(root),
        "source_count": len(prepared.sources),
        "result_count": len(results),
        "resource_count": len(results) - 1,
        "removed_face_count": sum(len(source.spec.faces) for source in prepared.sources),
        "shape_types": list(
            dict.fromkeys(str(shape.ShapeType) for shape in shapes)
        ),
        "total_face_count": sum(len(shape.Faces) for shape in shapes),
        "total_area_mm2": sum(float(shape.Area) for shape in shapes),
        "total_volume_mm3": sum(float(shape.Volume) for shape in shapes),
    }
