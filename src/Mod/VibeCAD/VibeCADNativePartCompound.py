# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained standalone Part Compound implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"sources"})
_MAX_SOURCES = 64


@dataclass(frozen=True, slots=True)
class PartCompoundSpec:
    source_refs: tuple[NativeObjectRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedPartCompound:
    spec: PartCompoundSpec
    sources: tuple[CurrentPartElement, ...]
    presentations: tuple[tuple[Any, bool], ...]


def _source_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError("A Part Compound source target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def prepare_part_compound(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartCompoundSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError(
            "A Part Compound definition must contain its exact sources."
        )
    values = value["sources"]
    if not isinstance(values, list) or not 1 <= len(values) <= _MAX_SOURCES:
        raise NativeModelError("Part Compound requires 1 to 64 exact source objects.")
    refs = tuple(_source_ref(document_uid, item) for item in values)
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Compound source objects must be distinct.")
    return PartCompoundSpec(refs)


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_compound(
    document: Any,
    spec: PartCompoundSpec,
) -> PreparedPartCompound:
    import PartGui

    if not isinstance(spec, PartCompoundSpec):
        raise TypeError("spec must be a PartCompoundSpec")
    sources = tuple(
        resolve_current_part_element(
            document,
            reference,
            subelement=None,
            operation="Part Compound source",
        )
        for reference in spec.source_refs
    )
    targets = tuple(source.target for source in sources)
    if len(targets) != len(set(targets)):
        raise NativeModelError(
            "Part Compound sources resolve to duplicate current shapes."
        )
    presentations = []
    for source in sources:
        presentation = PartGui.resolveModelingPresentationObject(source.target)
        if presentation is None:
            presentation = source.target
        if all(existing[0] is not presentation for existing in presentations):
            presentations.append((presentation, _visible(presentation)))
    return PreparedPartCompound(spec, sources, tuple(presentations))


def _sources_are_exact(document: Any, prepared: PreparedPartCompound) -> bool:
    return all(
        current_part_element_is_exact(document, source)
        for source in prepared.sources
    )


def create_part_compound(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartCompound,
) -> NativeMutationDraft:
    import PartGui

    if not _sources_are_exact(document, prepared):
        raise NativeModelError("A Part Compound source changed after preflight.")
    result = document.addObject("Part::Compound", "Compound")
    if result is None or result.TypeId != "Part::Compound":
        raise NativeModelError("The Part Compound factory returned the wrong type.")
    result.Label = label
    result.Links = tuple(source.target for source in prepared.sources)

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Compound"
    ):
        raise NativeModelError(
            str(result.getStatusString() or "Part Compound did not produce valid geometry.")
        )

    PartGui.publishDesignDefinitionBlock((result,))
    replaced = tuple(obj for obj, was_visible in prepared.presentations if was_visible)
    if replaced:
        if not PartGui.setModelingReplacedInputs(result, replaced):
            raise NativeModelError("Part Compound could not retain its replaced inputs.")
        for presentation in replaced:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "result": result,
            "replaced": replaced,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_compound(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    sources = tuple(source.target for source in prepared.sources)
    if document.getObject(result.Name) is not result or result.TypeId != "Part::Compound":
        raise NativeModelError("The Part Compound result lost its identity.")
    if str(result.Label) != draft.value["label"] or tuple(result.Links) != sources:
        raise NativeModelError("The Part Compound result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Compound"
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part Compound result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != draft.value["replaced"]
    ):
        raise NativeModelError("The Part Compound Design identity is invalid.")
    if not _sources_are_exact(document, prepared):
        raise NativeModelError("A Part Compound source changed before commit.")
    for presentation, _was_visible in prepared.presentations:
        if _visible(presentation):
            raise NativeModelError("Part Compound changed an input presentation incorrectly.")

    return {
        "root": object_reference(result),
        "source_count": len(sources),
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
